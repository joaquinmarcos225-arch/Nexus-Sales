"""Cifrado Fernet para tokens OAuth en reposo (ConnectedAccount)."""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

_fernet: Fernet | None = None


def get_token_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    key = (os.getenv("NEXUS_TOKEN_FERNET_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "NEXUS_TOKEN_FERNET_KEY no está definido. Generá una clave Fernet, p. ej.: "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`",
        )
    _fernet = Fernet(key.encode("utf-8"))
    return _fernet


def encrypt_secret(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return get_token_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(blob: str | None) -> str | None:
    if blob is None or blob == "":
        return None
    try:
        return get_token_fernet().decrypt(blob.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None
