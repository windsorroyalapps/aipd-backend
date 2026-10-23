
from typing import Optional
class PermissionEngine:
    def __init__(self, db):
        self.db = db
    def grant(self, user_id: str, resource: str, action: str, granted_by: str = "system"):
        self.db.grant_permission(user_id, "default", resource, action, granted_by)
    def check(self, user_id: str, resource: str, action: str) -> bool:
        return self.db.check_permission(user_id, resource, action)
