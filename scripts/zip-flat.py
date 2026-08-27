"""ZIP with files at archive root (Chrome Web Store rejects ./manifest.json)."""

from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main() -> int:
    if len(sys.argv) != 3:
        print("uso: zip-flat.py <carpeta> <salida.zip>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    if not (root / "manifest.json").is_file():
        print(f"no hay manifest.json en {root}", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    with ZipFile(out, "w", compression=ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            arcname = path.relative_to(root).as_posix()
            zf.write(path, arcname)
    names = ZipFile(out).namelist()
    if "manifest.json" not in names:
        print("el zip no tiene manifest.json en la raíz", file=sys.stderr)
        return 1
    print(f"zip ok: {out} ({len(names)} archivos, raíz=manifest.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
