import os
from pathlib import Path

_backend_root = Path(__file__).resolve().parent.parent.parent
_data_dir = _backend_root / "data"
_data_dir.mkdir(parents=True, exist_ok=True)

_default_sqlite = _data_dir / "nexus_sales.db"

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{_default_sqlite.as_posix()}",
)
