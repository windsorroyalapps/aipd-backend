
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db import AIPDDatabase
from backend.auth.identity import IdentityProvider
from backend.auth.permissions import PermissionEngine
from backend.orchestration.router import TaskRouter
from backend.orchestration.workflow import WorkflowEngine
from backend.control.plane import ControlPlane
from backend.control.config import ConfigController
from backend.tools.registry import ToolRegistry
from backend.approvals.gates import ApprovalGate
from backend.approvals.escalation import EscalationEngine
from backend.intelligence.engine import IntelligenceEngine
from backend.models.inference import InferenceService
from backend.models.registry import ModelRegistry
from backend.knowledge.graph import KnowledgeGraph
from backend.extraction.pipeline import ExtractionPipeline
from backend.extraction.schemas import SchemaLibrary
from backend.evaluation.benchmarks import BenchmarkRunner
from backend.audit.audit import AuditLog
from backend.audit.versions import VersionTracker
from backend.boundaries.boundaries import DataBoundary

app = FastAPI(title="AIPD Backend", version="1.0.0")
DB = AIPDDatabase()
IDP = IdentityProvider(DB)
PERM = PermissionEngine(DB)
ROUTER = TaskRouter(DB)
ENGINE = WorkflowEngine(DB, ROUTER)
PLANE = ControlPlane(DB, {"data": {}})
CONFIG_CTRL = ConfigController({"data": {}})
REGISTRY = ToolRegistry()
APPROVAL_GATE = ApprovalGate(DB)
ESCALATION = EscalationEngine(DB)
SCHEMA_LIB = SchemaLibrary()
SCHEMA_LIB.register("clinical_summary", ["trial_id", "phase", "enrollment", "primary_endpoint"])
EXTRACTION = ExtractionPipeline(SCHEMA_LIB.schemas)
KG = KnowledgeGraph(DB)
MODEL_REG = ModelRegistry(DB)
INFERENCE = InferenceService(MODEL_REG)
INTELLIGENCE = IntelligenceEngine(DB, MODEL_REG)
BENCHMARK = BenchmarkRunner()
AUDIT_LOG_OBJ = AuditLog(DB)
VERSION_TRACKER = VersionTracker(DB)
BOUNDARY = DataBoundary(isolation_level="project")

# Register a demo tool
REGISTRY.register("demo_extract", lambda text: EXTRACTION.run_full_pipeline(text, "clinical_summary")(text, "clinical_summary"), "Demo extraction")

class LoginRequest(BaseModel):
    email: str
    provider: str = "local"

class WorkflowRequest(BaseModel):
    name: str
    owner_email: str
    definition: dict

@app.post("/auth/login")
def login(req: LoginRequest):
    user = DB.get_user_by_email(req.email)
    if not user:
        # Auto-register if missing for demo
        user_id = DB.create_user(req.email, "Auto User", req.provider)
        user = DB.get_user_by_id(user_id)
    token = IDP.login(req.email, req.provider)
    if not token:
        raise HTTPException(status_code=401, detail="Login failed")
    DB.audit("auth.login", user["id"], "user", user["id"])
    return {"token": token, "user_id": user["id"]}

@app.get("/auth/me")
def me(request: Request):
    auth_header = request.headers.get("authorization", "")
    token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else auth_header
    session = IDP.verify_token(token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid token")
    return session

@app.post("/workflows")
def create_workflow(req: WorkflowRequest):
    user = DB.get_user_by_email(req.owner_email) if DB.get_user_by_email(req.owner_email) else DB.get_user_by_email(req.owner_email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    wf_id = ENGINE.start_workflow(req.name, user["id"], req.definition)
    return {"workflow_id": wf_id}

@app.get("/workflows/{wf_id}/tasks")
def list_tasks(wf_id: str):
    tasks = DB.execute("SELECT * FROM tasks WHERE workflow_id = ?", (wf_id,), fetch=True)
    return {"workflow_id": wf_id, "tasks": tasks}

@app.post("/tasks/{task_id}/route")
def route_task(task_id: str, payload: dict):
    return {"task_id": task_id, "routed": True, "payload": payload}

@app.post("/tasks/{task_id}/approve")
def approve_task(task_id: str, approver_email: str, notes: str = ""):
    user = DB.get_user_by_email(approver_email)
    if not user:
        raise HTTPException(status_code=404, detail="Approver not found")
    approval = DB.get_approval_by_task(task_id)
    if not approval:
        approval_id = APPROVAL_GATE.request_approval(task_id, user["id"], notes)
        DB.audit("approval.requested", user["id"], "approval", approval_id)
        return {"approval_id": approval_id, "status": "requested"}
    APPROVAL_GATE.approve(approval["id"], notes)
    DB.audit("approval.approved", user["id"], "approval", approval["id"])
    return {"approval_id": approval["id"], "status": "approved"}

@app.post("/tasks/{task_id}/escalate")
def escalate_task(task_id: str, reason: str):
    ESCALATION.escalate(task_id, reason)
    DB.audit("escalation.triggered", "system", "escalation", task_id, '{"reason":"' + reason + '"}')
    return {"task_id": task_id, "escalated": True}

@app.get("/intelligence/analyze")
def analyze(query: str):
    result = INTELLIGENCE.analyze_context(query)
    return result

@app.get("/models")
def list_models():
    return {"models": MODEL_REG.get_all()}

@app.post("/extraction/run")
def run_extraction(text: str, schema_id: str = "clinical_summary"):
    result = EXTRACTION.run_full_pipeline(text, schema_id)
    return result

@app.get("/knowledge/graph")
def get_knowledge():
    return {"graph_stats": KG.get_stats(), "nodes": len(KG.graph.nodes())}

@app.get("/evaluation/benchmark")
def run_demo_benchmark():
    def dummy():
        return "ok"
    result = BENCHMARK.run_benchmark("demo_benchmark", dummy)
    return result

@app.get("/control/health")
def health():
    return PLANE.health_check()

@app.get("/audit/events")
def audit_events(resource_type: str = None, resource_id: str = None):
    if resource_type and resource_id:
        events = AUDIT_LOG_OBJ.get_events_for_resource(resource_type, resource_id)
    else:
        events = AUDIT_LOG_OBJ.query()
    return {"events": events}

@app.get("/versions/history")
def version_history(artifact_type: str, artifact_id: str):
    return {"history": VERSION_TRACKER.get_history(artifact_type, artifact_id)}

@app.get("/permissions/check")
def check_permission(user_email: str, resource: str, action: str):
    user = DB.get_user_by_email(user_email)
    if not user: return {"allowed": False, "reason": "user_not_found"}
    allowed = PERM.check(user["id"], resource, action)
    DB.audit("permission.check", user["id"], "permission", resource, '{"action":"' + action + '","allowed":' + ("true" if allowed else "false") + '}')
    return {"allowed": allowed}

@app.get("/boundaries/enforce")
def enforce_boundary(user_email: str, resource: str, boundary_id: str = "default"):
    user = DB.get_user_by_email(user_email)
    try:
        BOUNDARY.enforce(user["id"], resource, boundary_id)
        return {"enforced": True, "allowed": True}
    except PermissionError:
        DB.audit("boundary.violation", user["id"], "boundary", boundary_id)
        return {"allowed": False, "enforced": False}

@app.get("/")
def root():
    return {"service": "AIPD Backend", "version": "1.0.0", "status": "running", "capabilities": [
        "logons", "orchestration", "task_routing", "tool_use", "human_approval",
        "escalation", "intelligence", "model_services", "structured_extraction",
        "knowledge_graphs", "evaluation", "control_plane", "identity",
        "permissions", "data_boundaries", "version_audit"
    ]}
