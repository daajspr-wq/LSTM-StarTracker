"""Fixtures communes.

Par défaut, chaque test démarre un faux serveur Alpaca neuf (tests/fake_alpaca.py).
Pour tester contre un vrai serveur (OmniSim, ASCOM Remote) :

    ALPACA_TEST_SERVER=127.0.0.1:32323 pytest -q          (Linux / WSL)
    $env:ALPACA_TEST_SERVER="127.0.0.1:32323"; pytest -q  (PowerShell)

Les tests marqués `fake_only` dépendent du comportement exact du faux serveur
et sont sautés contre un vrai serveur.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_alpaca import FakeAlpacaServer  # noqa: E402
from startracker_devices import DeviceKind, Devices  # noqa: E402

REAL = os.environ.get("ALPACA_TEST_SERVER")


def pytest_configure(config):
    config.addinivalue_line("markers", "fake_only: dépend du faux serveur, sauté contre un vrai serveur")


def pytest_collection_modifyitems(config, items):
    if REAL:
        skip = pytest.mark.skip(reason="comportement propre au faux serveur")
        for item in items:
            if "fake_only" in item.keywords:
                item.add_marker(skip)


@pytest.fixture()
def server():
    if REAL:
        yield REAL
        return
    srv = FakeAlpacaServer(port=0).start()
    yield srv.address
    srv.stop()


@pytest.fixture()
def devs(server):
    with Devices(servers=[server], discover=False) as d:
        yield d


@pytest.fixture()
def mount(devs):
    m = devs.mount(devs.list_devices(DeviceKind.MOUNT)[0].id)
    yield m
    if m.connected:
        try:
            m.stop_slew()
        except Exception:
            pass
        m.disconnect()


@pytest.fixture()
def camera(devs):
    c = devs.camera(devs.list_devices(DeviceKind.CAMERA)[0].id)
    yield c
    if c.connected:
        try:
            c.abort()
        except Exception:
            pass
        c.disconnect()
