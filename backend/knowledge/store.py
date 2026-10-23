
import json, os
class KnowledgeStore:
    def __init__(self, path="data/knowledge_store.json"):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    def save_knowledge(self, key: str, value: dict):
        data = {}
        try:
            with open(self.path) as f:
                data = json.load(f)
        except Exception:
            pass
        data[key] = value
        with open(self.path, "w") as f:
            json.dump(data, f)
    def retrieve_knowledge(self, key: str) -> dict:
        try:
            with open(self.path) as f:
                data = json.load(f)
            return data.get(key, {})
        except Exception:
            return {}
