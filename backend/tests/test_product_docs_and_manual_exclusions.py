"""Extracción de documentos de producto + import manual de exclusiones."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.company import Company
from app.services.crm import exclusions as crm_exclusions
from app.services.product_document_extract import DocumentExtractError, extract_document_text


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_extract_txt_ok():
    out = extract_document_text(
        filename="brief.txt",
        data=(
            b"Nexus Sales automatiza prospeccion outbound con email, LinkedIn y WhatsApp "
            b"para equipos comerciales B2B."
        ),
    )
    assert out.format == "txt"
    assert out.chars >= 40
    assert "Nexus" in out.text


def test_extract_too_short():
    try:
        extract_document_text(filename="x.txt", data=b"corto")
        assert False, "expected error"
    except DocumentExtractError as exc:
        assert "40" in exc.message or "suficiente" in exc.message.lower()


def test_manual_exclusion_import_lines():
    db = _session()
    company = Company(name="Acme", plan="starter", employee_count=5)
    db.add(company)
    db.flush()

    result = crm_exclusions.import_manual_exclusions_text(
        db,
        company.id,
        "already@acme.com\nacme.com\nAcme Industries\n",
    )
    assert result.ok
    assert result.inserted >= 2
    status = crm_exclusions.exclusion_status(db, company.id)
    assert status["total"] >= 2
    assert status["by_provider"].get("manual", 0) >= 2

    deleted = crm_exclusions.clear_manual_exclusions(db, company.id)
    assert deleted >= 2
    status2 = crm_exclusions.exclusion_status(db, company.id)
    assert status2["by_provider"].get("manual", 0) == 0
