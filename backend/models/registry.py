"""Register, look up, and list models stored in the existing models table."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def parse_capabilities(model: Optional[Dict]) -> Dict[str, Any]:
    if not model:
        return {}
    raw = model.get("capabilities_json") or model.get("capabilities") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def cost_score(model_or_caps) -> float:
    """Numeric cost for comparisons. Unknown cost sorts last when minimizing."""
    if isinstance(model_or_caps, dict) and (
        "capabilities_json" in model_or_caps or "provider" in model_or_caps
    ):
        caps = parse_capabilities(model_or_caps)
    elif isinstance(model_or_caps, dict):
        caps = model_or_caps
    else:
        caps = {}
    cost = caps.get("cost", caps.get("cost_per_1k"))
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        return float(cost)
    if isinstance(cost, dict):
        for key in ("input", "per_1k", "amount", "value"):
            if key in cost:
                try:
                    return float(cost[key])
                except (TypeError, ValueError):
                    return float("inf")
    return float("inf")


class ModelRegistry:
    def __init__(self, db):
        self.db = db

    def register(
        self,
        model_id: str,
        name: str,
        version: str,
        provider: str,
        endpoint: str = "",
        capabilities: dict = None,
        cost=None,
        limits=None,
    ) -> str:
        if not model_id:
            raise ValueError("model_id is required")
        caps = dict(capabilities or {})
        if cost is not None and "cost" not in caps:
            caps["cost"] = cost
        if limits is not None and "limits" not in caps:
            caps["limits"] = limits
        caps_json = json.dumps(caps)
        existing = self.db.execute("SELECT id FROM models WHERE id = ?", (model_id,), fetch=True)
        if existing:
            self.db.execute(
                "UPDATE models SET name = ?, version = ?, provider = ?, endpoint_url = ?, capabilities_json = ?, status = ? WHERE id = ?",
                (name, version, provider, endpoint, caps_json, "active", model_id),
            )
        else:
            self.db.register_model(model_id, name, version, provider, endpoint, caps_json)
        return model_id

    def get(self, model_id: str) -> Optional[Dict]:
        rows = self.db.execute("SELECT * FROM models WHERE id = ?", (model_id,), fetch=True)
        return rows[0] if rows else None

    def get_all(self) -> list:
        return self.db.execute("SELECT * FROM models ORDER BY created_at ASC, id ASC", fetch=True)

    def get_active(self) -> list:
        return self.db.get_models("active")

    def get_by_name(self, name: str) -> Optional[Dict]:
        rows = self.db.execute("SELECT * FROM models WHERE name = ? ORDER BY created_at ASC", (name,), fetch=True)
        return rows[0] if rows else None

    def update_status(self, model_id: str, status: str):
        self.db.execute("UPDATE models SET status = ? WHERE id = ?", (status, model_id))

    def capabilities(self, model_id: str) -> Dict[str, Any]:
        return parse_capabilities(self.get(model_id))
