
class ModelRouter:
    def __init__(self, registry, inference_service):
        self.registry = registry
        self.inference = inference_service
    def select_model(self, task_capabilities: list) -> str:
        for m in self.registry.get_active():
            caps = __import__('json').loads(m.get("capabilities_json") or "{}")
            if any(c in caps.get("supported", []) for c in task_capabilities):
                return m["id"]
        return self.registry.get_active()[0]["id"] if self.registry.get_active() else "default"
    def route_inference(self, prompt: str, capabilities: list = None) -> dict:
        model_id = self.select_model(capabilities or [])
        return self.inference.call_model(model_id, prompt)
