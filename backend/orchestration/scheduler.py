"""DB-backed, offline-testable workflow and task scheduling."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

JOBS_SQL = """
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    run_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    consumed_at TEXT
);
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class Scheduler:
    def __init__(self, db, engine):
        self.db = db
        self.engine = engine
        self.running = False
        self.thread = None
        self._ensure_table()

    def _ensure_table(self) -> None:
        self.db.execute(JOBS_SQL)

    def schedule_workflow(self, wf_id: str, delay_seconds: int = 0) -> str:
        return self._schedule("workflow", wf_id, delay_seconds)

    def schedule_task(self, task_id: str, delay_seconds: int = 0) -> str:
        return self._schedule("task", task_id, delay_seconds)

    def _schedule(self, job_type: str, target_id: str, delay_seconds: int = 0) -> str:
        job_id = str(uuid.uuid4())
        run_at = _utcnow() + timedelta(seconds=max(0, int(delay_seconds)))
        self.db.execute(
            "INSERT INTO scheduled_jobs (id, job_type, target_id, run_at, status) VALUES (?, ?, ?, ?, ?)",
            (job_id, job_type, target_id, run_at.isoformat(), "scheduled"),
        )
        self.db.audit("job.scheduled", "system", job_type, target_id)
        if delay_seconds <= 0:
            self.run_due()
        return job_id

    def due_jobs(self, now: Optional[datetime] = None) -> List[Dict]:
        now = now or _utcnow()
        rows = self.db.execute(
            "SELECT * FROM scheduled_jobs WHERE status = ? ORDER BY run_at ASC",
            ("scheduled",),
            fetch=True,
        )
        due = []
        for row in rows:
            try:
                if _parse_dt(row["run_at"]) <= now:
                    due.append(row)
            except (TypeError, ValueError):
                due.append(row)
        return due

    def run_due(self, now: Optional[datetime] = None) -> List[Dict]:
        """Dequeue due jobs and submit them to the orchestration engine."""
        submitted = []
        for job in self.due_jobs(now):
            count = self.db.execute(
                "UPDATE scheduled_jobs SET status = ?, consumed_at = ? WHERE id = ? AND status = ?",
                ("queued", _utcnow().isoformat(), job["id"], "scheduled"),
            )
            if not count:
                continue
            item = {"type": job["job_type"], "id": job["target_id"], "job_id": job["id"]}
            self.engine.submit(item)
            self.db.execute(
                "UPDATE scheduled_jobs SET status = ? WHERE id = ?",
                ("done", job["id"]),
            )
            submitted.append({**job, "status": "done"})
        return submitted

    def cancel(self, job_id: str) -> bool:
        count = self.db.execute(
            "UPDATE scheduled_jobs SET status = ? WHERE id = ? AND status = ?",
            ("cancelled", job_id, "scheduled"),
        )
        return count > 0

    def list_jobs(self, status: Optional[str] = None) -> List[Dict]:
        if status:
            return self.db.execute(
                "SELECT * FROM scheduled_jobs WHERE status = ? ORDER BY run_at ASC",
                (status,),
                fetch=True,
            )
        return self.db.execute("SELECT * FROM scheduled_jobs ORDER BY run_at ASC", fetch=True)

    def start(self, interval_seconds: float = 0.5) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.running = True

        def _loop():
            while self.running:
                self.run_due()
                threading.Event().wait(interval_seconds)

        self.thread = threading.Thread(target=_loop, daemon=True, name="aipd-scheduler")
        self.thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self.running = False
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=timeout)
