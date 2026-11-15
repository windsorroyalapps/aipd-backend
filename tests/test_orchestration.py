import os
import tempfile
import unittest
from datetime import timedelta

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.db import AIPDDatabase
from backend.orchestration import OrchestrationEngine, Scheduler, TaskRouter, WorkflowEngine
from backend.orchestration.scheduler import _utcnow
from backend.orchestration.workflow import parse_steps


class OrchestrationTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix="aipd_orch_", suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        self.db = AIPDDatabase(self.db_path)
        self.owner_id = self.db.create_user("orch@example.com", "Orch User", "local", "user")
        self.router = TaskRouter(self.db, retry_attempts=2, timeout_seconds=2)
        self.workflows = WorkflowEngine(self.db, self.router)
        self.engine = OrchestrationEngine(self.db, self.router, workflow_engine=self.workflows)
        self.scheduler = Scheduler(self.db, self.engine)

    def tearDown(self):
        self.engine.stop()
        self.scheduler.stop()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_package_exports(self):
        self.assertIs(self.workflows.__class__, WorkflowEngine)
        self.assertIs(self.router.__class__, TaskRouter)

    def test_start_workflow_and_get_status(self):
        wf_id = self.workflows.start_workflow(
            "Trial Pipeline",
            self.owner_id,
            {"steps": ["extract", "review", "approve"], "priority": 2},
        )
        self.assertIsNotNone(wf_id)
        status = self.workflows.get_status(wf_id)
        self.assertEqual(status["workflow"]["name"], "Trial Pipeline")
        self.assertEqual(status["workflow"]["status"], "pending")
        self.assertEqual(status["workflow"]["priority"], 2)
        self.assertEqual(status["tasks"], [])
        self.assertEqual(status["progress"]["total"], 3)
        self.assertEqual(status["progress"]["completed"], 0)
        self.assertEqual(self.workflows.get_status("missing"), {})

    def test_advance_creates_steps_in_order_and_completes(self):
        wf_id = self.workflows.start_workflow(
            "Ordered",
            self.owner_id,
            {"steps": ["extract", "review"], "agent_id": "agent-001"},
        )
        first = self.workflows.advance(wf_id)
        self.assertEqual(first["status"], "created")
        first_id = first["task_id"]
        waiting = self.workflows.advance(wf_id)
        self.assertEqual(waiting["status"], "waiting")
        self.assertEqual(waiting["task_id"], first_id)
        self.assertEqual(self.workflows.get_workflow(wf_id)["status"], "running")

        self.assertTrue(self.router.complete_task(first_id, {"ok": True}))
        second = self.workflows.advance(wf_id)
        self.assertEqual(second["status"], "created")
        self.assertNotEqual(second["task_id"], first_id)
        self.assertTrue(self.router.complete_task(second["task_id"], {"ok": True}))
        done = self.workflows.advance(wf_id)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(self.workflows.get_workflow(wf_id)["status"], "completed")

    def test_existing_route_claim_complete_signatures(self):
        wf_id = self.workflows.start_workflow("Manual", self.owner_id, {"steps": ["test"]})
        task_id = self.router.route_task(wf_id, "agent-001", {"input": "test"})
        claimed = self.router.claim_task("agent-001")
        self.assertEqual(claimed, task_id)
        self.assertTrue(self.router.complete_task(task_id, {"output": "done"}))
        task = self.router.get_task(task_id)
        self.assertEqual(task["status"], "completed")
        self.assertIsNone(self.router.claim_task("agent-001"))

    def test_route_to_unknown_workflow_still_works(self):
        # test_all approvals path routes a task to a bare workflow id with no row.
        task_id = self.router.route_task("wf-123", "agent-001", {})
        self.assertIsNotNone(task_id)
        self.assertEqual(self.router.get_task(task_id)["workflow_id"], "wf-123")

    def test_claim_prefers_higher_priority(self):
        wf_id = self.workflows.start_workflow("Priority", self.owner_id, {})
        low = self.router.route_task(wf_id, "agent-001", {"input": "low", "priority": 1})
        high = self.router.route_task(wf_id, "agent-001", {"input": "high", "priority": 5})
        self.assertEqual(self.router.claim_task("agent-001"), high)
        self.assertEqual(self.router.claim_task("agent-001"), low)

    def test_release_and_fail_task(self):
        wf_id = self.workflows.start_workflow("Release", self.owner_id, {})
        task_id = self.router.route_task(wf_id, "agent-001", {"input": "x"})
        self.assertEqual(self.router.claim_task("agent-001"), task_id)
        self.assertTrue(self.router.release_task(task_id))
        self.assertEqual(self.router.get_task(task_id)["status"], "backlog")
        self.assertEqual(self.router.claim_task("agent-001"), task_id)
        self.assertTrue(self.router.fail_task(task_id, "boom"))
        self.assertEqual(self.router.get_task(task_id)["status"], "failed")
        self.assertFalse(self.router.complete_task(task_id, {"nope": True}))

    def test_cancel_blocks_advance(self):
        wf_id = self.workflows.start_workflow("Cancel me", self.owner_id, {"steps": ["extract"]})
        self.assertTrue(self.workflows.cancel(wf_id))
        result = self.workflows.advance(wf_id)
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNone(result.get("task_id"))

    def test_run_workflow_with_handlers(self):
        wf_id = self.workflows.start_workflow(
            "Handled",
            self.owner_id,
            {"steps": [
                {"name": "extract", "handler": "extract", "agent_id": "worker"},
                {"name": "review", "handler": "review", "agent_id": "worker"},
            ]},
        )
        seen = []

        def extract(payload):
            seen.append(payload["step"])
            return {"text": "extracted"}

        def review(payload):
            seen.append(payload["step"])
            return {"ok": True}

        self.router.register_handler("extract", extract)
        self.router.register_handler("review", review)
        result = self.engine.run_workflow(wf_id)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(seen, ["extract", "review"])
        self.assertEqual(self.workflows.get_workflow(wf_id)["status"], "completed")
        run = self.engine.get_run(result["run_id"])
        self.assertEqual(run["status"], "completed")
        self.assertIsNotNone(run["finished_at"])

    def test_run_workflow_without_handler_does_not_fabricate_results(self):
        wf_id = self.workflows.start_workflow("Open", self.owner_id, {"steps": ["extract"]})
        result = self.engine.run_workflow(wf_id)
        self.assertEqual(result["status"], "waiting")
        self.assertIsNotNone(result["task_id"])
        task = self.router.get_task(result["task_id"])
        self.assertEqual(task["status"], "backlog")
        self.assertIsNone(task.get("result_json"))
        run = self.engine.get_run(result["run_id"])
        self.assertEqual(run["status"], "waiting")

    def test_handler_failure_fails_workflow(self):
        wf_id = self.workflows.start_workflow(
            "Broken",
            self.owner_id,
            {"steps": [{"name": "explode", "handler": "explode"}]},
        )

        def explode(payload):
            raise RuntimeError("handler exploded")

        self.router.register_handler("explode", explode)
        result = self.engine.run_workflow(wf_id)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.workflows.get_workflow(wf_id)["status"], "failed")
        task_id = result["result"]["task_id"]
        self.assertEqual(self.router.get_task(task_id)["status"], "failed")

    def test_handler_retries_then_succeeds(self):
        wf_id = self.workflows.start_workflow(
            "Retry",
            self.owner_id,
            {"steps": [{"name": "flaky", "handler": "flaky"}]},
        )
        calls = {"n": 0}

        def flaky(payload):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return {"attempt": calls["n"]}

        self.router.register_handler("flaky", flaky)
        result = self.engine.run_workflow(wf_id)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls["n"], 2)

    def test_submit_and_drain(self):
        wf_id = self.workflows.start_workflow(
            "Queued",
            self.owner_id,
            {"steps": [{"name": "extract", "handler": "extract"}]},
        )
        self.router.register_handler("extract", lambda payload: {"ok": True})
        self.engine.submit({"type": "workflow", "id": wf_id})
        results = self.engine.drain()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "completed")

    def test_scheduler_immediate_and_due(self):
        wf_id = self.workflows.start_workflow(
            "Scheduled",
            self.owner_id,
            {"steps": [{"name": "extract", "handler": "extract"}]},
        )
        self.router.register_handler("extract", lambda payload: {"ok": True})
        job_id = self.scheduler.schedule_workflow(wf_id, delay_seconds=0)
        self.assertTrue(job_id)
        drained = self.engine.drain()
        self.assertEqual(drained[0]["status"], "completed")
        jobs = self.scheduler.list_jobs("done")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], job_id)

        later = self.workflows.start_workflow(
            "Later",
            self.owner_id,
            {"steps": [{"name": "extract", "handler": "extract"}]},
        )
        future_id = self.scheduler.schedule_workflow(later, delay_seconds=3600)
        self.assertEqual(self.scheduler.due_jobs(), [])
        self.assertTrue(self.scheduler.cancel(future_id))
        self.assertFalse(self.scheduler.cancel(future_id))

        due_wf = self.workflows.start_workflow(
            "Due now",
            self.owner_id,
            {"steps": [{"name": "extract", "handler": "extract"}]},
        )
        due_id = self.scheduler.schedule_workflow(due_wf, delay_seconds=3600)
        self.db.execute(
            "UPDATE scheduled_jobs SET run_at = ? WHERE id = ?",
            ((_utcnow() - timedelta(seconds=5)).isoformat(), due_id),
        )
        submitted = self.scheduler.run_due()
        self.assertEqual(len(submitted), 1)
        self.assertEqual(self.engine.drain()[0]["status"], "completed")

    def test_parse_steps_rejects_bad_definition(self):
        with self.assertRaises(ValueError):
            parse_steps({"steps": [123]})

    def test_workflow_with_no_steps(self):
        wf_id = self.workflows.start_workflow("Empty", self.owner_id, {})
        result = self.engine.run_workflow(wf_id)
        self.assertEqual(result["status"], "no_steps")
        # Ad-hoc tasks can still be routed onto it.
        task_id = self.router.route_task(wf_id, "agent-001", {"input": "loose"})
        self.assertEqual(self.router.claim_task("agent-001"), task_id)


if __name__ == "__main__":
    unittest.main()
