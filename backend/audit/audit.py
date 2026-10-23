
import datetime, json
class AuditLog:
    def __init__(self, db):
        self.db = db
    def log(self, event: str, actor: str, resource: str, resource_id: str, details: dict = None):
        self.db.audit(event, actor, resource, resource_id, json.dumps(details or {}))
    def query(self, actor: str = None, event: str = None, resource: str = None) -> list:
        sql = "SELECT * FROM audit_log WHERE 1=1"
        params = []
        if actor: sql += " AND actor_id = ?"; params.append(actor)
        if event: sql += " AND event_type = ?"; params.append(event)
        if resource: sql += " AND resource_type = ?"; params.append(resource)
        sql += " ORDER BY timestamp DESC"
        return self.db.execute(sql, tuple(params), fetch=True)
    def get_events_for_resource(self, resource_type: str, resource_id: str) -> list:
        return self.db.execute("SELECT * FROM audit_log WHERE resource_type = ? AND resource_id = ? ORDER BY timestamp DESC", (resource_type, resource_id), fetch=True)
