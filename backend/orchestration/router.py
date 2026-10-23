
from typing import Dict, List, Optional
class TaskRouter:
    def __init__(self, db):
        self.db = db
    def route_task(self, workflow_id: str, agent_id: str, payload: Dict) -> str:
        payload_json = __import__('json').dumps(payload)
        task_id = self.db.create_task(workflow_id, agent_id, payload_json)
        self.db.audit("task.routed", agent_id, "task", task_id, payload_json)
        return task_id
    def claim_task(self, agent_id: str) -> Optional[str]:
        tasks = self.db.get_tasks_by_agent(agent_id, "backlog")
        if not tasks:
            return None
        task = tasks[0]
        self.db.update_task_status(task["id"], "claimed")
        self.db.audit("task.claimed", agent_id, "task", task["id"])
        return task["id"]
    def complete_task(self, task_id: str, result: Dict):
        import json
        self.db.update_task_status(task_id, "completed", json.dumps(result))
        self.db.audit("task.completed", "agent", "task", task_id, json.dumps(result))
