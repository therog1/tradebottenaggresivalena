import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


import pytest


@pytest.fixture(autouse=True)
def _no_cross_bot_network(monkeypatch):
    """Hermetiska tester: ingen hämtning av den riktiga status-feeden."""
    import crossbot
    monkeypatch.setattr(crossbot, "load_peer", lambda peer, timeout=5.0: [])
