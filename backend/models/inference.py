"""Dispatch inference to a registered backend. No fabricated provider output."""

from __future__ import annotations

from typing import Callable, Dict, Optional


def echo_handler(prompt: str, model=None, **kwargs) -> dict:
    """A local echo backend for tests and offline development."""
    text = prompt if prompt is not None else ""
    return {
        "response": text,
        "tokens": len(str(text).split()),
        "backend": "echo",
        "model_id": (model or {}).get("id") if isinstance(model, dict) else None,
    }


class InferenceService:
    def __init__(self, model_registry, default_provider: Optional[str] = None):
        self.registry = model_registry
        self.default_provider = default_provider or _default_provider()
        self.handlers: Dict[str, Callable] = {}

    def register_handler(self, key: str, handler: Callable) -> None:
        """Bind a handler to a model id, provider name, or the alias 'echo'."""
        if not key or not callable(handler):
            raise ValueError("handler key and callable are required")
        self.handlers[key] = handler

    def call_model(self, model_id: str, prompt: str) -> dict:
        model = self.registry.get(model_id) if model_id else None
        handler = self._resolve_handler(model_id, model)
        if handler is None:
            return {
                "model_id": model_id,
                "prompt": prompt,
                "status": "not_configured",
                "error": (
                    "No inference backend is configured for this model. "
                    "No external LLM provider is wired; register a local handler "
                    "(for example echo_handler) or provide provider credentials later."
                ),
            }
        try:
            result = handler(prompt, model=model)
        except TypeError:
            result = handler(prompt)
        except Exception as exc:
            return {
                "model_id": model_id,
                "prompt": prompt,
                "status": "failed",
                "error": str(exc),
            }
        if not isinstance(result, dict):
            result = {"response": result}
        result.setdefault("model_id", model_id)
        result.setdefault("prompt", prompt)
        result.setdefault("status", "ok")
        return result

    def route(self, prompt: str, provider_preference: str = "local") -> dict:
        preferred = provider_preference or self.default_provider
        active = self.registry.get_active()
        selected = None
        if preferred:
            for model in active:
                if model.get("provider") == preferred:
                    selected = model
                    break
        if selected is None and active:
            selected = active[0]
        if selected is None:
            return {
                "prompt": prompt,
                "status": "not_configured",
                "error": "No active models are registered",
            }
        return self.call_model(selected["id"], prompt)

    def _resolve_handler(self, model_id: Optional[str], model: Optional[Dict]) -> Optional[Callable]:
        if model_id and model_id in self.handlers:
            return self.handlers[model_id]
        if model:
            provider = model.get("provider")
            if provider and provider in self.handlers:
                return self.handlers[provider]
        return None


def _default_provider() -> str:
    try:
        from backend.config import Config
        return Config().get("models.default_provider") or "local"
    except Exception:
        return "local"
