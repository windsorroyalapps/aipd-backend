
from typing import Optional, Dict
class SessionManager:
    def __init__(self, db, identity_provider):
        self.db = db
        self.idp = identity_provider
    def login(self, email: str) -> Optional[str]:
        return self.idp.login(email)
    def verify(self, token: str) -> Optional[Dict]:
        return self.idp.verify_token(token)
    def revoke(self, token: str) -> bool:
        import hashlib
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        return self.db.revoke_session(token_hash) > 0
