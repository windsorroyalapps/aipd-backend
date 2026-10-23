
class InferenceService:
    def __init__(self, model_registry):
        self.registry = model_registry
    def call_model(self, model_id: str, prompt: str) -> dict:
        # Simulation of model inference
        return {"model_id": model_id, "prompt": prompt, "response": f"Simulated response from {model_id} to '{prompt[:50]}...'", "tokens": len(prompt.split())}
    def route(self, prompt: str, provider_preference: str = "local") -> dict:
        active_models = self.registry.get_active()
        selected = active_models[0] if active_models else {"id": "default", "name": "default-model", "version": "1.0"}
        return self.call_model(selected["id"], prompt)
