import os
import tempfile
import time
import unittest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.db import AIPDDatabase
from backend.tools import Sandbox, ToolExecutor, ToolRegistry
from backend.tools.registry import validate_arguments


class ToolsTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix="aipd_tools_", suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        self.work_dir = tempfile.mkdtemp(prefix="aipd_sandbox_")
        self.db = AIPDDatabase(self.db_path)
        self.registry = ToolRegistry(self.db)
        self.sandbox = Sandbox(work_dir=self.work_dir, timeout=2, max_output=500)
        self.executor = ToolExecutor(self.registry, self.sandbox, db=self.db, timeout=2)

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass
        for root, dirs, files in os.walk(self.work_dir, topdown=False):
            for name in files:
                try:
                    os.unlink(os.path.join(root, name))
                except OSError:
                    pass
            for name in dirs:
                try:
                    os.rmdir(os.path.join(root, name))
                except OSError:
                    pass
        try:
            os.rmdir(self.work_dir)
        except OSError:
            pass

    def test_package_exports(self):
        self.assertIs(self.registry.__class__, ToolRegistry)
        self.assertIs(self.executor.__class__, ToolExecutor)
        self.assertIs(self.sandbox.__class__, Sandbox)

    def test_register_list_get_execute(self):
        self.registry.register("echo", lambda text: f"echo:{text}", "Echo tool", "1.2.0")
        entry = self.registry.get("echo")
        self.assertEqual(entry["desc"], "Echo tool")
        self.assertEqual(entry["version"], "1.2.0")
        listed = self.registry.list()
        self.assertTrue(any(item["name"] == "echo" and item["desc"] == "Echo tool" for item in listed))
        result = self.registry.execute("echo", "hello")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], "echo:hello")
        self.assertEqual(result["tool"], "echo")

    def test_execute_missing_and_no_handler(self):
        missing = self.registry.execute("nope")
        self.assertIn("not found", missing["error"])
        self.assertEqual(missing["status"], "not_found")
        self.registry.register("planned", None, "Definition only")
        outcome = self.registry.execute("planned")
        self.assertEqual(outcome["status"], "no_handler")
        self.assertIn("no callable handler", outcome["error"])

    def test_execute_failure_is_not_fabricated_success(self):
        def boom(text):
            raise RuntimeError("tool broke")
        self.registry.register("boom", boom)
        result = self.registry.execute("boom", "x")
        self.assertEqual(result["status"], "failed")
        self.assertIn("tool broke", result["error"])
        self.assertNotIn("result", result)

    def test_schema_validation(self):
        schema = {
            "arguments": [
                {"name": "text", "type": "string", "required": True},
                {"name": "count", "type": "integer", "required": False, "default": 1},
            ],
            "additional_properties": False,
        }
        self.registry.register("repeat", lambda text, count=1: text * count, schema=schema)
        self.assertEqual(self.registry.execute("repeat", "ab", count=2)["result"], "abab")
        invalid = self.registry.execute("repeat", 10)
        self.assertEqual(invalid["status"], "invalid")
        missing = self.registry.execute("repeat")
        self.assertEqual(missing["status"], "invalid")
        extra = self.registry.execute("repeat", "ab", unexpected=True)
        self.assertEqual(extra["status"], "invalid")
        self.assertIsNone(validate_arguments(schema, ("ok",), {}))

    def test_definitions_persist_without_callables(self):
        schema = {"arguments": [{"name": "text", "type": "string"}]}
        self.registry.register("stored", None, "Stored tool", "2.0.0", schema=schema)
        again = ToolRegistry(self.db)
        entry = again.get("stored")
        self.assertIsNotNone(entry)
        self.assertIsNone(entry["func"])
        self.assertEqual(entry["desc"], "Stored tool")
        self.assertEqual(entry["version"], "2.0.0")
        self.assertEqual(entry["schema"]["arguments"][0]["name"], "text")
        self.assertEqual(again.execute("stored", "hi")["status"], "no_handler")
        again.register("stored", lambda text: text.upper(), "Stored tool", "2.0.0", schema=schema)
        self.assertEqual(again.execute("stored", "hi")["result"], "HI")

    def test_executor_records_invocations(self):
        self.registry.register("upper", lambda text: text.upper())
        result = self.executor.run_in_sandbox("upper", args=["abc"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], "ABC")
        rows = self.executor.list_invocations("upper")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "ok")
        self.assertIn("abc", rows[0]["args_json"])

    def test_executor_no_handler_and_not_found(self):
        self.registry.register("ghost", None)
        self.assertEqual(self.executor.run_in_sandbox("ghost")["status"], "no_handler")
        self.assertEqual(self.executor.run_in_sandbox("missing")["status"], "not_found")
        self.assertGreaterEqual(len(self.executor.list_invocations()), 2)

    def test_invoke_with_approval(self):
        self.registry.register("sensitive", lambda: "secret-result")
        denied_missing = self.executor.invoke_with_approval("sensitive", approval_id="nope")
        self.assertEqual(denied_missing["status"], "denied")
        self.assertNotIn("result", denied_missing)

        user_id = self.db.create_user("approver@example.com", "Approver")
        pending_id = self.db.create_approval_request("task-1", user_id, "wait")
        denied_pending = self.executor.invoke_with_approval("sensitive", approval_id=pending_id)
        self.assertEqual(denied_pending["status"], "denied")

        self.db.approve_task(pending_id, "ok")
        allowed = self.executor.invoke_with_approval("sensitive", approval_id=pending_id)
        self.assertEqual(allowed["status"], "ok")
        self.assertEqual(allowed["result"], "secret-result")

        # No approval id keeps the previous always-run behaviour.
        unchecked = self.executor.invoke_with_approval("sensitive")
        self.assertEqual(unchecked["status"], "ok")

    def test_invoke_subprocess_and_timeout(self):
        echoed = self.executor.invoke_subprocess(["/bin/echo", "hello-tools"], timeout=5)
        self.assertEqual(echoed["returncode"], 0)
        self.assertIn("hello-tools", echoed["stdout"])
        timed = self.executor.invoke_subprocess(["/bin/sleep", "10"], timeout=1)
        self.assertEqual(timed["returncode"], -1)
        self.assertIn("timeout", timed["stderr"])

    def test_sandbox_trims_output_and_env(self):
        huge = self.sandbox.execute(["/usr/bin/python3", "-c", "print('x'*2000)"], max_output=50)
        self.assertEqual(len(huge["stdout"]), 50)
        self.assertTrue(huge["truncated"])
        leaked = self.sandbox.execute(
            ["/usr/bin/python3", "-c", "import os; print(os.environ.get('SECRET_TOKEN', 'missing'))"],
            env={"SECRET_TOKEN": "should-not-pass", "AIPD_OK": "yes", "PATH": "/tmp"},
        )
        self.assertIn("missing", leaked["stdout"])
        ok = self.sandbox.execute(
            ["/usr/bin/python3", "-c", "import os; print(os.environ.get('AIPD_OK', 'missing'))"],
            env={"AIPD_OK": "yes"},
        )
        self.assertIn("yes", ok["stdout"])
        self.assertEqual(ok["status"], "ok")

    def test_create_isolated_script_stays_in_work_dir(self):
        path = self.sandbox.create_isolated_script("result = 1\n")
        self.assertTrue(path.startswith(self.work_dir))
        self.assertTrue(os.path.isfile(path))

    def test_run_python_snippet_and_blocked_import(self):
        ok = self.sandbox.run_python("result = 21 * 2")
        self.assertEqual(ok["status"], "ok")
        self.assertEqual(ok["result"], 42)
        blocked = self.sandbox.run_python("import os\nresult = os.getcwd()")
        self.assertEqual(blocked["status"], "failed")
        self.assertIn("not allowed", blocked["stderr"])

    def test_run_python_timeout(self):
        started = time.time()
        outcome = self.sandbox.run_python("while True:\n    pass", timeout=1)
        elapsed = time.time() - started
        self.assertEqual(outcome["status"], "timeout")
        self.assertLess(elapsed, 5)

    def test_invalid_command_is_rejected(self):
        bad = self.sandbox.execute("echo hi")
        self.assertEqual(bad["status"], "invalid")
        self.assertEqual(bad["returncode"], -1)

    def test_unregister(self):
        self.registry.register("temp", lambda: 1)
        self.assertTrue(self.registry.unregister("temp"))
        self.assertFalse(self.registry.unregister("temp"))
        self.assertEqual(self.registry.execute("temp")["status"], "not_found")

    def test_demo_compatible_execute_shape(self):
        self.registry.register("demo_extraction", lambda text: {"valid": True, "data": {"text_preview": text[:20]}}, "Demo")
        result = self.registry.execute("demo_extraction", "Phase II trial")
        self.assertEqual(result.get("status"), "ok")
        self.assertTrue(result.get("result", {}).get("valid"))
        self.assertIn("data", result["result"])


if __name__ == "__main__":
    unittest.main()
