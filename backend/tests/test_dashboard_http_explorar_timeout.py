"""Explorar search no debe compartir el timeout corto (25s) del dashboard genérico."""

from app.middleware import dashboard_http as mw
from app.services import explorar as svc


def test_explorar_search_uses_longer_http_timeout(monkeypatch):
    monkeypatch.delenv("NEXUS_HTTP_DASHBOARD_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("NEXUS_HTTP_EXPLORAR_TIMEOUT_SEC", raising=False)
    assert mw.dashboard_http_timeout_sec() == 25.0
    assert mw.explorar_http_timeout_sec() >= 90.0
    assert mw._is_explorar_long_path("/companies/4/explorar/search")
    assert mw._is_explorar_long_path("/companies/4/explorar/potential-customers")
    assert not mw._is_explorar_long_path("/companies/4/campaigns")
    # Contrato: wall-clock de search << soft limit Explorar (y el middleware no-kill Explorar).
    assert svc.search_deadline_sec() + 5 <= mw.explorar_http_timeout_sec()
