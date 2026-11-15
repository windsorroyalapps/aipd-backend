"""Role and permission checks against the permissions table."""

from __future__ import annotations

from typing import Dict, List, Optional


class PermissionEngine:
    def __init__(self, db):
        self.db = db

    def grant(self, user_id: str, resource: str, action: str, granted_by: str = "system", role_text: Optional[str] = None) -> bool:
        user = self.db.get_user_by_id(user_id)
        if not user:
            return False
        role = role_text or user.get("role") or "default"
        existing = self.db.execute(
            "SELECT id, granted FROM permissions WHERE user_id = ? AND resource = ? AND action = ? ORDER BY id DESC",
            (user_id, resource, action),
            fetch=True,
        )
        if existing:
            row = existing[0]
            if row["granted"] == 1:
                return True
            self.db.execute(
                "UPDATE permissions SET granted = 1, role_text = ?, granted_by = ?, granted_at = CURRENT_TIMESTAMP WHERE id = ?",
                (role, granted_by, row["id"]),
            )
            return True
        self.db.grant_permission(user_id, role, resource, action, granted_by)
        return True

    def revoke(self, user_id: str, resource: str, action: str) -> bool:
        count = self.db.execute(
            "UPDATE permissions SET granted = 0 WHERE user_id = ? AND resource = ? AND action = ? AND granted = 1",
            (user_id, resource, action),
        )
        return count > 0

    def check(self, user_id: str, resource: str, action: str) -> bool:
        user = self.db.get_user_by_id(user_id)
        if not user or not user.get("active", 1):
            return False
        if user.get("role") == "admin":n            return True
        rows = self.db.execute(
            "SELECT resource, action FROM permissions WHERE user_id = ? AND granted = 1",
            (user_id,),
            fetch=True,
        )
        for row in rows:
            resource_ok = row["resource"] in (resource, "*")
            action_ok = row["action"] in (action, "*")
            if resource_ok and action_ok:
                return True
        return False

    def has_role(self, user_id: str, role: str) -> bool:
        user = self.db.get_user_by_id(user_id)
        return bool(user) and user.get("role") == role

    def set_role(self, user_id: str, role: str) -> bool:
        if not self.db.get_user_by_id(user_id):
            return False
        self.db.execute("UPDATE users SET role = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (role, user_id))
        return True

    def list_for_user(self, user_id: str) -> List[Dict]:
        return self.db.execute(
            "SELECT id, user_id, role_text, resource, action, granted, granted_by, granted_at FROM permissions WHERE user_id = ? ORDER BY id",
            (user_id,),
            fetch=True,
        )
