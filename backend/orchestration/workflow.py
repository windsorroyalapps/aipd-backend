
import json
from typing import Dict
class WorkflowEngine:
    def __init__(self, db, router):
        self.db = db
        self.router = router
    def start_workflow(self, name: str, owner_id: str, definition: Dict) -> str:
        wf_id = self.db.create_workflow(name, owner_id, json.dumps(definition))
        return wf_id
    def get_status(self, wf_id: str) -> Dict:
        wfs = self.db.get_workflows()
        for w in wfs:
            if w["id"] == wf_id:
                tasks = self.db.execute("SELECT * FROM tasks WHERE workflow_id = ?", (wf_id,), fetch=True)
                return {"workflow": w, "tasks": tasks}
        return {}
