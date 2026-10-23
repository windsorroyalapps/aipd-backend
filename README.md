
# AIPD Backend — aipharmadevelopments

Complete backend for AI-Pharma-Developments (AIPD) with 7 core services and full capability set.

## Services (7 Core)
1. Authentication & Identity (`backend/auth/`)
2. Orchestration Engine (`backend/orchestration/`)
3. Task Routing (`backend/orchestration/router.py`)
4. Tool Use & Execution (`backend/tools/`)
5. Human Approval Gates (`backend/approvals/`)
6. Escalation (`backend/approvals/escalation.py`)
7. Intelligence Engine (`backend/intelligence/`)

## Extended Capabilities
- Model Services (`backend/models/`)
- Structured Extraction (`backend/extraction/`)
- Knowledge Graphs (`backend/knowledge/`)
- Evaluation / Benchmarks (`backend/evaluation/`)
- Control Plane (`backend/control/`)
- Identity (`backend/auth/`)
- Permissions (`backend/auth/permissions.py`)
- Data Boundaries (`backend/boundaries/`)
- Version Tracking (`backend/audit/versions.py`)
- Audit Logging (`backend/audit/`)

## Configuration
```yaml
host: 0.0.0.0
port: 8080
isolation_level: project
audit_all_crossings: true
```

## Database
SQLite at `data/aipd.db`. Schema includes: users, sessions, workflows, tasks, approvals, audit_log, versions, permissions, models.

## Usage
```bash
pip install -r requirements.txt
python scripts/demo_full.py
python -m unittest tests/test_all.py
# Run server
python -m uvicorn api.server:app --host 0.0.0.0 --port 8080
```

## Capabilities Verified
Logons, Orchestration, Task Routing, Tool Use, Human Approval, Escalation, Intelligence, Model Services, Structured Extraction, Knowledge Graphs, Evaluation, Control Plane, Identity, Permissions, Data Boundaries, Version & Audit Records.
