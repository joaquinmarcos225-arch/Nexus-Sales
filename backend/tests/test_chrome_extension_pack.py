"""ZIP de extensión Chrome incluye orígenes de prod."""

import json
from io import BytesIO
from zipfile import ZipFile

from app.services.chrome_extension_pack import build_chrome_extension_zip, extension_available


def test_extension_available_in_repo():
    assert extension_available() is True


def test_zip_includes_frontend_and_api_origins(monkeypatch):
    monkeypatch.setenv("NEXUS_FRONTEND_URL", "https://nexus.costguard.com.ar")
    monkeypatch.setenv("NEXUS_API_PUBLIC_URL", "https://api-production-21aa.up.railway.app")
    raw = build_chrome_extension_zip()
    with ZipFile(BytesIO(raw)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        background = zf.read("background.js").decode("utf-8")
        content_nexus = zf.read("content-nexus.js").decode("utf-8")
    page_matches = []
    for block in manifest["content_scripts"]:
        js = block.get("js") or []
        if "content-nexus.js" in js or "page-bridge.js" in js:
            page_matches = block["matches"]
            break
    assert "https://nexus.costguard.com.ar/*" in page_matches
    assert "https://api-production-21aa.up.railway.app/*" in manifest["host_permissions"]
    assert 'const DEFAULT_API = "https://api-production-21aa.up.railway.app"' in background
    assert '"https://nexus.costguard.com.ar/*"' in background
    assert 'apiBaseUrl: "https://api-production-21aa.up.railway.app"' in content_nexus
