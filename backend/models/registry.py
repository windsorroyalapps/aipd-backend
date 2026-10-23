
import json
class ModelRegistry:
    def __init__(self, db):
        self.db = db
    def register(self, model_id: str, name: str, version: str, provider: str, endpoint: str = "", capabilities: dict = None):
        self.db.register_model(model_id, name, version, provider, endpoint, json.dumps(capabilities or {}))
    def get_all(self) -> list:
        return self.db.get_models()
    def get_active(self) -> list:
        return self.db.get_models("active")
    def update_status(self, model_id: str, status: str):
        self.db.execute("UPDATE models SET status = ? WHERE id = ?", (status, model_id))
