"""gcguard: cyclic GC must run on the GUI thread, never on a pool worker."""

import gc
import threading
import weakref

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from spuza.ui import gcguard


@pytest.fixture
def qapp():
    return QApplication.instance()


class Node(QObject):
    pass


def _scenario():
    """Drop a QObject cycle, then allocate on a worker thread. Returns the threads finalizers ran on."""
    ran = []
    a = Node()
    a.me = a  # reference cycle: only the cyclic GC can free it
    weakref.finalize(a, lambda: ran.append(threading.current_thread()))
    del a

    def churn():
        keep = []  # allocations minus deallocations is what the gen-0 count measures, so hold them
        for _ in range(gc.get_threshold()[0] * 5):
            keep.append([])

    t = threading.Thread(target=churn)
    t.start()
    t.join()
    return ran


@pytest.fixture
def reset_gc():
    was = gc.isenabled()
    yield
    gc.enable() if was else gc.disable()


def test_cycle_is_not_collected_on_worker_thread(qapp, reset_gc):
    guard = gcguard.install(qapp)
    assert not gc.isenabled()
    gc.collect()
    ran = _scenario()
    assert ran == []  # the worker allocated a lot; automatic gc is off, so nothing ran there
    gc.collect()
    guard.collect()
    assert ran == [threading.main_thread()]


def test_scenario_is_red_with_automatic_gc_on(qapp, reset_gc):
    """The control: with automatic gc enabled the worker thread does the collecting."""
    gc.enable()
    gc.collect()
    ran = _scenario()
    assert ran and ran[0] is not threading.main_thread()


def test_install_is_idempotent(qapp):
    assert gcguard.install(qapp) is gcguard.install(qapp)


def test_collect_runs_oldest_exceeded_generation(qapp, monkeypatch):
    calls = []
    monkeypatch.setattr(gc, "get_count", lambda: (5, 20, 1))
    monkeypatch.setattr(gc, "get_threshold", lambda: (700, 10, 10))
    monkeypatch.setattr(gc, "collect", lambda gen=2: calls.append(gen))
    gcguard.install(qapp).collect()
    assert calls == [1]


def test_cli_app_installs_guard(qapp, monkeypatch):
    from spuza import cli

    gc.collect()
    assert cli._app() is qapp
    assert not gc.isenabled()
    assert gcguard._guard is not None
