"""Smoke test: Operaciones API + sourcing refill + health automation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

import urllib.error
import urllib.request

API = "http://127.0.0.1:8002"


def req(method: str, path: str, token: str | None = None, body: dict | None = None) -> tuple[int, dict]:
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=25) as res:
            raw = res.read().decode()
            return res.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw) if raw else {"detail": exc.reason}
        except json.JSONDecodeError:
            payload = {"detail": raw or exc.reason}
        return exc.code, payload


def login(email: str) -> tuple[str, dict]:
    status, data = req("POST", "/auth/login", body={"email": email, "password": "demo123"})
    if status != 200:
        raise SystemExit(f"Login {email} falló ({status}): {data}")
    return data["access_token"], data["user"]


def main() -> None:
    print("=== Verificación Operaciones + Sourcing Refill ===\n")
    failures: list[str] = []

    # Health playbook
    st, playbook = req("GET", "/health/sequence-playbook")
    days = playbook.get("touch_days") or playbook.get("milestone_days") or playbook.get("days") or []
    print(f"sequence-playbook: {st} touch_days={days}")
    if st != 200 or not days:
        failures.append("sequence-playbook")

    st, auto = req("GET", "/health/automation")
    print(
        f"automation health: scheduler_running={auto.get('scheduler_running')} "
        f"sequence_touches={auto.get('sequence_touches_scheduler_enabled')}"
    )
    job_keys = {j.get("job_key") for j in auto.get("jobs") or []}
    for expected in (
        "automation:tick_sequence_touches",
        "automation:tick_sourcing_refill",
    ):
        mark = "OK" if expected in job_keys else "— (aún sin corrida)"
        print(f"  job {expected}: {mark}")

    mgr_token, mgr_user = login("manager@test.com")
    company_id = int(mgr_user["company_id"])
    print(f"\nmanager login OK company_id={company_id} role={mgr_user.get('role')}")

    st, overview = req("GET", f"/companies/{company_id}/operations/overview", token=mgr_token)
    print(f"operations/overview (manager): HTTP {st}")
    if st != 200:
        failures.append("operations overview manager")
    else:
        labels = {j.get("job_key"): j.get("label") for j in overview.get("jobs") or []}
        refill_label = labels.get("automation:tick_sourcing_refill")
        print(f"  campaigns_running={overview.get('campaigns_running')} jobs={len(overview.get('jobs') or [])}")
        if refill_label:
            print(f"  sourcing refill label: {refill_label}")
        print(f"  scheduler.running={overview.get('scheduler', {}).get('running')}")

    sdr_token, _ = login("sdr@test.com")
    st_sdr, _ = req("GET", f"/companies/{company_id}/operations/overview", token=sdr_token)
    print(f"operations/overview (sdr): HTTP {st_sdr} (esperado 403)")
    if st_sdr != 403:
        failures.append(f"sdr should be 403 got {st_sdr}")

    # Tick sourcing refill directo (misma lógica que scheduler)
    from app.services.automation_runner import run_sourcing_refill_tick
    from app.services.lead_sourcing.auto_bootstrap import sourcing_refill_enabled

    print(f"\nsourcing_refill_enabled={sourcing_refill_enabled()}")
    tick = run_sourcing_refill_tick()
    print(f"run_sourcing_refill_tick: {json.dumps(tick, default=str)[:400]}")
    if tick.get("skipped") and tick.get("reason") not in (
        "sourcing_refill_disabled",
        "automation_disabled",
        "locked",
    ):
        failures.append(f"unexpected tick skip: {tick}")

    st2, auto2 = req("GET", "/health/automation")
    job_keys2 = {j.get("job_key") for j in auto2.get("jobs") or []}
    if "automation:tick_sourcing_refill" in job_keys2:
        print("OK job sourcing_refill registrado tras tick")
    elif not sourcing_refill_enabled():
        print("SKIP job sourcing_refill (refill deshabilitado por env)")
    else:
        failures.append("sourcing refill job not registered after tick")

    print()
    if failures:
        print("FALLOS:", ", ".join(failures))
        sys.exit(1)
    print("TODO OK — listo para paso 5 (visual/tutorial)")


if __name__ == "__main__":
    main()
