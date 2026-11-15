"""Run and step workflows, tracking persisted workflow runs."""

from __future__ import annotations

import json
import queue
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .workflow import WorkflowEngine, load_definition, parse_steps

RUNS_SQL = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step TEXT,
    detail_json TEXT,
    started_at TEXT,
    finished_at TEXT
);
"""


def load_orchestration_settings() -> Dict:
    settings = {
        "max_workers": 10,
        "retry_attempts": 3,
        "retry_delay_seconds": 5,
        "timeout_seconds": 180,
    }
    try:
        from backend.config import Config
        cfg = Config()
        for key in list(settings):
            value = cfg.get(f"orchestration.{key}")
            if value is not None:
                settings[key] = int(value)
    except Exception:
        pass
    return settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OrchestrationEngine:
    def __init__(self, db, router, workflow_engine: Optional[WorkflowEngine] = None):
        settings = load_orchestration_settings()
        self.db = db
        self.router = router
        self.workflows = workflow_engine or WorkflowEngine(db, router)
        self.settings = settings
        self.max_workers = settings["max_workers"]
        if getattr(router, "retry_attempts", None) in (None, 1) and not hasattr(router, "_retry_configured"):
            router.retry_attempts = settings["retry_attempts"]
        if getattr(router, "timeout_seconds", None) is None:
            router.timeout_seconds = settings["timeout_seconds"]
        self.queue: queue.Queue = queue.Queue()
        self.running = True
        self.thread = threading.Thread(target=self.run_loop, daemon=True, name="aipd-orchestration")
        self._ensure_runs_table()

    def _ensure_runs_table(self) -> None:
        self.db.execute(RUNS_SQL)

    def start(self) -> None:
        if self.thread.is_alive():
            return
        self.running = True
        if self.thread.ident is not None:
            self.thread = threading.Thread(target=self.run_loop, daemon=True, name="aipd-orchestration")
        self.thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self.running = False
        if self.thread.is_alive():
            self.thread.join(timeout=timeout)

    def submit(self, item: dict) -> None:
        self.queue.put(item)

    def drain(self, max_items: int = 100) -> List[Dict]:
        """Process queued items on the calling thread (offline-testable)."""
        results = []
        for _ in range(max_items):
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            results.append(self.process_item(item))
        return results

    def run_loop(self) -> None:
        while self.running:
            try:
                item = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self.process_item(item)
            except Exception as exc:
                self.db.audit(
                    "orchestration.error",
                    "system",
                    "orchestration",
                    str((item or {}).get("id") or "unknown"),
                    json.dumps({"error": str(exc)}),
                )

    def process_item(self, item: dict) -> Dict:
        item = item or {}
        typ = item.get("type") or "workflow"
        target_id = item.get("id") or item.get("workflow_id") or item.get("task_id") or "unknown"
        if typ in {"workflow", "run"}:
            result = self.run_workflow(item.get("id") or item.get("workflow_id"))
        elif typ in {"step", "advance"}:
            result = self.step_workflow(item.get("id") or item.get("workflow_id"))
        elif typ == "task":
            result = self.run_task(item.get("id") or item.get("task_id"))
        else:
            result = {"status": "ignored", "detail": f"Unknown orchestration item type: {typ}"}
        self.db.audit("orchestration.processed", "system", "orchestration", target_id, json.dumps({"type": typ, "status": result.get("status")}))
        return result

    def step_workflow(self, wf_id: str) -> Dict:
        if not wf_id:
            return {"status": "not_found", "workflow_id": wf_id}
        result = self.workflows.advance(wf_id)
        if result.get("status") in {"created", "waiting"} and result.get("task_id"):
            execution = self.router.execute(result["task_id"])
            result["execution"] = execution
            if execution.get("status") == "completed":
                # Advance again so a completed step can close the workflow or expose the next task.
                follow = self.workflows.advance(wf_id)
                result["follow_up"] = follow
                result["status"] = follow.get("status") or result["status"]
                if follow.get("task_id"):
                    result["task_id"] = follow.get("task_id")
            elif execution.get("status") == "failed":
                self.workflows.fail(wf_id, execution.get("error") or "task failed")
                result["status"] = "failed"
        return result

    def run_workflow(self, wf_id: str) -> Dict:
        workflow = self.workflows.get_workflow(wf_id)
        if not workflow:
            return {"status": "not_found", "workflow_id": wf_id}
        run_id = self._open_run(wf_id)
        steps = parse_steps(load_definition(workflow))
        try:
            if not steps:
                self._finish_run(run_id, "no_steps", None, {"detail": "Workflow has no steps to run"})
                return {"status": "no_steps", "workflow_id": wf_id, "run_id": run_id}

            last = None
            while True:
                last = self.step_workflow(wf_id)
                status = last.get("status")
                execution = (last or {}).get("execution") or {}
                step_name = (last.get("step") or {}).get("name")
                if status == "completed":
                    self._finish_run(run_id, "completed", None, last)
                    return {"status": "completed", "workflow_id": wf_id, "run_id": run_id, "result": last}
                if status in {"failed", "cancelled", "not_found"}:
                    self._finish_run(run_id, status, step_name, last)
                    return {"status": status, "workflow_id": wf_id, "run_id": run_id, "result": last}
                if status == "no_steps":
                    self._finish_run(run_id, "no_steps", None, last)
                    return {"status": "no_steps", "workflow_id": wf_id, "run_id": run_id, "result": last}
                if execution.get("status") == "failed":
                    self._finish_run(run_id, "failed", step_name, last)
                    return {"status": "failed", "workflow_id": wf_id, "run_id": run_id, "result": last}
                if execution.get("status") == "completed":
                    # A step finished; loop to run the next created step or close the workflow.
                    continue
                if execution.get("status") == "no_handler" or status == "waiting":
                    self._update_run(run_id, "waiting", step_name, last)
                    return {
                        "status": "waiting",
                        "workflow_id": wf_id,
                        "run_id": run_id,
                        "task_id": last.get("task_id"),
                        "result": last,
                    }
                if status == "created":
                    continue
                self._finish_run(run_id, "waiting", step_name, last)
                return {"status": "waiting", "workflow_id": wf_id, "run_id": run_id, "result": last}
        except Exception as exc:
            self.workflows.fail(wf_id, str(exc))
            self._finish_run(run_id, "failed", None, {"error": str(exc)})
            return {"status": "failed", "workflow_id": wf_id, "run_id": run_id, "error": str(exc)}

    def run_task(self, task_id: str) -> Dict:
        return self.router.execute(task_id)

    def get_run(self, run_id: str) -> Optional[Dict]:
        rows = self.db.execute("SELECT * FROM workflow_runs WHERE id = ?", (run_id,), fetch=True)
        return rows[0] if rows else None

    def list_runs(self, workflow_id: str) -> List[Dict]:
        return self.db.execute(
            "SELECT * FROM workflow_runs WHERE workflow_id = ? ORDER BY started_at ASC",
            (workflow_id,),
            fetch=True,
        )

    def _open_run(self, wf_id: str) -> str:
        run_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO workflow_runs (id, workflow_id, status, started_at, detail_json) VALUES (?, ?, ?, ?, ?)",
            (run_id, wf_id, "running", _utcnow().isoformat(), "{}"),
        )
        return run_id

    def _update_run(self, run_id: str, status: str, current_step: Optional[str], detail: Dict) -> None:
        self.db.execute(
            "UPDATE workflow_runs SET status = ?, current_step = ?, detail_json = ? WHERE id = ?",
            (status, current_step, json.dumps(detail, default=str), run_id),
        )

    def _finish_run(self, run_id: str, status: str, current_step: Optional[str], detail: Dict) -> None:
        self.db.execute(
            "UPDATE workflow_runs SET status = ?, current_step = ?, detail_json = ?, finished_at = ? WHERE id = ?",
            (status, current_step, json.dumps(detail, default=str), _utcnow().isoformat(), run_id),
        )
