from cryptography.fernet import Fernet

from app.core.config import get_settings


def _fernet() -> Fernet:
    settings = get_settings()
    if not settings.token_encryption_key:
        raise RuntimeError(
            "TOKEN_ENCRYPTION_KEY is not set. Generate one with: python -c "
            '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(settings.token_encryption_key.encode())


def encrypt_token(raw_token: str) -> str:
    return _fernet().encrypt(raw_token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    return _fernet().decrypt(encrypted_token.encode()).decode()
