"""Activa auto-respuesta Gmail y poll rápido (sin tocar secretos)."""
from pathlib import Path

ENV = Path(__file__).resolve().parents[1] / ".env"

KEYS = {
    "NEXUS_INBOUND_AUTO_REPLY": "1",
    "NEXUS_AUTO_SEND_ENABLED": "1",
    "NEXUS_GMAIL_POLL_INTERVAL_SEC": "45",
    "NEXUS_INBOUND_REPLY_POLL_INTERVAL_SEC": "45",
}


def upsert(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    out = []
    found = False
    for line in lines:
        if line.startswith(f"{key}=") or line.startswith(f"#{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def main() -> None:
    raw = ENV.read_text(encoding="utf-8") if ENV.is_file() else ""
    for k, v in KEYS.items():
        raw = upsert(raw, k, v)
    ENV.write_text(raw, encoding="utf-8")
    print("updated", ENV)
    for k, v in KEYS.items():
        print(f"  {k}={v}")


if __name__ == "__main__":
    main()
