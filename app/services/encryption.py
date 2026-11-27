import base64
import os
from typing import Optional


def encrypt(password: str) -> str:
    if not password:
        return ""
    encoded = base64.b64encode(password.encode('utf-8')).decode('utf-8')
    return f"enc_{encoded}"

def decrypt(encrypted_password: str) -> str:
    if not encrypted_password or not encrypted_password.startswith("enc_"):
        return encrypted_password

    encoded = encrypted_password[4:]
    try:
        return base64.b64decode(encoded).decode('utf-8')
    except Exception:
        return ""
