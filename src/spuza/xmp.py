"""The pill store: XMP written into the destination copy only, never the card.

JPEG and PNG carry the packet embedded (a hand-rolled, byte-lossless segment insert/rewrite: only the one XMP
APP1 segment / iTXt chunk differs from the source, the pixel data is never decoded or re-encoded). Everything
else, RAW included, gets an Adobe-named `<stem>.xmp` sidecar beside the copy. Existing XMP is merged into and
never overwritten: foreign namespaces, `dc:subject` keywords and a foreign `xmp:Label` survive.

Fields Spuza owns: the private `spuza:pills` Seq (name + hex), the pill names in `dc:subject`, `xmp:Label`
(only while it is empty or still the value Spuza wrote, recorded in `spuza:label`), and `xmp:Rating` but only when
a caller passes a `rating`: 1..5 is written as integer text, 0 removes an existing 1..5, None leaves it alone, and
a foreign -1 (reject) is never removed, only replaced by 1..5. Reading a rating is tolerant (`parse_rating`).

Stdlib only; Pillow is imported inside the verify step alone. No Qt.
"""

from __future__ import annotations

import io
import math
import os
import re
import secrets
import struct
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "EMBED_EXTS",
    "Outcome",
    "PillTag",
    "apply_embedded",
    "build_packet",
    "embed",
    "parse_pills",
    "parse_rating",
    "read_embedded",
    "read_picture_meta",
    "read_picture_pills",
    "read_sidecar",
    "write_sidecar",
]

EMBED_EXTS = frozenset({".jpg", ".jpeg", ".png"})

_STD = b"http://ns.adobe.com/xap/1.0/\x00"
_EXT = b"http://ns.adobe.com/xmp/extension/\x00"
_MAX_PAYLOAD = 65533  # one APP1 segment: 2-byte length field counts itself
_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_PNG_KEY = b"XML:com.adobe.xmp\x00"
_MAX_TEXT = 16 * 1024 * 1024  # largest iTXt payload / inflated text / sidecar we will read
_MAX_STEPS = 100_000  # bound on any segment/chunk walk (each step advances >= 4 bytes)
_PAD = 256

NS_X = "adobe:ns:meta/"
NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
NS_DC = "http://purl.org/dc/elements/1.1/"
NS_XMP = "http://ns.adobe.com/xap/1.0/"
NS_SPUZA = "http://spuza.app/ns/1.0/"

_PREFIXES = {
    NS_X: "x", NS_RDF: "rdf", NS_DC: "dc", NS_XMP: "xmp", NS_SPUZA: "spuza",
    "http://ns.adobe.com/xap/1.0/mm/": "xmpMM",
    "http://ns.adobe.com/xap/1.0/rights/": "xmpRights",
    "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#": "stEvt",
    "http://ns.adobe.com/xap/1.0/sType/ResourceRef#": "stRef",
    "http://ns.adobe.com/photoshop/1.0/": "photoshop",
    "http://ns.adobe.com/exif/1.0/": "exif",
    "http://ns.adobe.com/tiff/1.0/": "tiff",
    "http://ns.adobe.com/exif/1.0/aux/": "aux",
    "http://ns.adobe.com/camera-raw-settings/1.0/": "crs",
    "http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/": "Iptc4xmpCore",
    "http://iptc.org/std/Iptc4xmpExt/2008-02-29/": "Iptc4xmpExt",
    "http://ns.microsoft.com/photo/1.0/": "MicrosoftPhoto",
    "http://www.w3.org/XML/1998/namespace": "xml",
}
for _uri, _pfx in _PREFIXES.items():
    if _pfx != "xml":
        ET.register_namespace(_pfx, _uri)


def _q(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"


T_XMPMETA, T_RDF, T_DESC = _q(NS_X, "xmpmeta"), _q(NS_RDF, "RDF"), _q(NS_RDF, "Description")
T_LI, T_BAG, T_SEQ = _q(NS_RDF, "li"), _q(NS_RDF, "Bag"), _q(NS_RDF, "Seq")
T_ABOUT, T_PARSE = _q(NS_RDF, "about"), _q(NS_RDF, "parseType")
T_SUBJECT, T_LABEL, T_RATING = _q(NS_DC, "subject"), _q(NS_XMP, "Label"), _q(NS_XMP, "Rating")
T_PILLS, T_NAME, T_HEX, T_SLABEL = (_q(NS_SPUZA, n) for n in ("pills", "name", "hex", "label"))

# Private on purpose: the pills model module keeps its own table.
_SWATCHES = (
    ("None", "#d9d9d9"), ("Gray", "#8e8e96"), ("Green", "#8fd14f"), ("Purple", "#b266e6"),
    ("Blue", "#2fb4e8"), ("Yellow", "#f2d12e"), ("Red", "#ff4d4d"), ("Orange", "#ff8a1f"),
)
_HEX_RE = re.compile(r"#[0-9a-f]{6}")


@dataclass(frozen=True)
class PillTag:
    name: str
    hex: str  # "#rrggbb", lowercase


@dataclass(frozen=True)
class Outcome:
    status: str  # "written" | "declined"
    reason: str = ""


class _Decline(Exception):
    """Internal: a reason to leave the file alone."""


def _declined(reason: str) -> Outcome:
    return Outcome("declined", reason)


# ------------------------------------------------------------------ packet ------------------------------------------


def _rgb(hexv: str) -> tuple[int, int, int]:
    return int(hexv[1:3], 16), int(hexv[3:5], 16), int(hexv[5:7], 16)


def _colour_name(hexv: str) -> str:
    c = _rgb(hexv)
    return min(_SWATCHES, key=lambda s: math.dist(c, _rgb(s[1])))[0]


def _norm(pills) -> list[PillTag]:
    out = []
    for p in pills:
        if not isinstance(p, PillTag) or not isinstance(p.name, str) or not p.name.strip():
            raise ValueError("pill needs a non-empty name")
        h = p.hex.lower() if isinstance(p.hex, str) else ""
        if not _HEX_RE.fullmatch(h):
            raise ValueError(f"bad pill colour {p.hex!r}")
        out.append(PillTag(p.name, h))
    return out


def _parse(raw) -> ET.Element:
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("packet must be bytes")
    raw = bytes(raw).strip(b"\x00 \t\r\n")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or not raw:
        raise ValueError("empty or UTF-16 packet")
    low = raw[:4096].lower()
    if b"<!doctype" in low or b"<!entity" in raw.lower():
        raise ValueError("DTD/entity declarations are not accepted")
    try:
        return ET.fromstring(raw)
    except (ET.ParseError, RecursionError, ValueError, MemoryError) as exc:
        raise ValueError(f"XMP does not parse: {exc}") from None


def _descriptions(root: ET.Element, create: bool) -> list[ET.Element]:
    rdf = root if root.tag == T_RDF else next(root.iter(T_RDF), None)
    if rdf is None:
        raise ValueError("no rdf:RDF in the packet")
    descs = rdf.findall(T_DESC)
    if not descs and create:
        d = ET.SubElement(rdf, T_DESC)
        d.set(T_ABOUT, "")
        descs = [d]
    return descs


def _value(descs, tag: str) -> str | None:
    for d in descs:
        if tag in d.attrib:
            return d.attrib[tag]
        e = d.find(tag)
        if e is not None:
            return (e.text or "").strip()
    return None


def _set_value(descs, tag: str, value: str | None) -> None:
    for d in descs:
        if tag in d.attrib:
            if value is None:
                del d.attrib[tag]
            else:
                d.attrib[tag] = value
            return
        e = d.find(tag)
        if e is not None:
            if value is None:
                d.remove(e)
            else:
                for child in list(e):
                    e.remove(child)
                e.text = value
            return
    if value is not None:
        ET.SubElement(descs[0], tag).text = value


def _bag_items(el: ET.Element | None) -> list[ET.Element]:
    if el is None:
        return []
    out: list[ET.Element] = []
    for container in el:
        if container.tag in (T_BAG, T_SEQ):
            out.extend(i for i in container if i.tag == T_LI)
    return out


def _pills_of(descs) -> list[PillTag] | None:
    found = None
    for d in descs:
        el = d.find(T_PILLS)
        if el is not None:
            found = el
            break
    if found is None:
        return None
    out = []
    for li in _bag_items(found):
        name = li.get(T_NAME)
        hexv = li.get(T_HEX)
        if name is None:
            name = li.findtext(T_NAME)
        if hexv is None:
            hexv = li.findtext(T_HEX)
        hexv = (hexv or "").strip().lower()
        if name and name.strip() and _HEX_RE.fullmatch(hexv):
            out.append(PillTag(name.strip(), hexv))
    return out


def _subject_element(descs) -> tuple[ET.Element | None, ET.Element | None]:
    for d in descs:
        el = d.find(T_SUBJECT)
        if el is not None:
            return d, el
    return None, None


def _subject_names(descs) -> list[str]:
    return [(li.text or "").strip() for li in _bag_items(_subject_element(descs)[1])]


def _rating_arg(rating) -> int | None:
    """None (leave alone) or an int 0..5; anything else is a caller bug."""
    if rating is None:
        return None
    if isinstance(rating, bool) or not isinstance(rating, int) or not 0 <= rating <= 5:
        raise ValueError(f"rating must be None or 0..5, not {rating!r}")
    return rating


def _rating_num(text) -> int | None:
    """The integer an xmp:Rating text means: -1..5, else None (junk, NaN, infinite, out of range). "3.0" -> 3."""
    try:
        v = float(str(text).strip())
    except (ValueError, TypeError):
        return None
    if not math.isfinite(v):
        return None
    n = int(round(v))
    return n if -1 <= n <= 5 else None


def _rating_raw(descs) -> int | None:
    """The existing xmp:Rating as -1..5 (None when absent or unreadable)."""
    text = _value(descs, T_RATING)
    return None if text is None else _rating_num(text)


def _stars(n: int | None) -> int | None:
    return n if n is not None and 1 <= n <= 5 else None


def _edit_rating(descs, rating: int | None) -> None:
    if rating is None:
        return
    if rating == 0:
        if _stars(_rating_raw(descs)) is not None:
            _set_value(descs, T_RATING, None)
        return
    _set_value(descs, T_RATING, str(rating))


def _build(pills, existing: bytes | None, pad: int, rating: int | None = None) -> bytes:
    pills = _norm(pills)
    rating = _rating_arg(rating)
    try:
        if existing is None:
            root = ET.Element(T_XMPMETA)
            ET.SubElement(root, T_RDF)
        else:
            root = _parse(existing)
        descs = _descriptions(root, create=True)
        old = _pills_of(descs)
        old_names = {p.name for p in old or []}
        new_names = {p.name for p in pills}
        _edit_pills(descs, pills)
        _edit_subject(descs, old_names, new_names, [p.name for p in pills])
        _edit_label(descs, pills)
        _edit_rating(descs, rating)
        body = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    except RecursionError:
        raise ValueError("XMP is nested too deeply") from None
    head = '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'.encode()
    return head + body + b"\n" + b" " * pad + b'\n<?xpacket end="w"?>'


def _edit_pills(descs, pills: list[PillTag]) -> None:
    for d in descs:
        for el in d.findall(T_PILLS):
            d.remove(el)
    if not pills:
        return
    el = ET.SubElement(descs[0], T_PILLS)
    seq = ET.SubElement(el, T_SEQ)
    for p in pills:
        li = ET.SubElement(seq, T_LI)
        li.set(T_PARSE, "Resource")
        ET.SubElement(li, T_NAME).text = p.name
        ET.SubElement(li, T_HEX).text = p.hex


def _edit_subject(descs, old_names: set[str], new_names: set[str], ordered: list[str]) -> None:
    drop = old_names - new_names
    owner, el = _subject_element(descs)
    if el is None:
        if not ordered:
            return
        owner = descs[0]
        el = ET.SubElement(owner, T_SUBJECT)
    containers = [c for c in el if c.tag in (T_BAG, T_SEQ)]
    bag = containers[0] if containers else ET.SubElement(el, T_BAG)
    for li in list(bag):
        if li.tag == T_LI and (li.text or "").strip() in drop:
            bag.remove(li)
    have = {(li.text or "").strip() for li in bag if li.tag == T_LI}
    for name in ordered:
        if name not in have:
            ET.SubElement(bag, T_LI).text = name
            have.add(name)
    if not any(c.tag == T_LI for c in bag):
        owner.remove(el)


def _edit_label(descs, pills: list[PillTag]) -> None:
    current = _value(descs, T_LABEL)
    mine = _value(descs, T_SLABEL)
    ours = current is None or current == "" or (mine is not None and current == mine)
    if pills:
        if ours:
            want = _colour_name(pills[0].hex)
            _set_value(descs, T_LABEL, want)
            _set_value(descs, T_SLABEL, want)
        else:
            _set_value(descs, T_SLABEL, None)
    else:
        if mine is not None and current == mine:
            _set_value(descs, T_LABEL, None)
        _set_value(descs, T_SLABEL, None)


def _unchanged(existing: bytes, pills, rating: int | None = None) -> bool:
    """True when `existing` already holds exactly this state for everything Spuza owns, so no rewrite is due.

    Raises ValueError when `existing` does not parse."""
    want = _norm(pills)
    rating = _rating_arg(rating)
    try:
        descs = _descriptions(_parse(existing), create=False)
        if not descs:
            return False
        if rating is not None and (_stars(_rating_raw(descs)) or 0) != rating:
            return False
        old = _pills_of(descs)
        current, mine = _value(descs, T_LABEL), _value(descs, T_SLABEL)
        if not want:
            return old is None and mine is None
        if old != want or not {p.name for p in want} <= set(_subject_names(descs)):
            return False
        if current is None or current == "" or current == mine:
            return current == mine == _colour_name(want[0].hex)
        return mine is None
    except RecursionError:
        raise ValueError("XMP is nested too deeply") from None


def build_packet(pills, existing: bytes | None = None, rating: int | None = None) -> bytes:
    """The full `<?xpacket ...?>` packet for `pills`, merged into `existing` (never replacing it).

    `rating`: None leaves any xmp:Rating alone; 1..5 writes it; 0 removes an existing 1..5 (a -1 reject stays).
    Raises ValueError when `existing` does not parse, a pill is malformed or the rating is not None/0..5."""
    return _build(pills, existing, _PAD, rating)


def parse_pills(packet: bytes) -> list[PillTag] | None:
    """The pills in a packet, or None when it has no spuza:pills bag (or does not parse)."""
    try:
        return _pills_of(_descriptions(_parse(packet), create=False))
    except (ValueError, RecursionError):
        return None


def parse_rating(packet) -> int | None:
    """The star rating (1..5) in a packet, read tolerantly (attribute or element, "3.0"); -1, 0, junk, a packet that
    does not parse -> None."""
    try:
        return _stars(_rating_raw(_descriptions(_parse(packet), create=False)))
    except (ValueError, RecursionError):
        return None


# ------------------------------------------------------------------ JPEG --------------------------------------------


def _jpeg_scan(data: bytes) -> tuple[tuple[int, int] | None, int]:
    """Walk EVERY segment from SOI to SOS. Returns (span of the one standard XMP APP1 or None, insert offset).

    Raises _Decline on anything odd: bad marker or length, no SOS, extended XMP, a second standard packet.
    The walk advances at least 4 bytes per step, so it takes at most len(data)/4 steps."""
    n = len(data)
    if data[:2] != b"\xff\xd8":
        raise _Decline("not a JPEG")
    i, insert_at, leading = 2, 2, True
    found: list[tuple[int, int]] = []
    while True:
        if i + 4 > n:
            raise _Decline("no SOS marker (truncated JPEG)")
        if data[i] != 0xFF:
            raise _Decline(f"bad marker at byte {i}")
        m = data[i + 1]
        if m == 0xDA:
            break
        if m in (0x00, 0x01, 0xFF, 0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
            raise _Decline(f"unexpected marker 0x{m:02x} before SOS")
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        end = i + 2 + length
        if length < 2 or end > n:
            raise _Decline(f"malformed segment length at byte {i}")
        if m == 0xE1:
            head = data[i + 4 : i + 4 + len(_EXT)]
            if head.startswith(_EXT):
                raise _Decline("extended XMP present")
            if head.startswith(_STD):
                found.append((i, end))
        if leading and m in (0xE0, 0xE1):
            insert_at = end
        else:
            leading = False
        i = end
    if len(found) > 1:
        raise _Decline("more than one standard XMP packet")
    return (found[0] if found else None), insert_at


def _embed_jpeg(data: bytes, pills, rating: int | None = None) -> bytes:
    span, insert_at = _jpeg_scan(data)
    existing = data[span[0] + 4 + len(_STD) : span[1]] if span else None
    if span is None and not pills and not _rating_arg(rating):
        return data
    if existing is not None and _unchanged(existing, pills, rating):
        return data
    for pad in (_PAD, 0):
        packet = _build(pills, existing, pad, rating)
        if len(_STD) + len(packet) <= _MAX_PAYLOAD:
            break
    else:
        raise _Decline(f"packet exceeds one {_MAX_PAYLOAD}-byte APP1 payload")
    body = _STD + packet
    segment = b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body
    if span:
        return data[: span[0]] + segment + data[span[1] :]
    return data[:insert_at] + segment + data[insert_at:]


# ------------------------------------------------------------------ PNG ---------------------------------------------


def _itxt_text(body: bytes) -> bytes | None:
    """The XMP text of an iTXt body, or None when it is not the XMP chunk."""
    if not body.startswith(_PNG_KEY) or len(body) < len(_PNG_KEY) + 2:
        return None
    k = len(_PNG_KEY)
    flag = body[k]
    rest = body[k + 2 :]
    a = rest.index(b"\x00")  # language tag
    b = rest.index(b"\x00", a + 1)  # translated keyword
    text = rest[b + 1 :]
    if flag == 1:
        d = zlib.decompressobj()
        text = d.decompress(text, _MAX_TEXT)
        if d.unconsumed_tail:
            raise ValueError("compressed XMP too large")
    return text


def _png_scan(data: bytes) -> tuple[tuple[int, int] | None, int]:
    n = len(data)
    if data[:8] != _PNG_SIG:
        raise _Decline("not a PNG")
    i, insert_at, first = 8, 8, True
    found: list[tuple[int, int]] = []
    for _ in range(_MAX_STEPS):
        if i + 12 > n:
            raise _Decline("no IDAT chunk (truncated PNG)")
        length = struct.unpack(">I", data[i : i + 4])[0]
        typ = data[i + 4 : i + 8]
        end = i + 12 + length
        if length > 0x7FFFFFFF or end > n:
            raise _Decline(f"malformed chunk length at byte {i}")
        if first and typ != b"IHDR":
            raise _Decline("first chunk is not IHDR")
        if typ == b"IDAT":
            break
        if typ == b"IEND":
            raise _Decline("IEND before IDAT")
        if typ == b"IHDR":
            insert_at = end
        if typ == b"iTXt" and data[i + 8 : i + 8 + len(_PNG_KEY)] == _PNG_KEY:
            found.append((i, end))
        first = False
        i = end
    else:
        raise _Decline("too many chunks before IDAT")
    if len(found) > 1:
        raise _Decline("more than one XMP iTXt chunk")
    return (found[0] if found else None), insert_at


def _embed_png(data: bytes, pills, rating: int | None = None) -> bytes:
    span, insert_at = _png_scan(data)
    existing = None
    if span:
        existing = _itxt_text(data[span[0] + 8 : span[1] - 4])
        if existing is None:
            raise _Decline("XMP chunk unreadable")
        if _unchanged(existing, pills, rating):
            return data
    elif not pills and not _rating_arg(rating):
        return data
    payload = _PNG_KEY + b"\x00\x00\x00\x00" + _build(pills, existing, _PAD, rating)
    chunk = b"iTXt" + payload
    out = struct.pack(">I", len(payload)) + chunk + struct.pack(">I", zlib.crc32(chunk))
    if span:
        return data[: span[0]] + out + data[span[1] :]
    return data[:insert_at] + out + data[insert_at:]


# ------------------------------------------------------------------ embed / apply -----------------------------------


def embed(data: bytes, ext: str, pills, rating: int | None = None) -> tuple[bytes | None, str]:
    """Pure. (new bytes, "") with the pills (and `rating`, see build_packet) merged into the file's XMP, or
    (None, reason) to fall back."""
    try:
        ext = ext.lower()
        if ext in (".jpg", ".jpeg"):
            return _embed_jpeg(data, pills, rating), ""
        if ext == ".png":
            return _embed_png(data, pills, rating), ""
        return None, f"{ext or 'this type'} does not embed XMP"
    except _Decline as exc:
        return None, str(exc)
    except (ValueError, RecursionError, struct.error, zlib.error) as exc:
        return None, f"cannot merge: {exc}"


def _span(data: bytes, ext: str):
    return (_png_scan if ext == ".png" else _jpeg_scan)(data)[0]


def _without(data: bytes, span) -> bytes:
    return data if span is None else data[: span[0]] + data[span[1] :]


def _verify(old: bytes, new: bytes, ext: str) -> str:
    """"" when `new` differs from `old` only inside the XMP segment/chunk and decodes to the same pixels."""
    try:
        so, sn = _span(old, ext), _span(new, ext)
    except _Decline as exc:
        return f"verify: {exc}"
    if sn is None:
        return "verify: no XMP segment in the written file"
    if _without(old, so) != _without(new, sn):
        return "verify: bytes outside the XMP segment changed"
    from PIL import Image

    with Image.open(io.BytesIO(old)) as a, Image.open(io.BytesIO(new)) as b:
        if a.size != b.size or a.mode != b.mode or a.tobytes() != b.tobytes():
            return "verify: decoded pixels differ"
    return ""


def apply_embedded(tmp: Path, pills, rating: int | None = None) -> Outcome:
    """Merge `pills` (and `rating`, see build_packet) into our temp copy `tmp` in place, verified; restores the pre-write bytes on any doubt."""
    part = None
    old = None
    replaced = False
    try:
        tmp = Path(tmp)
        ext = tmp.suffix.lower()
        if ext not in EMBED_EXTS:
            return _declined(f"{ext or 'this type'} does not embed XMP")
        old = tmp.read_bytes()
        new, reason = embed(old, ext, pills, rating)
        if new is None:
            return _declined(reason)
        if new == old:
            return Outcome("written", "nothing to change")
        part = tmp.with_name("." + tmp.name + ".spuza-part")
        part.write_bytes(new)
        os.replace(part, tmp)
        replaced = True
        back = tmp.read_bytes()
        problem = "verify: re-read differs from what was written" if back != new else _verify(old, back, ext)
        if problem:
            raise _Decline(problem)
        return Outcome("written")
    except Exception as exc:  # never raises for I/O or format problems
        reason = str(exc) or type(exc).__name__
        if replaced and old is not None:
            try:
                part = tmp.with_name("." + tmp.name + ".spuza-part")
                part.write_bytes(old)
                os.replace(part, tmp)
            except Exception as exc2:
                try:
                    tmp.write_bytes(old)
                except Exception:
                    reason += f" (restore failed: {exc2})"
        return _declined(reason)
    finally:
        if part is not None:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass


# ------------------------------------------------------------------ sidecar -----------------------------------------


def _sigs(root: ET.Element, rated: bool = False) -> set:
    """Signatures of everything foreign. `rated`: Spuza wrote an xmp:Rating, so the old one is not foreign."""
    skip: set = set()
    own = (T_RATING,) if rated else ()
    for tag in (T_PILLS, T_SUBJECT, T_SLABEL, T_LABEL, *own):
        for e in root.iter(tag):
            skip.update(e.iter())
    out = set()
    for e in root.iter():
        if e in skip:
            continue
        attrs = tuple(sorted((k, v) for k, v in e.attrib.items() if k not in (T_LABEL, T_SLABEL, *own)))
        out.add((e.tag, attrs, (e.text or "").strip()))
    return out


def _preserved(old: bytes, new: bytes, rating: int | None = None) -> str:
    """"" when everything that is not Spuza's in `old` is still in `new`."""
    ro, rn = _parse(old), _parse(new)
    do, dn = _descriptions(ro, create=False), _descriptions(rn, create=False)
    rated = rating is not None
    if not _sigs(ro, rated) <= _sigs(rn, rated):
        return "foreign XMP content went missing"
    old_pill = {p.name for p in _pills_of(do) or []}
    new_pill = {p.name for p in _pills_of(dn) or []}
    keep = set(_subject_names(do)) - (old_pill - new_pill)
    if not keep <= set(_subject_names(dn)):
        return "foreign dc:subject keywords went missing"
    label, mine = _value(do, T_LABEL), _value(do, T_SLABEL)
    if label and label != mine and _value(dn, T_LABEL) != label:
        return "foreign xmp:Label changed"
    if rating == 0 and _rating_raw(do) == -1 and _rating_raw(dn) != -1:
        return "foreign xmp:Rating (reject) changed"
    return ""


def _check_written(blob: bytes, pills: list[PillTag], old: bytes | None, rating: int | None = None) -> str:
    got = parse_pills(blob)
    if (got or None) != (pills or None) or (pills and got != pills):
        return "verify: pills read back differ"
    if rating is not None and (parse_rating(blob) or 0) != rating:
        return "verify: rating read back differs"
    if old is not None:
        try:
            return _preserved(old, blob, rating)
        except (ValueError, RecursionError) as exc:
            return f"verify: {exc}"
    return ""


def write_sidecar(path: Path, pills, rating: int | None = None) -> Outcome:
    """Create or merge into the `<stem>.xmp` at `path` (`rating`: see build_packet). Never overwrites foreign
    content; never raises."""
    part = None
    try:
        path = Path(path)
        part = path.with_name(f".{path.name}.{os.getpid()}-{secrets.token_hex(4)}.spuza-part")
        want = _norm(pills)
        rating = _rating_arg(rating)
        if not path.exists():
            if not want and not rating:
                return Outcome("written", "nothing to change")
            packet = build_packet(want, None, rating)
            problem = _check_written(packet, want, None, rating)
            if problem:
                return _declined(problem)
            part.write_bytes(packet)
            try:
                os.link(part, path)
                return Outcome("written")
            except FileExistsError:
                pass
            except OSError:  # no hard links here (exFAT/FAT): create exclusively instead
                try:
                    f = open(path, "xb")
                except FileExistsError:
                    f = None
                if f is not None:  # we created it: a failed write must not leave a truncated sidecar
                    try:
                        with f:
                            f.write(packet)
                    except BaseException:
                        try:
                            path.unlink()
                        except OSError:
                            pass
                        raise
                    return Outcome("written")
        return _merge_sidecar(path, part, want, rating)
    except Exception as exc:
        return _declined(str(exc) or type(exc).__name__)
    finally:
        if part is not None:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass


def _merge_sidecar(path: Path, part: Path, want: list[PillTag], rating: int | None = None) -> Outcome:
    st0 = path.stat()
    old = read_sidecar(path)
    if old is None:
        return _declined("existing sidecar unreadable")
    try:
        if _unchanged(old, want, rating):
            return Outcome("written", "nothing to change")
        new = build_packet(want, old, rating)
    except ValueError as exc:
        return _declined(f"existing sidecar is not usable XMP: {exc}")
    part.write_bytes(new)
    problem = _check_written(part.read_bytes(), want, old, rating)
    if problem:
        return _declined(problem)
    st1 = path.stat()
    if (st1.st_size, st1.st_mtime_ns) != (st0.st_size, st0.st_mtime_ns):
        return _declined("sidecar changed while merging; left as it is")
    os.replace(part, path)
    return Outcome("written")


# ------------------------------------------------------------------ read-back ---------------------------------------


def _read_jpeg(f) -> bytes | None:
    f.seek(2)
    for _ in range(_MAX_STEPS):
        hdr = f.read(4)
        if len(hdr) < 4 or hdr[0] != 0xFF:
            return None
        m = hdr[1]
        if m == 0xDA or m in (0x00, 0x01, 0xFF, 0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
            return None
        n = struct.unpack(">H", hdr[2:4])[0]
        if n < 2:
            return None
        if m == 0xE1 and n - 2 >= len(_STD):
            payload = f.read(n - 2)
            if payload.startswith(_STD):
                return payload[len(_STD) :]
        else:
            f.seek(n - 2, 1)
    return None


def _read_png(f) -> bytes | None:
    f.seek(8)
    for _ in range(_MAX_STEPS):
        hdr = f.read(8)
        if len(hdr) < 8:
            return None
        n = struct.unpack(">I", hdr[:4])[0]
        typ = hdr[4:]
        if typ in (b"IDAT", b"IEND"):
            return None
        if typ == b"iTXt" and n <= _MAX_TEXT:
            text = _itxt_text(f.read(n))
            if text is not None:
                return text
            f.seek(4, 1)
        else:
            f.seek(n + 4, 1)
    return None


def read_embedded(path: Path) -> bytes | None:
    """The embedded XMP packet of a JPEG/PNG, read as a stream up to SOS / the first IDAT; else None."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
            if head[:2] == b"\xff\xd8":
                return _read_jpeg(f)
            if head == _PNG_SIG:
                return _read_png(f)
    except (OSError, ValueError, zlib.error, struct.error):
        pass
    return None


def read_sidecar(path: Path) -> bytes | None:
    try:
        with open(path, "rb") as f:
            data = f.read(_MAX_TEXT + 1)
    except OSError:
        return None
    return data if len(data) <= _MAX_TEXT else None


def _pills_from(raw: bytes | None) -> list[PillTag] | None:
    return parse_pills(raw) if raw else None


def _has_rating(raw: bytes | None) -> tuple[bool, int | None]:
    """(the packet carries an xmp:Rating at all, its star value 1..5 or None)."""
    if not raw:
        return False, None
    try:
        descs = _descriptions(_parse(raw), create=False)
        text = _value(descs, T_RATING)
    except (ValueError, RecursionError):
        return False, None
    return (text is not None), (None if text is None else _stars(_rating_num(text)))


def read_picture_meta(picture) -> tuple[list[PillTag] | None, int | None]:
    """(pills, stars) of a catalog Picture. Pills: only a store with a spuza:pills bag counts, the primary's store
    wins (RAW: its `<stem>.xmp`, then the JPEG twin's embedded; JPEG/PNG: embedded, then its `<stem>.xmp`), no union.
    Stars: the xmp:Rating (1..5) of the first store in that order that has one at all, bag or not; a -1 reject,
    0 or junk there reads as None and ends the search."""
    def sidecar():
        try:
            want = picture.primary.stem.lower() + ".xmp"
            for c in picture.companions:
                if Path(c).name.lower() == want:
                    return read_sidecar(Path(c))
        except Exception:
            pass
        return None

    def embedded(p):
        try:
            if p is not None and Path(p).suffix.lower() in EMBED_EXTS:
                return read_embedded(Path(p))
        except Exception:
            pass
        return None

    pills: list[PillTag] | None = None
    stars: int | None = None
    rated = False
    try:
        if Path(picture.primary).suffix.lower() in EMBED_EXTS:
            order = (lambda: embedded(picture.primary), sidecar)
        else:
            order = (sidecar, lambda: embedded(picture.sidecar))
        for store in order:
            raw = store()
            if pills is None:
                pills = _pills_from(raw)
            if not rated:
                rated, stars = _has_rating(raw)
            if pills is not None and rated:
                break
    except Exception:
        pass
    return pills, stars


def read_picture_pills(picture) -> list[PillTag] | None:
    """The pills of a catalog Picture (see read_picture_meta)."""
    return read_picture_meta(picture)[0]
