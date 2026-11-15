"""In-process task routing, claiming, and offline handler execution."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Callable, Dict, List, Optional


class TaskRouter:
    def __init__(self, db, timeout_seconds: Optional[int] = None, retry_attempts: Optional[int] = None):
        self.db = db
        self.handlers: Dict[str, Callable] = {}
        self.timeout_seconds = timeout_seconds
        self.retry_attempts = 1 if retry_attempts is None else int(retry_attempts)

    def register_handler(self, name: str, func: Callable, description: str = "") -> None:
        if not callable(func):
            raise ValueError(f"Handler {name!r} is not callable")
        self.handlers[name] = func
        self.db.audit("handler.registered", "system", "handler", name, json.dumps({"description": description}))

    def get_handler(self, name: str) -> Optional[Callable]:
        return self.handlers.get(name)

    def route_task(self, workflow_id: str, agent_id: str, payload: Dict) -> str:
        payload = payload or {}
        payload_json = json.dumps(payload)
        priority = int(payload.get("priority", 1))
        task_id = self.db.create_task(workflow_id, agent_id, payload_json, priority)
        self.db.audit("task.routed", agent_id, "task", task_id, payload_json)
        return task_id

    def get_task(self, task_id: str) -> Optional[Dict]:
        rows = self.db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,), fetch=True)
        return rows[0] if rows else None

    def list_tasks(self, workflow_id: Optional[str] = None, agent_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict]:
        clauses = []
        params: List[str] = []
        if workflow_id:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        sql = "SELECT * FROM tasks"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY priority DESC, created_at ASC, id ASC"
        return self.db.execute(sql, tuple(params), fetch=True)

    def claim_task(self, agent_id: str) -> Optional[str]:
        tasks = self.db.execute(
            "SELECT * FROM tasks WHERE agent_id = ? AND status = ? ORDER BY priority DESC, created_at ASC, id ASC",
            (agent_id, "backlog"),
            fetch=True,
        )
        for task in tasks:
            count = self.db.execute(
                "UPDATE tasks SET status = ?, updated_at = CURRENT_TIMESTAMP, attempts = attempts + 1 WHERE id = ? AND status = ?",
                ("claimed", task["id"], "backlog"),
            )
            if count:
                self.db.audit("task.claimed", agent_id, "task", task["id"])
                return task["id"]
        return None

    def complete_task(self, task_id: str, result: Dict) -> bool:
        task = self.get_task(task_id)
        if not task or task["status"] in {"completed", "failed"}:
            return False
        self.db.update_task_status(task_id, "completed", json.dumps(result or {}))
        self.db.audit("task.completed", task.get("agent_id") or "agent", "task", task_id, json.dumps(result or {}))
        return True

    def fail_task(self, task_id: str, error: str) -> bool:
        task = self.get_task(task_id)
        if not task or task["status"] in {"completed", "failed"}:
            return False
        result = json.dumps({"error": error})
        self.db.update_task_status(task_id, "failed", result)
        self.db.audit("task.failed", task.get("agent_id") or "agent", "task", task_id, result)
        return True

    def release_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if not task or task["status"] != "claimed":
            return False
        self.db.execute(
            "UPDATE tasks SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = ?",
            ("backlog", task_id, "claimed"),
        )
        self.db.audit("task.released", task.get("agent_id") or "agent", "task", task_id)
        return True

    def execute(self, task_id: str) -> Dict:
        """Run a registered in-process handler for the task. Does not call external models."""
        task = self.get_task(task_id)
        if not task:
            return {"status": "not_found", "task_id": task_id}
        if task["status"] == "completed":
            return {"status": "completed", "task_id": task_id, "result": _loads(task.get("result_json"))}
        if task["status"] == "failed":
            return {"status": "failed", "task_id": task_id, "result": _loads(task.get("result_json"))}

        payload = _loads(task.get("payload_json")) or {}
        handler_name = payload.get("handler") or payload.get("step") or task.get("agent_id")
        handler = self.get_handler(handler_name) if handler_name else None
        if handler is None:
            return {
                "status": "no_handler",
                "task_id": task_id,
                "handler": handler_name,
                "detail": "No in-process handler is registered for this task; it remains in the backlog to be claimed.",
            }

        attempts = max(1, self.retry_attempts)
        last_error = None
        for _ in range(attempts):
            try:
                result = self._invoke(handler, payload)
                if not isinstance(result, dict):
                    result = {"result": result}
                if task["status"] == "backlog":
                    self.db.execute(
                        "UPDATE tasks SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = ?",
                        ("claimed", task_id, "backlog"),
                    )
                self.complete_task(task_id, result)
                return {"status": "completed", "task_id": task_id, "handler": handler_name, "result": result}
            except Exception as exc:
                last_error = str(exc)
        self.fail_task(task_id, last_error or "handler failed")
        return {"status": "failed", "task_id": task_id, "handler": handler_name, "error": last_error}

    def _invoke(self, handler: Callable, payload: Dict):
        if self.timeout_seconds is None:
            return handler(payload)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(handler, payload)
            try:
                return future.result(timeout=self.timeout_seconds)
            except FuturesTimeout as exc:
                future.cancel()
                raise TimeoutError(f"Handler exceeded {self.timeout_seconds}s") from exc


def _loads(raw) -> Optional[Dict]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {"value": data}
