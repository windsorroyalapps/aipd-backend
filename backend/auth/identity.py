"""User identity, password credentials, and login."""

from __future__ import annotations

import hashlib
import secrets
from typing import Dict, Optional

from .sessions import SessionManager

PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 200_000
CREDENTIALS_SQL = """
CREATE TABLE IF NOT EXISTS user_credentials (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    password_hash TEXT NOT NULL,
    algorithm TEXT NOT NULL DEFAULT 'pbkdf2_sha256',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{PBKDF2_ALGORITHM}${iterations}${salt.hex()}${dk.hex()}"


def verify_password_hash(stored: str, password: str) -> bool:
    try:
        algorithm, iter_s, salt_hex, hash_hex = stored.split("$", 3)
    except ValueError:
        return False
    if algorithm != PBKDF2_ALGORITHM:
        return False
    try:
        iterations = int(iter_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return secrets.compare_digest(candidate, expected)


class IdentityProvider:
    def __init__(self, db, sessions: Optional[SessionManager] = None):
        self.db = db
        self._ensure_credential_table()
        self.sessions = sessions or SessionManager(db, self)

    def _ensure_credential_table(self) -> None:
        self.db.execute(CREDENTIALS_SQL)

    def register_user(
        self,
        email: str,
        display_name: str = "",
        provider: str = "local",
        password: Optional[str] = None,
        role: str = "user",
    ) -> Dict:
        user = self.db.get_user_by_email(email)
        if not user:
            user_id = self.db.create_user(email, display_name, provider, role)
            user = self.db.get_user_by_id(user_id)
        if password:
            self.set_password(user["id"], password)
        return user

    def get_user(self, email: Optional[str] = None, user_id: Optional[str] = None) -> Optional[Dict]:
        if user_id:
            return self.db.get_user_by_id(user_id)
        if email:
            return self.db.get_user_by_email(email)
        return None

    def set_password(self, user_id: str, password: str) -> bool:
        if not password:
            raise ValueError("Password must be a non-empty string")
        if not self.db.get_user_by_id(user_id):
            return False
        password_hash = hash_password(password)
        existing = self.db.execute(
            "SELECT user_id FROM user_credentials WHERE user_id = ?",
            (user_id,),
            fetch=True,
        )
        if existing:
            self.db.execute(
                "UPDATE user_credentials SET password_hash = ?, algorithm = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (password_hash, PBKDF2_ALGORITHM, user_id),
            )
        else:
            self.db.execute(
                "INSERT INTO user_credentials (user_id, password_hash, algorithm) VALUES (?, ?, ?)",
                (user_id, password_hash, PBKDF2_ALGORITHM),
            )
        return True

    def verify_password(self, user_id: str, password: str) -> bool:
        if not password:
            return False
        rows = self.db.execute(
            "SELECT password_hash FROM user_credentials WHERE user_id = ?",
            (user_id,),
            fetch=True,
        )
        if not rows:
            return False
        return verify_password_hash(rows[0]["password_hash"], password)

    def has_password(self, user_id: str) -> bool:
        rows = self.db.execute(
            "SELECT 1 FROM user_credentials WHERE user_id = ?",
            (user_id,),
            fetch=True,
        )
        return bool(rows)

    def login(self, email: str, provider: str = "local", password: Optional[str] = None) -> Optional[str]:
        """Issue a session token.

        Existing callers (server, demo, test_all) log in with only an email.
        If a local password has been set, it is required and must match.
        Accounts with no stored password keep the legacy email-only path so
        current integrations keep working.
        """
        del provider  # recorded at registration; not used to reject existing users
        user = self.db.get_user_by_email(email)
        if not user or not user.get("active", 1):
            return None
        if self.has_password(user["id"]):
            if not self.verify_password(user["id"], password or ""):
                return None
        elif password:
            return None
        elif (user.get("identity_provider") or "local") != "local":
            # OAuth-provisioned accounts must use the OAuth exchange (or set a local password).
            return None
        return self.sessions.issue(user)

    def verify_token(self, token: str) -> Optional[Dict]:
        return self.sessions.verify(token)

    def logout(self, token: str) -> bool:
        return self.sessions.revoke(token)
