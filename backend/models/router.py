"""Select a registered model by capability, name, or cost. No provider calls."""

from __future__ import annotations

from typing import Dict, List, Optional

from .registry import cost_score, parse_capabilities


class ModelRouter:
    def __init__(self, registry, inference_service):
        self.registry = registry
        self.inference = inference_service

    def select_model(
        self,
        task_capabilities: list,
        name: Optional[str] = None,
        model_id: Optional[str] = None,
        provider: Optional[str] = None,
        prefer_lowest_cost: bool = False,
    ) -> str:
        record = self.select_record(
            task_capabilities,
            name=name,
            model_id=model_id,
            provider=provider,
            prefer_lowest_cost=prefer_lowest_cost,
        )
        if record:
            return record["id"]
        return "default"

    def select_record(
        self,
        task_capabilities: list = None,
        name: Optional[str] = None,
        model_id: Optional[str] = None,
        provider: Optional[str] = None,
        prefer_lowest_cost: bool = False,
        status: str = "active",
    ) -> Optional[Dict]:
        """Return the chosen model row, or None if nothing matches."""
        if model_id:
            model = self.registry.get(model_id)
            if model and (status is None or model.get("status") == status):
                return model
            return None
        if name:
            model = self.registry.get_by_name(name)
            if model and (status is None or model.get("status") == status):
                return model
            return None

        models = self.registry.get_active() if status == "active" else self.registry.get_all()
        if provider:
            models = [m for m in models if m.get("provider") == provider]
        required = list(task_capabilities or [])
        if required:
            matched = [m for m in models if _supports_any(m, required)]
        else:
            matched = list(models)
        if not matched and required:
            # Preserve the original fallback: first active model when nothing matches.
            matched = list(models)
        if not matched:
            return None
        if prefer_lowest_cost:
            matched = sorted(matched, key=lambda m: (cost_score(m), m.get("created_at") or "", m["id"]))
        return matched[0]

    def route_inference(self, prompt: str, capabilities: list = None) -> dict:
        model_id = self.select_model(capabilities or [])
        return self.inference.call_model(model_id, prompt)


def _supports_any(model: Dict, required: List[str]) -> bool:
    caps = parse_capabilities(model)
    supported = caps.get("supported") or caps.get("capabilities") or []
    if isinstance(supported, str):
        supported = [supported]
    return any(item in supported for item in required)
