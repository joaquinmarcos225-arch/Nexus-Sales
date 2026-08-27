"""Verifica flujos LinkedIn outbound (mark-sent) e inbound (registro + borrador) vía API."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime

API = "http://127.0.0.1:8002"
EMAIL = "sdr@test.com"
PASSWORD = "demo123"
MIA_ID = 10


def req(method: str, path: str, token: str | None = None, body: dict | None = None) -> tuple[int, dict]:
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as res:
            raw = res.read().decode()
            return res.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw) if raw else {"detail": exc.reason}
        except json.JSONDecodeError:
            payload = {"detail": raw or exc.reason}
        return exc.code, payload


def login() -> str:
    status, data = req("POST", "/auth/login", body={"email": EMAIL, "password": PASSWORD})
    if status != 200:
        raise SystemExit(f"Login falló ({status}): {data}")
    return data["access_token"]


def main() -> None:
    print("=== Verificación LinkedIn Nexus (API en vivo) ===\n")
    token = login()
    print("OK login sdr@test.com")

    status, prospect = req("GET", f"/prospects/{MIA_ID}/outreach-workspace", token=token)
    if status != 200:
        raise SystemExit(f"No pude leer prospecto {MIA_ID}: {prospect}")
    print(f"OK prospecto Mia id={MIA_ID} campaign={prospect.get('campaign_id')}")

    # --- Outbound: preparar borrador pendiente si hace falta ---
    draft_before = (prospect.get("linkedin_assisted_draft") or "").strip()
    marked_sent = bool(prospect.get("linkedin_sdr_marked_sent_at"))
    print(f"   borrador pendiente: {bool(draft_before)} | ya marcado enviado: {marked_sent}")

    if not draft_before and not marked_sent:
        # Simular toque LinkedIn generado (cola)
        from app.database.session import SessionLocal, init_db
        from app.models.campaign import Campaign
        from app.models.prospect import Prospect
        from app.services import linkedin_assisted_service as las

        init_db()
        db = SessionLocal()
        try:
            p = db.get(Prospect, MIA_ID)
            c = db.get(Campaign, int(p.campaign_id))
            las.mark_draft_suggested(
                db,
                p,
                c,
                "Hola Mia, te escribo desde Nexus para coordinar una breve charla sobre outbound.",
                log_event=False,
            )
            db.commit()
            print("   -> borrador de prueba creado en BD")
        finally:
            db.close()
        status, prospect = req("GET", f"/prospects/{MIA_ID}/outreach-workspace", token=token)

    # Simular extensión: mark-sent automático
    status, sent = req("POST", f"/prospects/{MIA_ID}/linkedin-assisted/mark-sent", token=token)
    outbound_ok = status == 200
    print(f"\n[OUTBOUND auto-detect simulado] mark-sent -> HTTP {status}")
    if outbound_ok:
        print(f"   OK: {sent.get('detail', sent)}")
    else:
        print(f"   FAIL: {sent}")

    status, after_out = req("GET", f"/prospects/{MIA_ID}/outreach-workspace", token=token)
    queue_cleared = not (after_out.get("linkedin_assisted_draft") or "").strip()
    print(f"   cola limpia (sin borrador): {queue_cleared}")

    # --- Inbound: simular extensión detectando respuesta de Mia ---
    inbound_msg = (
        f"Hola, sí me interesa saber más sobre Nexus. "
        f"¿Podemos hablar esta semana? [test-auto {datetime.now(UTC).isoformat()}]"
    )
    status, inbound = req(
        "POST",
        f"/prospects/{MIA_ID}/linkedin-inbound",
        token=token,
        body={"message": inbound_msg, "linkedin_message_id": f"verify-{int(datetime.now(UTC).timestamp())}"},
    )
    inbound_ok = status == 200 and inbound.get("inserted")
    print(f"\n[INBOUND detect simulado] linkedin-inbound -> HTTP {status}")
    print(f"   inserted: {inbound.get('inserted')} | reply_draft_ready: {inbound.get('reply_draft_ready')}")
    if inbound.get("reply_draft"):
        print(f"   borrador réplica: {str(inbound['reply_draft'])[:120]}…")

    status, after_in = req("GET", f"/prospects/{MIA_ID}/outreach-workspace", token=token)
    seq_paused = bool(after_in.get("sequence_paused"))
    has_reply_draft = bool((after_in.get("linkedin_assisted_draft") or "").strip())
    print(f"   secuencia pausada: {seq_paused} | borrador réplica en cola: {has_reply_draft}")

    campaign_id = after_in.get("campaign_id")
    if campaign_id:
        status, queue = req("GET", f"/campaigns/{campaign_id}/linkedin-assisted/queue", token=token)
        pending = queue.get("total_pending", "?")
        print(f"   cola campaña pending: {pending}")

    print("\n=== Resultado ===")
    checks = [
        ("Outbound mark-sent", outbound_ok),
        ("Cola outbound limpia", queue_cleared),
        ("Inbound registrado", inbound_ok),
        ("Secuencia pausada tras inbound", seq_paused),
        ("Borrador réplica generado", has_reply_draft or inbound.get("reply_draft_ready")),
    ]
    all_ok = True
    for label, ok in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}")
        all_ok = all_ok and ok

    if not all_ok:
        sys.exit(1)
    print("\nTodo OK en backend. La extensión en Chrome debe replicar estos mismos POSTs.")


if __name__ == "__main__":
    main()
