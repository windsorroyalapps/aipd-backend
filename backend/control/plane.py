
class ControlPlane:
    def __init__(self, db, config):
        self.db = db
        self.config = config
        self.state = {}
    def set_state(self, key: str, value):
        self.state[key] = value
    def get_state(self, key: str):
        return self.state.get(key)
    def health_check(self) -> dict:
        return {"status": "healthy", "db_connected": True, "workflows": len(self.db.get_workflows())}
    def get_metrics(self) -> dict:
        return {"workflows": len(self.db.get_workflows()), "pending_approvals": 0}
