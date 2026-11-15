import os
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.db import AIPDDatabase
from backend.models import InferenceService, ModelRegistry, ModelRouter, echo_handler
from backend.models.registry import cost_score, parse_capabilities


class ModelsTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix="aipd_models_", suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        self.db = AIPDDatabase(self.db_path)
        self.registry = ModelRegistry(self.db)
        self.inference = InferenceService(self.registry)
        self.router = ModelRouter(self.registry, self.inference)

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _register_demo_models(self):
        self.registry.register(
            "gpt-4-model", "GPT-4", "v4.0", "openai",
            "https://api.openai.com/v1/chat/completions",
            {"supported": ["text", "reasoning"]},
        )
        self.registry.register(
            "local-llm", "Local LLM", "v1.0", "local", "",
            {"supported": ["text", "extraction"]},
        )

    def test_package_exports(self):
        self.assertIs(self.registry.__class__, ModelRegistry)
        self.assertIs(self.router.__class__, ModelRouter)
        self.assertIs(self.inference.__class__, InferenceService)

    def test_register_lookup_and_status(self):
        self._register_demo_models()
        model = self.registry.get("gpt-4-model")
        self.assertEqual(model["name"], "GPT-4")
        self.assertEqual(model["provider"], "openai")
        self.assertEqual(model["status"], "active")
        self.assertEqual(parse_capabilities(model)["supported"], ["text", "reasoning"])
        names = [m["name"] for m in self.registry.get_active()]
        self.assertEqual(names, ["GPT-4", "Local LLM"])
        self.registry.update_status("gpt-4-model", "inactive")
        self.assertEqual(self.registry.get("gpt-4-model")["status"], "inactive")
        self.assertEqual([m["id"] for m in self.registry.get_active()], ["local-llm"])
        self.assertEqual(len(self.registry.get_all()), 2)

    def test_register_same_model_twice_does_not_raise(self):
        self.registry.register("gpt-4-model", "GPT-4", "v4.0", "openai", "https://api.openai.com", {"supported": ["text"]})
        self.registry.register("gpt-4-model", "GPT-4 Turbo", "v4.1", "openai", "https://api.openai.com/v2", {"supported": ["text", "vision"], "cost": 0.5})
        model = self.registry.get("gpt-4-model")
        self.assertEqual(model["name"], "GPT-4 Turbo")
        self.assertEqual(model["version"], "v4.1")
        self.assertEqual(model["endpoint_url"], "https://api.openai.com/v2")
        self.assertEqual(parse_capabilities(model)["supported"], ["text", "vision"])
        self.assertEqual(cost_score(model), 0.5)
        self.assertEqual(len(self.registry.get_all()), 1)

    def test_register_reactivates_inactive_model(self):
        self.registry.register("local-llm", "Local LLM", "v1.0", "local", "")
        self.registry.update_status("local-llm", "inactive")
        self.registry.register("local-llm", "Local LLM", "v1.1", "local", "")
        self.assertEqual(self.registry.get("local-llm")["status"], "active")
        self.assertEqual(self.registry.get("local-llm")["version"], "v1.1")

    def test_cost_and_limits_metadata(self):
        self.registry.register(
            "cheap", "Cheap", "1", "local", "",
            {"supported": ["text"]}, cost=0.01, limits={"max_tokens": 1024},
        )
        caps = self.registry.capabilities("cheap")
        self.assertEqual(caps["cost"], 0.01)
        self.assertEqual(caps["limits"]["max_tokens"], 1024)

    def test_select_model_demo_capability_behavior(self):
        self._register_demo_models()
        # Same as scripts/demo_full.py: first model whose supported list intersects.
        self.assertEqual(self.router.select_model(["text", "extraction"]), "gpt-4-model")
        self.assertEqual(self.router.select_model(["extraction"]), "local-llm")
        self.assertEqual(self.router.select_model(["no-such-cap"]), "gpt-4-model")
        self.assertEqual(self.router.select_record(["extraction"])["name"], "Local LLM")

    def test_select_by_name_id_provider_and_cost(self):
        self.registry.register("dear", "Dear", "1", "cloud", "", {"supported": ["text"], "cost": 2.0})
        self.registry.register("cheap", "Cheap", "1", "local", "", {"supported": ["text"], "cost": 0.1})
        self.assertEqual(self.router.select_model(["text"], name="Cheap"), "cheap")
        self.assertEqual(self.router.select_model(["text"], model_id="dear"), "dear")
        self.assertEqual(self.router.select_model(["text"], provider="local"), "cheap")
        self.assertEqual(self.router.select_model(["text"], prefer_lowest_cost=True), "cheap")
        self.assertIsNone(self.router.select_record(["text"], name="missing"))

    def test_select_model_with_no_models_returns_default(self):
        self.assertEqual(self.router.select_model(["text"]), "default")
        self.assertIsNone(self.router.select_record(["text"]))

    def test_call_model_without_backend_is_not_fabricated(self):
        self._register_demo_models()
        result = self.inference.call_model("gpt-4-model", "Summarize trial protocol")
        self.assertEqual(result["status"], "not_configured")
        self.assertIn("No inference backend", result["error"])
        self.assertNotIn("response", result)
        routed = self.router.route_inference("Summarize trial protocol")
        self.assertEqual(routed["status"], "not_configured")
        self.assertIn("model_id", routed)
        self.assertIn("prompt", routed)

    def test_echo_handler_when_registered(self):
        self.registry.register("local-llm", "Local LLM", "v1.0", "local", "", {"supported": ["text"]})
        self.inference.register_handler("local", echo_handler)
        result = self.inference.call_model("local-llm", "hello world")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["response"], "hello world")
        self.assertEqual(result["tokens"], 2)
        self.assertEqual(result["backend"], "echo")

    def test_handler_bound_to_model_id_and_failure(self):
        self.registry.register("special", "Special", "1", "custom", "")
        self.inference.register_handler("special", lambda prompt, model=None: {"response": "ok-special"})
        self.assertEqual(self.inference.call_model("special", "q")["response"], "ok-special")

        def boom(prompt, model=None):
            raise RuntimeError("backend down")

        self.inference.register_handler("special", boom)
        failed = self.inference.call_model("special", "q")
        self.assertEqual(failed["status"], "failed")
        self.assertIn("backend down", failed["error"])
        self.assertNotIn("response", failed)

    def test_route_honors_provider_preference(self):
        self._register_demo_models()
        self.inference.register_handler("local", echo_handler)
        result = self.inference.route("ping", provider_preference="local")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["model_id"], "local-llm")
        unconfigured = self.inference.route("ping", provider_preference="openai")
        self.assertEqual(unconfigured["status"], "not_configured")

    def test_route_with_no_models(self):
        result = self.inference.route("ping")
        self.assertEqual(result["status"], "not_configured")


if __name__ == "__main__":
    unittest.main()
