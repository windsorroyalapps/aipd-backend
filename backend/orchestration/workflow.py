"""Workflow definition and lifecycle: start, advance, complete."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def parse_steps(definition: Optional[Dict]) -> List[Dict]:
    definition = definition or {}
    raw = definition.get("steps") or []
    default_agent = definition.get("agent_id") or "default"
    steps: List[Dict] = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            name = item
            agent_id = default_agent
            handler = name
            payload: Dict[str, Any] = {}
        elif isinstance(item, dict):
            name = item.get("name") or item.get("step") or f"step_{index}"
            agent_id = item.get("agent_id") or default_agent
            handler = item.get("handler") or name
            payload = dict(item.get("payload") or {})
        else:
            raise ValueError(f"Unsupported step definition at index {index}: {item!r}")
        payload.setdefault("step", name)
        payload.setdefault("step_index", index)
        payload.setdefault("handler", handler)
        steps.append({
            "index": index,
            "name": name,
            "agent_id": agent_id,
            "handler": handler,
            "payload": payload,
        })
    return steps


def load_definition(workflow: Dict) -> Dict:
    raw = workflow.get("definition_json") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _payload(task: Dict) -> Dict:
    raw = task.get("payload_json") or "{}"
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


class WorkflowEngine:
    def __init__(self, db, router):
        self.db = db
        self.router = router

    def start_workflow(self, name: str, owner_id: str, definition: Dict) -> str:
        definition = definition or {}
        priority = int(definition.get("priority", 1))
        wf_id = self.db.create_workflow(name, owner_id, json.dumps(definition), priority)
        self.db.audit("workflow.created", owner_id, "workflow", wf_id, json.dumps({"name": name}))
        return wf_id

    def get_workflow(self, wf_id: str) -> Optional[Dict]:
        rows = self.db.execute("SELECT * FROM workflows WHERE id = ?", (wf_id,), fetch=True)
        return rows[0] if rows else None

    def list_tasks(self, wf_id: str) -> List[Dict]:
        return self.db.execute(
            "SELECT * FROM tasks WHERE workflow_id = ? ORDER BY created_at ASC, id ASC",
            (wf_id,),
            fetch=True,
        )

    def get_status(self, wf_id: str) -> Dict:
        workflow = self.get_workflow(wf_id)
        if not workflow:
            return {}
        tasks = self.list_tasks(wf_id)
        steps = self.list_steps(workflow, tasks)
        return {
            "workflow": workflow,
            "tasks": tasks,
            "steps": steps,
            "progress": {
                "total": len(steps),
                "completed": sum(1 for step in steps if step["status"] == "completed"),
            },
        }

    def list_steps(self, workflow: Dict, tasks: Optional[List[Dict]] = None) -> List[Dict]:
        tasks = tasks if tasks is not None else self.list_tasks(workflow["id"])
        steps = parse_steps(load_definition(workflow))
        described = []
        for step in steps:
            task = _task_for_step(tasks, step["index"])
            described.append({
                **step,
                "task_id": task["id"] if task else None,
                "status": task["status"] if task else "pending",
            })
        return described

    def set_status(self, wf_id: str, status: str, actor_id: str = "system") -> bool:
        if not self.get_workflow(wf_id):
            return False
        self.db.execute(
            "UPDATE workflows SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, wf_id),
        )
        self.db.audit(f"workflow.{status}", actor_id, "workflow", wf_id)
        return True

    def complete(self, wf_id: str, actor_id: str = "system") -> bool:
        return self.set_status(wf_id, "completed", actor_id)

    def fail(self, wf_id: str, reason: str = "", actor_id: str = "system") -> bool:
        if not self.get_workflow(wf_id):
            return False
        self.db.execute(
            "UPDATE workflows SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            ("failed", wf_id),
        )
        self.db.audit("workflow.failed", actor_id, "workflow", wf_id, json.dumps({"reason": reason}))
        return True

    def cancel(self, wf_id: str, actor_id: str = "system") -> bool:
        return self.set_status(wf_id, "cancelled", actor_id)

    def advance(self, wf_id: str) -> Dict:
        """Create or report the next definition step. Does not invent task results."""
        workflow = self.get_workflow(wf_id)
        if not workflow:
            return {"status": "not_found", "workflow_id": wf_id}
        if workflow.get("status") in TERMINAL_STATUSES:
            return {"status": workflow["status"], "workflow_id": wf_id}

        steps = parse_steps(load_definition(workflow))
        if workflow.get("status") == "pending":
            self.set_status(wf_id, "running", workflow.get("owner_id") or "system")
            workflow = self.get_workflow(wf_id)

        if not steps:
            return {
                "status": "no_steps",
                "workflow_id": wf_id,
                "workflow": workflow,
                "task_id": None,
            }

        tasks = self.list_tasks(wf_id)
        for step in steps:
            task = _task_for_step(tasks, step["index"])
            if task is None:
                task_id = self.router.route_task(wf_id, step["agent_id"], step["payload"])
                if workflow.get("status") != "running":
                    self.set_status(wf_id, "running", workflow.get("owner_id") or "system")
                return {
                    "status": "created",
                    "workflow_id": wf_id,
                    "task_id": task_id,
                    "step": step,
                }
            if task["status"] == "failed":
                return {
                    "status": "failed",
                    "workflow_id": wf_id,
                    "task_id": task["id"],
                    "step": step,
                }
            if task["status"] != "completed":
                return {
                    "status": "waiting",
                    "workflow_id": wf_id,
                    "task_id": task["id"],
                    "task_status": task["status"],
                    "step": step,
                }

        self.complete(wf_id, workflow.get("owner_id") or "system")
        return {"status": "completed", "workflow_id": wf_id, "task_id": None}


def _task_for_step(tasks: List[Dict], index: int) -> Optional[Dict]:
    for task in tasks:
        payload = _payload(task)
        if payload.get("step_index") == index:
            return task
    return None
