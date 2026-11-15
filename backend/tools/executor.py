"""Execute registered tools with validation, recording, and optional approval."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .registry import validate_arguments

INVOCATIONS_SQL = """
CREATE TABLE IF NOT EXISTS tool_invocations (
    id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    args_json TEXT,
    status TEXT,
    result_json TEXT,
    error TEXT,
    approval_id TEXT,
    started_at TEXT,
    finished_at TEXT
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ToolExecutor:
    def __init__(self, registry, sandbox, db=None, timeout: Optional[int] = None):
        self.registry = registry
        self.sandbox = sandbox
        self.db = db if db is not None else getattr(registry, "db", None)
        self.timeout = timeout
        if self.db is not None:
            self.db.execute(INVOCATIONS_SQL)

    def run_in_sandbox(self, name: str, args: list = None, kwargs: dict = None) -> dict:
        args = args or []
        kwargs = kwargs or {}
        return self._execute(name, args, kwargs, approval_id=None, require_approval=False)

    def invoke_subprocess(self, cmd: list, timeout: int = 30) -> dict:
        outcome = self.sandbox.execute(cmd, timeout=timeout, max_output=0)
        # Preserve the original keys; include status as extra context.
        return {
            "stdout": outcome.get("stdout", ""),
            "stderr": outcome.get("stderr", ""),
            "returncode": outcome.get("returncode", -1),
        }

    def invoke_with_approval(self, name: str, args: list = None, kwargs: dict = None, approval_id: str = None) -> dict:
        args = args or []
        kwargs = kwargs or {}
        if approval_id:
            decision = self._check_approval(approval_id)
            if decision is not None:
                recorded = {
                    "tool": name,
                    "status": "denied",
                    "error": decision,
                    "approval_id": approval_id,
                }
                self._record(name, args, kwargs, recorded, approval_id)
                return recorded
        return self._execute(name, args, kwargs, approval_id=approval_id, require_approval=False)

    def list_invocations(self, tool_name: Optional[str] = None) -> List[Dict]:
        if self.db is None:
            return []
        if tool_name:
            return self.db.execute(
                "SELECT * FROM tool_invocations WHERE tool_name = ? ORDER BY started_at ASC",
                (tool_name,),
                fetch=True,
            )
        return self.db.execute("SELECT * FROM tool_invocations ORDER BY started_at ASC", fetch=True)

    def _check_approval(self, approval_id: str) -> Optional[str]:
        if self.db is None:
            return "Cannot verify approval because no database is bound to the executor"
        rows = self.db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,), fetch=True)
        if not rows:
            return f"Approval {approval_id} was not found"
        status = rows[0].get("status")
        if status != "approved":
            return f"Approval {approval_id} is {status or 'pending'}, not approved"
        return None

    def _execute(self, name: str, args: list, kwargs: dict, approval_id: Optional[str], require_approval: bool) -> dict:
        del require_approval
        tool = self.registry.get(name)
        if not tool:
            result = {"error": f"Tool {name} not found", "status": "not_found"}
            self._record(name, args, kwargs, result, approval_id)
            return result
        if tool.get("func") is None:
            result = {
                "tool": name,
                "status": "no_handler",
                "error": "Tool is registered but has no callable handler",
            }
            self._record(name, args, kwargs, result, approval_id)
            return result
        problem = validate_arguments(tool.get("schema"), tuple(args), dict(kwargs))
        if problem:
            result = {"tool": name, "status": "invalid", "error": problem}
            self._record(name, args, kwargs, result, approval_id)
            return result
        outcome = self.sandbox.run_callable(tool["func"], args, kwargs, timeout=self.timeout)
        if outcome.get("status") == "ok":
            result = {"tool": name, "result": outcome.get("result"), "status": "ok"}
        elif outcome.get("status") == "timeout":
            result = {"tool": name, "status": "timeout", "error": outcome.get("error")}
        else:
            result = {"tool": name, "status": "failed", "error": outcome.get("error")}
        self._record(name, args, kwargs, result, approval_id)
        return result

    def _record(self, name: str, args: list, kwargs: dict, result: dict, approval_id: Optional[str]) -> None:
        if self.db is None:
            return
        payload = {"args": list(args or []), "kwargs": dict(kwargs or {})}
        result_payload = result.get("result")
        try:
            result_json = json.dumps(result_payload, default=str) if "result" in result else ""
        except TypeError:
            result_json = json.dumps(str(result_payload))
        self.db.execute(
            "INSERT INTO tool_invocations (id, tool_name, args_json, status, result_json, error, approval_id, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                name,
                json.dumps(payload),
                result.get("status") or "unknown",
                result_json,
                result.get("error"),
                approval_id,
                _utcnow(),
                _utcnow(),
            ),
        )
