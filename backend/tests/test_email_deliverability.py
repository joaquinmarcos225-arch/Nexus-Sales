from app.services.email_deliverability import (
    deliverable_email_skip_reason,
    is_real_deliverable_email,
)


def test_demo_local_email_blocked():
    em = "demo.prospect.3.1@mail.nexus-sales.local"
    assert not is_real_deliverable_email(em)
    assert deliverable_email_skip_reason(em) is not None


def test_real_gmail_allowed():
    em = "fernandezjoaquinjose@gmail.com"
    assert is_real_deliverable_email(em)
    assert deliverable_email_skip_reason(em) is None


def test_dot_local_blocked():
    assert not is_real_deliverable_email("user@company.local")
