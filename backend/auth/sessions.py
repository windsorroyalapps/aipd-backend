"""Session issue, verify, and revoke using the sessions table and JWT."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import jwt

DEFAULT_SECRET = "aipd-dev-secret-change-in-production"
DEFAULT_EXPIRY_HOURS = 24
JWT_ALGORITHM = "HS256"


def load_auth_settings() -> Dict:
    """Load auth settings from the environment, then config/default.yaml."""
    secret = os.getenv("AIPD_SECRET_KEY")
    expiry_hours = DEFAULT_EXPIRY_HOURS
    providers = ["google", "github", "stripe"]
    try:
        from backend.config import Config
        cfg = Config()
        secret = secret or cfg.get("auth.secret_key")
        configured_expiry = cfg.get("auth.token_expiry_hours", DEFAULT_EXPIRY_HOURS)
        if configured_expiry is not None:
            expiry_hours = int(configured_expiry)
        providers = list(cfg.get("auth.oauth_providers") or providers)
    except Exception:
        pass
    return {
        "secret_key": secret or DEFAULT_SECRET,
        "expiry_hours": expiry_hours,
        "oauth_providers": providers,
    }


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SessionManager:
    def __init__(self, db, identity_provider, secret_key: Optional[str] = None, expiry_hours: Optional[int] = None):
        settings = load_auth_settings()
        self.db = db
        self.idp = identity_provider
        self.secret_key = secret_key or settings["secret_key"]
        self.expiry_hours = DEFAULT_EXPIRY_HOURS if expiry_hours is None else int(expiry_hours)

    def login(self, email: str) -> Optional[str]:
        return self.idp.login(email)

    def issue(self, user: Dict, extra_claims: Optional[Dict] = None) -> str:
        """Create a signed JWT and persist its hash in the sessions table."""
        now = utcnow()
        expires = now + timedelta(hours=self.expiry_hours)
        payload = {
            "sub": user["id"],
            "email": user.get("email"),
            "role": user.get("role"),
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
        }
        if extra_claims:
            payload.update(extra_claims)
        token = jwt.encode(payload, self.secret_key, algorithm=JWT_ALGORITHM)
        if isinstance(token, bytes):
            token = token.decode("utf-8")
        self.db.create_session(user["id"], hash_token(token), expires.isoformat())
        return token

    def verify(self, token: str) -> Optional[Dict]:
        if not token:
            return None
        try:
            claims = jwt.decode(token, self.secret_key, algorithms=[JWT_ALGORITHM])
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None
        session = self.db.get_session_by_token_hash(hash_token(token))
        if not session:
            return None
        expires_at = session.get("expires_at")
        if not expires_at or parse_dt(expires_at) <= utcnow():
            return None
        return {
            "user_id": session["user_id"],
            "email": session.get("email"),
            "role": session.get("role"),
        }

    def revoke(self, token: str) -> bool:
        if not token:
            return False
        return self.db.revoke_session(hash_token(token)) > 0
