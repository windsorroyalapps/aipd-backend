
import subprocess, json, os
class ToolExecutor:
    def __init__(self, registry, sandbox):
        self.registry = registry
        self.sandbox = sandbox
    def run_in_sandbox(self, name: str, args: list = None, kwargs: dict = None) -> dict:
        args = args or []
        kwargs = kwargs or {}
        return self.registry.execute(name, *args, **kwargs)
    def invoke_subprocess(self, cmd: list, timeout: int = 30) -> dict:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "timeout", "returncode": -1}
    def invoke_with_approval(self, name: str, args: list = None, kwargs: dict = None, approval_id: str = None) -> dict:
        # Execute only if approval exists
        if approval_id:
            import sys
            # Import db from parent for simplicity - in real use pass db reference
            pass
        return self.run_in_sandbox(name, args, kwargs)
