"""WA: nombre corto de producto + split de explicaciones largas."""

from __future__ import annotations

from app.services.outreach_display_names import scrub_piped_product_title_in_body
from app.services.whatsapp_meow_sequence import split_whatsapp_default_bubbles


def test_scrub_piped_product_title_in_body():
    long_name = (
        "Contractia | Plataforma integral de gestión documental y firma "
        "electrónica y digital"
    )
    body = (
        f"Vi que sos COO de Costguard, y con {long_name} sabemos que el "
        "desafío no está solamente en firmar digitalmente."
    )
    out = scrub_piped_product_title_in_body(body, product_name=long_name)
    assert "Contractia |" not in out
    assert "Plataforma integral" not in out
    assert "con Contractia sabemos" in out


def test_split_whatsapp_long_explanation_at_colon():
    body = (
        "Vi que sos COO de Costguard, y con Contractia sabemos que el desafío "
        "no está solamente en firmar digitalmente, sino en todo lo que sucede "
        "antes y después: aprobaciones internas, seguimiento de pendientes, "
        "coordinación entre áreas, validación de identidad y resguardo de la "
        "documentación."
    )
    parts = split_whatsapp_default_bubbles(body)
    assert len(parts) >= 2
    assert "Contractia |" not in " ".join(parts)
    assert parts[0].rstrip().endswith(".")
    assert "aprobaciones" in parts[1].lower() or "Aprobaciones" in parts[1]
