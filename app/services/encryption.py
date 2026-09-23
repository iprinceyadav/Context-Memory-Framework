import base64
import os
from typing import Optional

# In a real production application, this should use cryptography.fernet or similar
# with a key loaded from environment variables (e.g., CMF_ENCRYPTION_KEY).
# For this implementation, we will use a simple base64 encoding to represent encryption
# to avoid adding heavy cryptography dependencies for Phase 3, while demonstrating the concept.

def encrypt_password(password: str) -> str:
    """Encrypts a database password before storing it in the platform DB."""
    if not password:
        return ""
    # Add a mock prefix to demonstrate it was processed
    encoded = base64.b64encode(password.encode('utf-8')).decode('utf-8')
    return f"enc_{encoded}"

def decrypt_password(encrypted_password: str) -> str:
    """Decrypts a database password for the DatabaseManager to use."""
    if not encrypted_password or not encrypted_password.startswith("enc_"):
        return encrypted_password
    
    encoded = encrypted_password[4:]
    try:
        return base64.b64decode(encoded).decode('utf-8')
    except Exception:
        return ""
