"""Conservative process wrapper for tool commands and short Python snippets.

This is not a security boundary against hostile code. It applies timeouts,
output caps, a trimmed environment, a work-directory cwd, and (on POSIX)
resource limits. It does not use a container, seccomp, a network namespace,
or a complete interpreter lockdown. Restricted builtins in run_python() are
a convenience and can be bypassed by determined Python.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Callable, Dict, List, Optional

DEFAULT_TIMEOUT = 30
DEFAULT_MAX_OUTPUT = 500
DEFAULT_MAX_ARG_BYTES = 64 * 1024
DEFAULT_MAX_CPU_SECONDS = 5
DEFAULT_MAX_MEMORY_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 8 * 1024 * 1024
ALLOWED_PATH = "/usr/bin:/bin"
SAFE_ENV_KEYS = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM"}

# Builtins offered to run_python() snippets. This list is intentionally small
# and is not sufficient to make exec() safe against a motivated attacker.
SAFE_BUILTINS = {
    "abs", "all", "any", "bool", "dict", "enumerate", "float", "int",
    "isinstance", "len", "list", "max", "min", "print", "range", "repr",
    "sorted", "str", "sum", "tuple", "zip", "Exception", "ValueError",
    "TypeError", "KeyError", "RuntimeError", "True", "False", "None",
}
SAFE_IMPORTS = {"math", "json", "re", "datetime", "decimal", "fractions", "statistics"}


class Sandbox:
    def __init__(
        self,
        work_dir: str = "/tmp/aipd_sandbox",
        timeout: int = DEFAULT_TIMEOUT,
        max_output: int = DEFAULT_MAX_OUTPUT,
        max_cpu_seconds: int = DEFAULT_MAX_CPU_SECONDS,
        max_memory_bytes: int = DEFAULT_MAX_MEMORY_BYTES,
    ):
        self.work_dir = os.path.abspath(work_dir)
        self.timeout = timeout
        self.max_output = max_output
        self.max_cpu_seconds = max_cpu_seconds
        self.max_memory_bytes = max_memory_bytes
        os.makedirs(self.work_dir, exist_ok=True)

    def execute(self, cmd: list, env: dict = None, timeout: Optional[int] = None, max_output: Optional[int] = None) -> dict:
        check = _validate_cmd(cmd)
        if check:
            return {"stdout": "", "stderr": check, "returncode": -1, "status": "invalid"}
        timeout = self.timeout if timeout is None else timeout
        cap = self.max_output if max_output is None else max_output
        if cap is not None and cap <= 0:
            cap = None
        restricted_env = self._build_env(env)
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=self.work_dir,
                env=restricted_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
                preexec_fn=self._preexec_limits,
            )
        except Exception as exc:
            return {"stdout": "", "stderr": str(exc), "returncode": -1, "status": "failed"}
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            code = proc.returncode
            status = "ok" if code == 0 else "failed"
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            stdout, stderr = proc.communicate()
            stdout = stdout or ""
            stderr = (stderr or "") + "timeout"
            code = -1
            status = "timeout"
        except Exception as exc:
            _kill_group(proc)
            return {"stdout": "", "stderr": str(exc), "returncode": -1, "status": "failed"}
        return {
            "stdout": _clip(stdout, cap),
            "stderr": _clip(stderr, cap),
            "returncode": code,
            "status": status,
            "truncated": _truncated(stdout, stderr, cap),
        }

    def create_isolated_script(self, content: str) -> str:
        os.makedirs(self.work_dir, exist_ok=True)
        fd, path = tempfile.mkstemp(dir=self.work_dir, suffix=".py")
        with os.fdopen(fd, "w") as handle:
            handle.write(content or "")
        os.chmod(path, 0o600)
        return path

    def run_python(self, source: str, timeout: Optional[int] = None) -> dict:
        """Run a short Python snippet in a subprocess with limited builtins.

        The snippet may set a ``result`` variable, which is returned on success.
        This is not a secure interpreter jail.
        """
        if source is None:
            return {"stdout": "", "stderr": "source is required", "returncode": -1, "status": "invalid"}
        user_path = self.create_isolated_script(source)
        runner_path = self.create_isolated_script(_python_runner_source(user_path))
        outcome = self.execute([sys.executable, runner_path], timeout=timeout)
        parsed = _parse_runner_output(outcome.get("stdout") or "")
        outcome["result"] = parsed.get("result")
        if parsed.get("error") and outcome.get("status") != "timeout":
            outcome["status"] = "failed"
            if not outcome.get("stderr"):
                outcome["stderr"] = parsed["error"]
        return outcome

    def run_callable(self, func: Callable, args: list = None, kwargs: dict = None, timeout: Optional[int] = None) -> dict:
        """Best-effort timeout around an in-process callable.

        Python cannot reliably kill a thread that ignores cancellation, so a
        stuck handler may keep running after this returns a timeout status.
        """
        args = args or []
        kwargs = kwargs or {}
        timeout = self.timeout if timeout is None else timeout
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(func, *args, **kwargs)
            try:
                result = future.result(timeout=timeout)
                return {"status": "ok", "result": result}
            except FuturesTimeout:
                future.cancel()
                return {"status": "timeout", "error": f"Callable exceeded {timeout}s"}
            except Exception as exc:
                return {"status": "failed", "error": str(exc)}

    def _build_env(self, env: Optional[dict]) -> Dict[str, str]:
        source = env if env is not None else {}
        restricted = {}
        for key, value in source.items():
            if key in SAFE_ENV_KEYS or key.startswith("AIPD_"):
                restricted[str(key)] = str(value)
        restricted["PATH"] = ALLOWED_PATH
        restricted.setdefault("HOME", self.work_dir)
        restricted.setdefault("LANG", "C")
        return restricted

    def _preexec_limits(self) -> None:
        try:
            import resource
            cpu = max(1, int(self.max_cpu_seconds))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
            mem = int(self.max_memory_bytes)
            if mem > 0:
                resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
            resource.setrlimit(resource.RLIMIT_FSIZE, (DEFAULT_MAX_FILE_BYTES, DEFAULT_MAX_FILE_BYTES))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
        except (ValueError, OSError, ImportError):
            # Limits are best-effort; the timeout still applies.
            return


def _validate_cmd(cmd: list) -> Optional[str]:
    if not isinstance(cmd, list) or not cmd:
        return "Command must be a non-empty list of strings"
    if not all(isinstance(part, str) for part in cmd):
        return "Command arguments must be strings"
    if any(part.find(chr(0)) >= 0 for part in cmd):
        return "Command arguments must not contain NUL bytes"
    if sum(len(part.encode("utf-8", "replace")) for part in cmd) > DEFAULT_MAX_ARG_BYTES:
        return "Command arguments exceed the size limit"
    return None


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


def _clip(text: Optional[str], cap: Optional[int]) -> str:
    text = text or ""
    if cap is None:
        return text
    return text[:cap]


def _truncated(stdout: Optional[str], stderr: Optional[str], cap: Optional[int]) -> bool:
    if cap is None:
        return False
    return len(stdout or "") > cap or len(stderr or "") > cap


def _python_runner_source(user_path: str) -> str:
    return (
        "import json, sys\n"
        f"USER_PATH = {user_path!r}\n"
        f"SAFE_BUILTINS = {sorted(SAFE_BUILTINS)!r}\n"
        f"SAFE_IMPORTS = {sorted(SAFE_IMPORTS)!r}\n"
        "real_import = __import__\n"
        "def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):\n"
        "    root = name.split('.')[0]\n"
        "    if root not in SAFE_IMPORTS:\n"
        "        raise ImportError('Import of %r is not allowed in the sandbox' % name)\n"
        "    return real_import(name, globals, locals, fromlist, level)\n"
        "base = __builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__\n"
        "safe = {k: base[k] for k in SAFE_BUILTINS if k in base}\n"
        "safe['__import__'] = guarded_import\n"
        "ns = {'__builtins__': safe}\n"
        "try:\n"
        "    with open(USER_PATH, 'r', encoding='utf-8') as handle:\n"
        "        source = handle.read()\n"
        "    exec(compile(source, USER_PATH, 'exec'), ns, ns)\n"
        "    payload = {'ok': True, 'result': ns.get('result')}\n"
        "    print('AIPD_SANDBOX_RESULT:' + json.dumps(payload, default=str))\n"
        "except Exception as exc:\n"
        "    payload = {'ok': False, 'error': str(exc)}\n"
        "    print('AIPD_SANDBOX_RESULT:' + json.dumps(payload))\n"
        "    sys.exit(1)\n"
    )


def _parse_runner_output(stdout: str) -> dict:
    marker = "AIPD_SANDBOX_RESULT:"
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(marker):
            try:
                import json
                data = json.loads(line[len(marker):])
                if isinstance(data, dict):
                    return data
            except Exception:
                return {"error": "invalid sandbox result"}
    return {}
