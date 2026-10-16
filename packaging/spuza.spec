# PyInstaller spec: windowed onedir bundle "Spuza.app" (macOS).
# Build from the repo root:  uv run --group build pyinstaller packaging/spuza.spec --noconfirm
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

HERE = Path(SPECPATH)  # noqa: F821  (injected by PyInstaller)
VERSION = tomllib.loads((HERE.parent / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]

datas, binaries, hiddenimports = [], [], []
for pkg in ("rawpy", "pillow_heif"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Spuza only uses QtCore/QtGui/QtWidgets; keep the rest of PySide6 out.
UNUSED_QT = [
    "QtNetwork", "QtQml", "QtQuick", "QtQuickWidgets", "QtQuickControls2", "QtWebEngineCore",
    "QtWebEngineWidgets", "QtWebChannel", "QtMultimedia", "QtMultimediaWidgets", "QtPdf",
    "QtPdfWidgets", "QtSql", "QtTest", "QtSvg", "QtSvgWidgets", "QtOpenGL", "QtOpenGLWidgets",
    "QtDesigner", "QtHelp", "QtCharts", "QtDataVisualization", "Qt3DCore", "Qt3DRender",
    "QtBluetooth", "QtNfc", "QtPositioning", "QtSensors", "QtSerialPort", "QtRemoteObjects",
    "QtScxml", "QtStateMachine", "QtTextToSpeech", "QtWebSockets", "QtXml", "QtConcurrent",
    "QtDBus", "QtPrintSupport", "QtUiTools", "QtSpatialAudio", "QtGraphs",
]
excludes = [f"PySide6.{m}" for m in UNUSED_QT] + ["tkinter"]

a = Analysis(  # noqa: F821
    [str(HERE / "spuza_app.py")],
    pathex=[str(HERE.parent / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"],
    excludes=excludes,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Spuza",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Spuza")  # noqa: F821
app = BUNDLE(  # noqa: F821
    coll,
    name="Spuza.app",
    bundle_identifier="com.fungimasters.spuza",
    info_plist={
        "CFBundleName": "Spuza",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
    },
)
