"""Offline-testable OAuth authorization-code scaffold.

This is not wired to Google, GitHub, Stripe, or any other external IdP.
No outbound provider calls are made. For tests and local development,
issue a one-time code with ``issue_dev_code`` and exchange it here.
A real provider integration needs client IDs, secrets, and token endpoints.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Dict, List, Optional
from urllib.parse import urlencode

from .sessions import load_auth_settings, parse_dt, utcnow
from datetime import timedelta

CODE_TTL_MINUTES = 10
OAUTH_CODES_SQL = """
CREATE TABLE IF NOT EXISTS oauth_codes (
    code_hash TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    email TEXT NOT NULL,
    display_name TEXT,
    redirect_uri TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class OAuthService:
    def __init__(self, db, identity_provider, allowed_providers: Optional[List[str]] = None):
        self.db = db
        self.idp = identity_provider
        settings = load_auth_settings()
        self.allowed_providers = [p.lower() for p in (allowed_providers or settings["oauth_providers"])]
        self.db.execute(OAUTH_CODES_SQL)

    def list_providers(self) -> List[Dict]:
        return [
            {
                "id": provider,
                "external_idp_wired": False,
                "detail": "Configured as an allowed provider name only; no client credentials or token endpoint are set.",
            }
            for provider in self.allowed_providers
        ]

    def is_provider_supported(self, provider: str) -> bool:
        return (provider or "").lower() in self.allowed_providers

    def authorization_url(self, provider: str, redirect_uri: str, state: str = "") -> Dict:
        provider = (provider or "").lower()
        if not self.is_provider_supported(provider):
            raise ValueError(f"Unsupported OAuth provider: {provider}")
        if not redirect_uri:
            raise ValueError("redirect_uri is required")
        query = urlencode({
            "response_type": "code",
            "provider": provider,
            "redirect_uri": redirect_uri,
            "state": state,
        })
        return {
            "provider": provider,
            "url": f"/auth/oauth/authorize?{query}",
            "state": state,
            "external_idp_wired": False,
            "detail": (
                "No external identity provider is wired. This URL is a local "
                "placeholder. Use issue_dev_code() to mint an exchangeable code offline."
            ),
        }

    def issue_dev_code(self, provider: str, email: str, redirect_uri: str, display_name: str = "") -> str:
        """Mint a one-time authorization code for offline tests and local development."""
        provider = (provider or "").lower()
        if not self.is_provider_supported(provider):
            raise ValueError(f"Unsupported OAuth provider: {provider}")
        if not email:
            raise ValueError("email is required")
        if not redirect_uri:
            raise ValueError("redirect_uri is required")
        code = secrets.token_urlsafe(32)
        expires = utcnow() + timedelta(minutes=CODE_TTL_MINUTES)
        self.db.execute(
            "INSERT INTO oauth_codes (code_hash, provider, email, display_name, redirect_uri, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (_hash_code(code), provider, email, display_name, redirect_uri, expires.isoformat()),
        )
        return code

    def exchange_code(self, provider: str, code: str, redirect_uri: str) -> Dict:
        provider = (provider or "").lower()
        if not self.is_provider_supported(provider):
            raise ValueError(f"Unsupported OAuth provider: {provider}")
        if not code:
            raise ValueError("code is required")
        rows = self.db.execute(
            "SELECT * FROM oauth_codes WHERE code_hash = ?",
            (_hash_code(code),),
            fetch=True,
        )
        if not rows:
            raise ValueError("Invalid authorization code")
        record = rows[0]
        if record["consumed"]:
            raise ValueError("Authorization code has already been used")
        if record["provider"] != provider:
            raise ValueError("Provider does not match the authorization code")
        if record["redirect_uri"] != redirect_uri:
            raise ValueError("redirect_uri does not match the authorization code")
        if parse_dt(record["expires_at"]) <= utcnow():
            raise ValueError("Authorization code has expired")

        self.db.execute("UPDATE oauth_codes SET consumed = 1 WHERE code_hash = ?", (record["code_hash"],))
        user = self.idp.register_user(record["email"], record.get("display_name") or "", provider)
        token = self.idp.sessions.issue(user)
        return {
            "token": token,
            "user_id": user["id"],
            "email": user["email"],
            "provider": provider,
        }
