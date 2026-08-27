"""Avatar interno de usuario."""

from app.services.user_avatar import _ext_from_bytes, save_user_avatar


def test_ext_from_jpeg_magic():
    assert _ext_from_bytes(b"\xff\xd8\xff\xe0rest", "image/jpeg") == ".jpg"


def test_ext_from_png_magic():
    assert _ext_from_bytes(b"\x89PNG\r\n\x1a\nrest", None) == ".png"


def test_reject_unknown(tmp_path, monkeypatch):
    from app.services import user_avatar as ua

    monkeypatch.setattr(ua, "AVATAR_DIR", tmp_path)
    monkeypatch.setattr(ua, "DATA_DIR", tmp_path)
    try:
        save_user_avatar(user_id=1, data=b"not-an-image", content_type="text/plain")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "JPG" in str(e) or "PNG" in str(e)
