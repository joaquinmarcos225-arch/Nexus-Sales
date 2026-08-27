"""Empaqueta la extensión Chrome con orígenes de Nexus (frontend + API).

Este ZIP es para **descarga / sideload** (árbol completo, puede incluir LinkedIn).
NO es el paquete de Chrome Web Store. El Store sale de `browser-extension-store/`
vía `scripts/pack-extension.mjs` con `NEXUS_EXTENSION_STORE_BUILD=1`.
"""

from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZIP_DEFLATED, ZipFile

_SKIP_NAMES = frozenset(
    {"README.md", "TEAM_INSTALL.md", ".DS_Store", "Thumbs.db"}
)


def _extension_dir() -> Path:
    """
    Local monorepo: <repo>/browser-extension
    Docker/Railway: /app/browser-extension (junto a /app/app)

    Preferir siempre el árbol completo de sideload, nunca browser-extension-store,
    para no mezclar el ZIP de descarga manual con el de Web Store.
    """
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "browser-extension",  # backend/browser-extension
        Path("/app/browser-extension"),
        here.parents[3] / "browser-extension",  # repo root (dev)
    ]
    for path in candidates:
        if (path / "manifest.json").is_file():
            return path
    return candidates[0]


def extension_available() -> bool:
    return (_extension_dir() / "manifest.json").is_file()


def _origin_match(url: str) -> str | None:
    raw = (url or "").strip().rstrip("/")
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/*"


def build_chrome_extension_zip() -> bytes:
    ext_dir = _extension_dir()
    manifest_path = ext_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("No se encontró browser-extension/manifest.json")

    frontend = (os.getenv("NEXUS_FRONTEND_URL") or "").strip()
    api_public = (
        (os.getenv("NEXUS_API_PUBLIC_URL") or "").strip()
        or (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()
    )
    if api_public and "://" not in api_public:
        api_public = f"https://{api_public}"

    page_matches = ["http://127.0.0.1/*", "http://localhost/*"]
    for origin in (_origin_match(frontend),):
        if origin and origin not in page_matches:
            page_matches.append(origin)

    host_permissions = [
        "https://www.linkedin.com/*",
        "https://web.whatsapp.com/*",
        "http://127.0.0.1:8002/*",
        "http://localhost:8002/*",
    ]
    for origin in (_origin_match(api_public), _origin_match(frontend)):
        if origin and origin not in host_permissions:
            host_permissions.append(origin)

    runtime_api = api_public or "http://127.0.0.1:8002"
    nexus_tab_matches = ["http://127.0.0.1/*", "http://localhost/*"]
    frontend_match = _origin_match(frontend)
    if frontend_match and frontend_match not in nexus_tab_matches:
        nexus_tab_matches.append(frontend_match)

    buf = BytesIO()
    with ZipFile(buf, "w", ZIP_DEFLATED) as zf:
        for path in sorted(ext_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.name in _SKIP_NAMES or path.name.endswith(".test.mjs"):
                continue
            rel = path.relative_to(ext_dir).as_posix()
            if path.name == "manifest.json":
                manifest = json.loads(path.read_text(encoding="utf-8"))
                for block in manifest.get("content_scripts") or []:
                    js = block.get("js") or []
                    if "page-bridge.js" in js or "content-nexus.js" in js:
                        block["matches"] = list(page_matches)
                manifest["host_permissions"] = host_permissions
                zf.writestr(rel, json.dumps(manifest, indent=2) + "\n")
            elif path.name == "background.js":
                source = path.read_text(encoding="utf-8")
                source = source.replace(
                    "const DEFAULT_API = 'http://127.0.0.1:8002'",
                    f"const DEFAULT_API = {json.dumps(runtime_api)}",
                )
                source = source.replace(
                    "['http://127.0.0.1/*', 'http://localhost/*']",
                    json.dumps(nexus_tab_matches),
                )
                zf.writestr(rel, source)
            elif path.name == "content-nexus.js":
                source = path.read_text(encoding="utf-8")
                source = source.replace(
                    "apiBaseUrl: 'http://127.0.0.1:8002'",
                    f"apiBaseUrl: {json.dumps(runtime_api)}",
                )
                zf.writestr(rel, source)
            else:
                zf.write(path, rel)
    return buf.getvalue()
