
from fastapi import APIRouter
router = APIRouter()

# Routes module for separation of concerns
# The main server.py includes all endpoints; this provides additional modular routing.
@router.get("/routes/info")
def routes_info():
    return {"routes_module": "routes", "available": ["/auth", "/workflows", "/tasks", "/approval", "/escalation", "/intelligence", "/models", "/extraction", "/knowledge", "/evaluation", "/control", "/audit", "/versions", "/permissions", "/boundaries"]}
