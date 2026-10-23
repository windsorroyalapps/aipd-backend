
import unittest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backend.db import AIPDDatabase
from backend.auth.identity import IdentityProvider
from backend.auth.permissions import PermissionEngine
from backend.orchestration.router import TaskRouter
from backend.orchestration.workflow import WorkflowEngine
from backend.approvals.gates import ApprovalGate
from backend.approvals.escalation import EscalationEngine
from backend.knowledge.graph import KnowledgeGraph
from backend.extraction.pipeline import ExtractionPipeline
from backend.extraction.schemas import SchemaLibrary
from backend.audit.audit import AuditLog
from backend.audit.versions import VersionTracker
from backend.boundaries.boundaries import DataBoundary
from backend.evaluation.benchmarks import BenchmarkRunner
from backend.evaluation.metrics import MetricsCollector

class AIPDIntegrationTest(unittest.TestCase):
    def setUp(self):
        import uuid
        self.db_path = "tests/test_" + str(uuid.uuid4())[:8] + ".db"
        self.db = AIPDDatabase(self.db_path)
        self.user_id = self.db.create_user("test@test.com", "Test User", "local", "admin")

    def test_identity_and_permissions(self):
        idp = IdentityProvider(self.db)
        token = idp.login("test@test.com")
        self.assertIsNotNone(token)
        session = idp.verify_token(token)
        self.assertIsNotNone(session)
        perm = PermissionEngine(self.db)
        perm.grant(self.user_id, "workflows", "read", "system")
        self.assertTrue(perm.check(self.user_id, "workflows", "read"))

    def test_workflow_and_routing(self):
        router = TaskRouter(self.db)
        engine = WorkflowEngine(self.db, router)
        wf_id = engine.start_workflow("Test Workflow", self.user_id, {"steps": ["test"]})
        self.assertIsNotNone(wf_id)
        task_id = router.route_task(wf_id, "agent-001", {"input": "test"})
        self.assertIsNotNone(task_id)
        claimed = router.claim_task("agent-001")
        self.assertEqual(claimed, task_id)

    def test_approvals_and_escallation(self):
        router = TaskRouter(self.db)
        task_id = router.route_task("wf-123", "agent-001", {})
        gate = ApprovalGate(self.db)
        approval_id = gate.request_approval(task_id, self.user_id)
        self.assertIsNotNone(approval_id)
        gate.approve(approval_id)
        approval = self.db.get_approval_by_task(task_id)
        self.assertEqual(approval["status"], "approved")
        escalation = EscalationEngine(self.db)
        escalation.escalate(task_id, "urgent")

    def test_knowledge_graph(self):
        kg = KnowledgeGraph(self.db, "tests/test_kg.json")
        kg.add_entity("e1", "Entity", {"label": "A"})
        kg.add_entity("e2", "Entity", {"label": "B"})
        kg.add_relation("e1", "e2", "related")
        kg.save()
        self.assertGreater(kg.get_stats()["nodes"], 0)

    def test_extraction_and_evaluation(self):
        schema_lib = SchemaLibrary()
        schema_lib.register("test", ["field"])
        extraction = ExtractionPipeline(schema_lib.schemas)
        result = extraction.run_full_pipeline("hello", "test")
        self.assertTrue(result.get("valid"))
        benchmark = BenchmarkRunner()
        result_bench = benchmark.run_benchmark("test", lambda: "done")
        self.assertEqual(result_bench["status"], "passed")
        metrics = MetricsCollector()
        metrics.record("score", 1.0)
        self.assertGreater(metrics.get_average("score"), 0.9)

    def test_audit_and_versions(self):
        audit = AuditLog(self.db)
        audit.log("test.event", self.user_id, "test_type", "test_id")
        events = audit.get_events_for_resource("test_type", "test_id")
        self.assertGreater(len(events), 0)
        version_tracker = VersionTracker(self.db)
        version_tracker.create_version("test_artifact", "artifact_1", "v1", "hash123", self.user_id)
        versions = version_tracker.get_history("test_artifact", "artifact_1")
        self.assertGreaterEqual(len(versions), 1)

    def test_boundaries(self):
        boundary = DataBoundary(isolation_level="project")
        boundary.define_boundary("b1", ["resource"], [self.user_id])
        self.assertTrue(boundary.enforce(self.user_id, "resource", "b1"))

if __name__ == "__main__":
    unittest.main()
