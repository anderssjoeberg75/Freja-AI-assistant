"""Encryption helpers for secrets persisted in Freja's SQLite database."""

import os
import stat

from cryptography.fernet import Fernet, InvalidToken

from backend.config import PROJECT_ROOT

ENC_PREFIX = "enc:"
_KEY_FILE = PROJECT_ROOT / ".freja_secret.key"

_fernet = None


def _load_or_create_key() -> bytes:
    env_key = os.environ.get("FREJA_ENCRYPTION_KEY")
    if env_key:
        return env_key.encode("utf-8")

    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes().strip()

    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)
    try:
        os.chmod(_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt_value(value):
    """Encrypts a secret for storage. None/empty values pass through unchanged."""
    if not value:
        return value
    token = _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    return ENC_PREFIX + token


def decrypt_value(value):
    """Decrypts a stored secret. Values without the enc: prefix are legacy
    plaintext (written before encryption-at-rest existed) and are returned
    unchanged, so existing installs keep working with no migration step."""
    if not value or not value.startswith(ENC_PREFIX):
        return value
    token = value[len(ENC_PREFIX):]
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""
