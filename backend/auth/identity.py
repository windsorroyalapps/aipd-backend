
import jwt, datetime, os, secrets, hashlib
from typing import Optional, Dict

SECRET_KEY = os.getenv("AIPD_SECRET_KEY", "aipd-dev-secret-change-in-production")

class IdentityProvider:
    def __init__(self, db):
        self.db = db

    def register_user(self, email: str, display_name: str = "", provider: str = "local") -> Dict:
        user = self.db.get_user_by_email(email)
        if user:
            return user
        user_id = self.db.create_user(email, display_name, provider)
        return self.db.get_user_by_id(user_id)

    def login(self, email: str, provider: str = "local") -> Optional[str]:
        user = self.db.get_user_by_email(email)
        if not user:
            return None
        # Issue session token
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires = (datetime.datetime.utcnow() + datetime.timedelta(hours=24)).isoformat()
        session_id = self.db.create_session(user["id"], token_hash, expires)
        return token

    def verify_token(self, token: str) -> Optional[Dict]:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        session = self.db.get_session_by_token_hash(token_hash)
        if session:
            return {"user_id": session["user_id"], "email": session.get("email"), "role": session.get("role")}
        return None
