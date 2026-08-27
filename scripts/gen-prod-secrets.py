# Genera secretos para .env.production
# Uso: python scripts/gen-prod-secrets.py

from cryptography.fernet import Fernet
import secrets

print("NEXUS_JWT_SECRET=" + secrets.token_urlsafe(48))
print("NEXUS_TOKEN_FERNET_KEY=" + Fernet.generate_key().decode())
print("GOOGLE_OAUTH_STATE_SECRET=" + secrets.token_urlsafe(32))
