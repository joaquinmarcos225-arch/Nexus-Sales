"""Tests for LinkedIn prospect resolution by profile URL."""

from unittest.mock import MagicMock

from app.services.linkedin_assisted_service import (
    linkedin_profile_slug,
    resolve_prospect_by_linkedin_url,
)


def test_linkedin_profile_slug_decodes_accent():
    slug = linkedin_profile_slug("https://www.linkedin.com/in/mia-%C3%A1lvarez/")
    assert slug == "mia-álvarez"


def test_resolve_prospect_by_linkedin_url_matches_slug():
    db = MagicMock()
    match = MagicMock(
        id=10,
        company_id=1,
        linkedin_url="https://www.linkedin.com/in/mia-álvarez/",
    )
    other = MagicMock(
        id=11,
        company_id=1,
        linkedin_url="https://www.linkedin.com/in/otro-perfil/",
    )
    db.scalars.return_value.all.return_value = [other, match]

    found = resolve_prospect_by_linkedin_url(
        db,
        company_id=1,
        url="https://www.linkedin.com/in/mia-%C3%A1lvarez/",
    )
    assert found is match


def test_resolve_prospect_by_linkedin_url_returns_none_when_missing():
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    found = resolve_prospect_by_linkedin_url(
        db,
        company_id=1,
        url="https://www.linkedin.com/in/desconocido/",
    )
    assert found is None
