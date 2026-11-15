import os
import tempfile
import unittest
from datetime import timedelta

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.db import AIPDDatabase
from backend.auth.identity import IdentityProvider, verify_password_hash
from backend.auth.sessions import SessionManager, hash_token, utcnow
from backend.auth.permissions import PermissionEngine
from backend.auth.oauth import OAuthService
from backend.auth import IdentityProvider as ExportedIdentity, OAuthService as ExportedOAuth


class AuthTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix="aipd_auth_", suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        self.db = AIPDDatabase(self.db_path)
        self.idp = IdentityProvider(self.db)
        self.sessions = self.idp.sessions
        self.perm = PermissionEngine(self.db)
        self.oauth = OAuthService(self.db, self.idp)

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_register_and_lookup(self):
        user = self.idp.register_user("alice@example.com", "Alice", "local", password="s3cret-pass", role="user")
        self.assertEqual(user["email"], "alice@example.com")
        self.assertEqual(user["display_name"], "Alice")
        self.assertEqual(user["role"], "user")
        again = self.idp.register_user("alice@example.com", "Ignored")
        self.assertEqual(again["id"], user["id"])
        self.assertEqual(self.idp.get_user(email="alice@example.com")["id"], user["id"])
        self.assertEqual(self.idp.get_user(user_id=user["id"])["email"], "alice@example.com")

    def test_password_hash_is_salted_and_verified(self):
        user = self.idp.register_user("bob@example.com", "Bob", password="correct-horse")
        rows = self.db.execute(
            "SELECT password_hash FROM user_credentials WHERE user_id = ?",
            (user["id"],),
            fetch=True,
        )
        stored = rows[0]["password_hash"]
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertNotIn("correct-horse", stored)
        self.assertTrue(self.idp.verify_password(user["id"], "correct-horse"))
        self.assertFalse(self.idp.verify_password(user["id"], "wrong-pass"))
        self.assertTrue(verify_password_hash(stored, "correct-horse"))
        other = self.idp.register_user("carol@example.com", "Carol", password="correct-horse")
        other_hash = self.db.execute(
            "SELECT password_hash FROM user_credentials WHERE user_id = ?",
            (other["id"],),
            fetch=True,
        )[0]["password_hash"]
        self.assertNotEqual(stored, other_hash)

    def test_login_requires_password_when_one_is_set(self):
        self.idp.register_user("dave@example.com", "Dave", password="p@ssw0rd")
        self.assertIsNone(self.idp.login("dave@example.com"))
        self.assertIsNone(self.idp.login("dave@example.com", password="nope"))
        token = self.idp.login("dave@example.com", password="p@ssw0rd")
        self.assertIsNotNone(token)
        session = self.idp.verify_token(token)
        self.assertEqual(session["email"], "dave@example.com")
        self.assertEqual(session["role"], "user")

    def test_legacy_email_only_login_without_stored_password(self):
        # Existing server/demo/test_all path: users created via the DB with no password.
        self.db.create_user("legacy@example.com", "Legacy", "local", "admin")
        token = self.idp.login("legacy@example.com")
        self.assertIsNotNone(token)
        session = self.idp.verify_token(token)
        self.assertIsNotNone(session)
        self.assertEqual(session["email"], "legacy@example.com")
        self.assertEqual(session["role"], "admin")
        self.assertIsNone(self.idp.login("legacy@example.com", password="anything"))

    def test_login_unknown_or_inactive_user(self):
        self.assertIsNone(self.idp.login("missing@example.com"))
        user_id = self.db.create_user("idle@example.com", "Idle")
        self.db.execute("UPDATE users SET active = 0 WHERE id = ?", (user_id,))
        self.assertIsNone(self.idp.login("idle@example.com"))

    def test_empty_password_rejected(self):
        user = self.idp.register_user("erin@example.com", "Erin")
        with self.assertRaises(ValueError):
            self.idp.set_password(user["id"], "")
        self.assertFalse(self.idp.set_password("missing-user-id", "ok-password"))

    def test_jwt_session_roundtrip_and_revoke(self):
        user = self.idp.register_user("fran@example.com", "Fran", password="tok")
        token = self.idp.login("fran@example.com", password="tok")
        # Token is a JWT with three segments
        self.assertEqual(len(token.split(".")), 3)
        session = self.sessions.verify(token)
        self.assertEqual(session["user_id"], user["id"])
        self.assertTrue(self.idp.logout(token))
        self.assertIsNone(self.idp.verify_token(token))
        self.assertFalse(self.sessions.revoke(""))

    def test_session_manager_login_delegates(self):
        self.db.create_user("greg@example.com", "Greg")
        manager = SessionManager(self.db, self.idp)
        token = manager.login("greg@example.com")
        self.assertIsNotNone(manager.verify(token))
        self.assertTrue(manager.revoke(token))
        self.assertIsNone(manager.verify(token))

    def test_expired_jwt_is_rejected(self):
        user = self.idp.register_user("hank@example.com", "Hank")
        short = SessionManager(self.db, self.idp, expiry_hours=0)
        # expiry_hours=0 still produces exp == iat; give the clock a moment and
        # also backdate the row so both JWT and table expiry are in the past.
        token = short.issue(user)
        self.db.execute(
            "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
            ((utcnow() - timedelta(hours=1)).isoformat(), hash_token(token)),
        )
        # A freshly issued 0-hour token may already be expired by JWT exp.
        self.assertIsNone(short.verify(token))

    def test_db_expiry_rejects_otherwise_valid_jwt(self):
        user = self.idp.register_user("ivy@example.com", "Ivy")
        token = self.sessions.issue(user)
        self.assertIsNotNone(self.sessions.verify(token))
        self.db.execute(
            "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
            ((utcnow() - timedelta(seconds=5)).isoformat(), hash_token(token)),
        )
        self.assertIsNone(self.sessions.verify(token))

    def test_tampered_or_wrong_secret_token_is_rejected(self):
        self.idp.register_user("jade@example.com", "Jade")
        token = self.idp.login("jade@example.com")
        self.assertIsNone(self.sessions.verify(token + "x"))
        self.assertIsNone(self.sessions.verify("not-a-jwt"))
        other = SessionManager(self.db, self.idp, secret_key="a-different-secret")
        self.assertIsNone(other.verify(token))

    def test_permissions_grant_check_revoke(self):
        user_id = self.db.create_user("kate@example.com", "Kate", "local", "user")
        self.assertFalse(self.perm.check(user_id, "workflows", "read"))
        self.assertTrue(self.perm.grant(user_id, "workflows", "read", "system"))
        self.assertTrue(self.perm.check(user_id, "workflows", "read"))
        self.assertFalse(self.perm.check(user_id, "workflows", "write"))
        self.assertTrue(self.perm.revoke(user_id, "workflows", "read"))
        self.assertFalse(self.perm.check(user_id, "workflows", "read"))
        # Re-grant after revoke should work
        self.assertTrue(self.perm.grant(user_id, "workflows", "read", "system"))
        self.assertTrue(self.perm.check(user_id, "workflows", "read"))
        listed = self.perm.list_for_user(user_id)
        self.assertTrue(any(row["resource"] == "workflows" and row["granted"] == 1 for row in listed))

    def test_wildcard_and_admin_role(self):
        user_id = self.db.create_user("leo@example.com", "Leo", "local", "user")
        self.perm.grant(user_id, "*", "read", "system")
        self.assertTrue(self.perm.check(user_id, "workflows", "read"))
        self.assertFalse(self.perm.check(user_id, "workflows", "write"))
        self.assertTrue(self.perm.set_role(user_id, "admin"))
        self.assertTrue(self.perm.has_role(user_id, "admin"))
        self.assertTrue(self.perm.check(user_id, "anything", "delete"))
        self.assertFalse(self.perm.grant("missing-user", "workflows", "read"))
        self.assertFalse(self.perm.check("missing-user", "workflows", "read"))

    def test_inactive_user_has_no_permissions(self):
        user_id = self.db.create_user("mia@example.com", "Mia", "local", "admin")
        self.db.execute("UPDATE users SET active = 0 WHERE id = ?", (user_id,))
        self.assertFalse(self.perm.check(user_id, "workflows", "read"))

    def test_oauth_offline_code_flow(self):
        redirect = "https://aipharmadevelopments.com/oauth/callback"
        listing = self.oauth.list_providers()
        self.assertTrue({p["id"] for p in listing} >= {"google", "github", "stripe"})
        self.assertTrue(all(p["external_idp_wired"] is False for p in listing))
        loc = self.oauth.authorization_url("google", redirect, state="xyz")
        self.assertFalse(loc["external_idp_wired"])
        self.assertIn("provider=google", loc["url"])
        self.assertEqual(loc["state"], "xyz")
        code = self.oauth.issue_dev_code("google", "nina@example.com", redirect, "Nina")
        result = self.oauth.exchange_code("google", code, redirect)
        self.assertEqual(result["email"], "nina@example.com")
        self.assertEqual(result["provider"], "google")
        session = self.idp.verify_token(result["token"])
        self.assertEqual(session["email"], "nina@example.com")
        user = self.idp.get_user(email="nina@example.com")
        self.assertEqual(user["identity_provider"], "google")
        # OAuth users cannot use the email-only local login path
        self.assertIsNone(self.idp.login("nina@example.com"))

    def test_oauth_rejects_bad_codes_and_providers(self):
        redirect = "https://aipharmadevelopments.com/oauth/callback"
        with self.assertRaises(ValueError):
            self.oauth.authorization_url("unknown-idp", redirect)
        with self.assertRaises(ValueError):
            self.oauth.issue_dev_code("not-a-provider", "owen@example.com", redirect)
        with self.assertRaises(ValueError):
            self.oauth.issue_dev_code("google", "", redirect)
        code = self.oauth.issue_dev_code("github", "owen@example.com", redirect)
        with self.assertRaises(ValueError):
            self.oauth.exchange_code("github", code, "https://other.example/callback")
        with self.assertRaises(ValueError):
            self.oauth.exchange_code("google", code, redirect)
        self.oauth.exchange_code("github", code, redirect)
        with self.assertRaises(ValueError):
            self.oauth.exchange_code("github", code, redirect)
        with self.assertRaises(ValueError):
            self.oauth.exchange_code("github", "not-a-real-code", redirect)

    def test_oauth_expired_code_is_rejected(self):
        redirect = "https://aipharmadevelopments.com/oauth/callback"
        code = self.oauth.issue_dev_code("stripe", "pam@example.com", redirect)
        self.db.execute(
            "UPDATE oauth_codes SET expires_at = ? WHERE code_hash = ?",
            ((utcnow() - timedelta(minutes=1)).isoformat(), hash_token(code)),
        )
        with self.assertRaises(ValueError):
            self.oauth.exchange_code("stripe", code, redirect)

    def test_package_exports(self):
        self.assertIs(ExportedIdentity, IdentityProvider)
        self.assertIs(ExportedOAuth, OAuthService)


if __name__ == "__main__":
    unittest.main()
