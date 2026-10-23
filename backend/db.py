
import sqlite3, os, json, datetime, hashlib, secrets, uuid
from typing import List, Dict, Optional, Any, Tuple

DB_PATH_DEFAULT = "data/aipd.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    display_name TEXT,
    identity_provider TEXT,
    role TEXT DEFAULT 'user',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id),
    token_hash TEXT UNIQUE,
    expires_at TEXT,
    revoked INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,
    name TEXT,
    status TEXT DEFAULT 'pending',
    owner_id TEXT REFERENCES users(id),
    definition_json TEXT,
    priority INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    workflow_id TEXT REFERENCES workflows(id),
    agent_id TEXT,
    status TEXT DEFAULT 'backlog',
    priority INTEGER DEFAULT 1,
    payload_json TEXT,
    result_json TEXT,
    attempts INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id),
    approver_id TEXT REFERENCES users(id),
    status TEXT DEFAULT 'pending',
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT,
    actor_id TEXT,
    resource_type TEXT,
    resource_id TEXT,
    details_json TEXT,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tasks_workflow ON tasks(workflow_id);
CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(agent_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_type TEXT,
    artifact_id TEXT,
    version_tag TEXT,
    content_hash TEXT,
    created_by TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_versions_artifact ON versions(artifact_type, artifact_id);
CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT REFERENCES users(id),
    role_text TEXT,
    resource TEXT,
    action TEXT,
    granted INTEGER DEFAULT 1,
    granted_by TEXT,
    granted_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS models (
    id TEXT PRIMARY KEY,
    name TEXT,
    version TEXT,
    provider TEXT,
    endpoint_url TEXT,
    capabilities_json TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

class AIPDDatabase:
    def __init__(self, db_path: str = DB_PATH_DEFAULT):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
        self.init()

    def init(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        conn.close()

    def connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def execute(self, sql: str, params: Tuple = (), fetch: bool = False):
        conn = self.connection()
        try:
            cursor = conn.execute(sql, params)
            if fetch:
                result = [dict(r) for r in cursor.fetchall()]
            else:
                result = cursor.rowcount
            conn.commit()
            return result
        finally:
            conn.close()

    def create_user(self, email: str, display_name: str = "", identity_provider: str = "local", role: str = "user") -> str:
        user_id = str(uuid.uuid4())
        self.execute("INSERT INTO users (id, email, display_name, identity_provider, role) VALUES (?, ?, ?, ?, ?)", (user_id, email, display_name, identity_provider, role))
        return user_id

    def get_user_by_email(self, email: str) -> Optional[Dict]:
        result = self.execute("SELECT * FROM users WHERE email = ?", (email,), fetch=True)
        return result[0] if result else None

    def get_user_by_id(self, user_id: str) -> Optional[Dict]:
        result = self.execute("SELECT * FROM users WHERE id = ?", (user_id,), fetch=True)
        return result[0] if result else None

    def create_session(self, user_id: str, token_hash: str, expires_at: str) -> str:
        session_id = str(uuid.uuid4())
        self.execute("INSERT INTO sessions (id, user_id, token_hash, expires_at) VALUES (?, ?, ?, ?)", (session_id, user_id, token_hash, expires_at))
        return session_id

    def get_session_by_token_hash(self, token_hash: str) -> Optional[Dict]:
        result = self.execute("SELECT s.*, u.email, u.display_name, u.role FROM sessions s JOIN users u ON s.user_id = u.id WHERE s.token_hash = ? AND s.revoked = 0", (token_hash,), fetch=True)
        return result[0] if result else None

    def revoke_session(self, token_hash: str) -> int:
        return self.execute("UPDATE sessions SET revoked = 1 WHERE token_hash = ?", (token_hash,))

    def create_workflow(self, name: str, owner_id: str, definition_json: str, priority: int = 1) -> str:
        wf_id = str(uuid.uuid4())
        self.execute("INSERT INTO workflows (id, name, owner_id, definition_json, priority) VALUES (?, ?, ?, ?, ?)", (wf_id, name, owner_id, definition_json, priority))
        return wf_id

    def get_workflows(self, status: Optional[str] = None) -> List[Dict]:
        sql = "SELECT * FROM workflows"
        params = ()
        if status: sql += " WHERE status = ?"; params = (status,)
        return self.execute(sql, params, fetch=True)

    def create_task(self, workflow_id: str, agent_id: str, payload_json: str, priority: int = 1) -> str:
        task_id = str(uuid.uuid4())
        self.execute("INSERT INTO tasks (id, workflow_id, agent_id, status, payload_json, priority) VALUES (?, ?, ?, ?, ?, ?)", (task_id, workflow_id, agent_id, "backlog", payload_json, priority))
        return task_id

    def get_tasks_by_agent(self, agent_id: str, status: Optional[str] = None) -> List[Dict]:
        sql = "SELECT * FROM tasks WHERE agent_id = ?"
        params = (agent_id,)
        if status: sql += " AND status = ?"; params += (status,)
        return self.execute(sql, params, fetch=True)

    def update_task_status(self, task_id: str, status: str, result_json: Optional[str] = None):
        self.execute("UPDATE tasks SET status = ?, result_json = ?, updated_at = CURRENT_TIMESTAMP, attempts = attempts + 1 WHERE id = ?", (status, result_json, task_id))

    def create_approval_request(self, task_id: str, approver_id: str, notes: str = "") -> str:
        approval_id = str(uuid.uuid4())
        self.execute("INSERT INTO approvals (id, task_id, approver_id, status, notes) VALUES (?, ?, ?, ?, ?)", (approval_id, task_id, approver_id, "pending", notes))
        return approval_id

    def get_approval_by_task(self, task_id: str) -> Optional[Dict]:
        result = self.execute("SELECT * FROM approvals WHERE task_id = ?", (task_id,), fetch=True)
        return result[0] if result else None

    def approve_task(self, approval_id: str, notes: str = ""):
        self.execute("UPDATE approvals SET status = ?, notes = ? WHERE id = ?", ("approved", notes, approval_id))

    def reject_task(self, approval_id: str, notes: str = ""):
        self.execute("UPDATE approvals SET status = ?, notes = ? WHERE id = ?", ("rejected", notes, approval_id))

    def audit(self, event_type: str, actor_id: str, resource_type: str, resource_id: str, details_json: str = "{}"):
        self.execute("INSERT INTO audit_log (event_type, actor_id, resource_type, resource_id, details_json) VALUES (?, ?, ?, ?, ?)", (event_type, actor_id, resource_type, resource_id, details_json))

    def record_version(self, artifact_type: str, artifact_id: str, version_tag: str, content_hash: str, created_by: str = "system"):
        self.execute("INSERT INTO versions (artifact_type, artifact_id, version_tag, content_hash, created_by) VALUES (?, ?, ?, ?, ?)", (artifact_type, artifact_id, version_tag, content_hash, created_by))

    def get_versions_for_artifact(self, artifact_type: str, artifact_id: str) -> List[Dict]:
        return self.execute("SELECT * FROM versions WHERE artifact_type = ? AND artifact_id = ? ORDER BY created_at DESC", (artifact_type, artifact_id), fetch=True)

    def grant_permission(self, user_id: str, role_text: str, resource: str, action: str, granted_by: str = "system"):
        self.execute("INSERT INTO permissions (user_id, role_text, resource, action, granted_by) VALUES (?, ?, ?, ?, ?)", (user_id, role_text, resource, action, granted_by))

    def check_permission(self, user_id: str, resource: str, action: str) -> bool:
        result = self.execute("SELECT * FROM permissions WHERE user_id = ? AND resource = ? AND action = ? AND granted = 1", (user_id, resource, action), fetch=True)
        return len(result) > 0

    def register_model(self, model_id: str, name: str, version: str, provider: str, endpoint_url: str = "", capabilities_json: str = "{}"):
        self.execute("INSERT INTO models (id, name, version, provider, endpoint_url, capabilities_json, status) VALUES (?, ?, ?, ?, ?, ?, ?)", (model_id, name, version, provider, endpoint_url, capabilities_json, "active"))

    def get_models(self, status: str = "active") -> List[Dict]:
        return self.execute("SELECT * FROM models WHERE status = ?", (status,), fetch=True)

if __name__ == "__main__":
    db = AIPDDatabase()
    print("DB initialized at:", db.db_path)
    u = db.create_user("test@aipharmadevelopments.com", "Test User")
    print("Created user:", u)
    w = db.create_workflow("Demo Workflow", u, '{"steps":["extract","evaluate"]}')
    print("Created workflow:", w)
    t = db.create_task(w, "agent-001", '{"input":"clinical trial summary"}')
    print("Created task:", t)
    db.audit("task.created", u, "task", t)
    db.record_version("workflow", w, "v1.0.0", hashlib.md5(b"{}").hexdigest()[:16], u)
    print("Audit and version recorded.")
