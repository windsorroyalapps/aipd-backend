
import subprocess, os, tempfile
class Sandbox:
    def __init__(self, work_dir: str = "/tmp/aipd_sandbox"):
        self.work_dir = work_dir
        os.makedirs(work_dir, exist_ok=True)
    def execute(self, cmd: list, env: dict = None) -> dict:
        import subprocess
        restricted_env = {k: v for k, v in (env or os.environ.copy()).items() if k.startswith(("PATH", "HOME", "USER", "LANG")) or k.startswith("AIPD_")}
        restricted_env["PATH"] = "/usr/bin:/bin"
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=self.work_dir, env=restricted_env, timeout=30)
            return {"stdout": result.stdout[:500], "stderr": result.stderr[:500], "returncode": result.returncode}
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "returncode": -1}
    def create_isolated_script(self, content: str) -> str:
        import tempfile
        fd, path = tempfile.mkstemp(dir=self.work_dir, suffix=".py")
        with os.fdopen(fd, "w") as f:
            f.write(content)
        return path
