
import json
class ToolRegistry:
    def __init__(self):
        self.tools = {}
    def register(self, name: str, tool_func, description: str = "", version: str = "1.0.0"):
        self.tools[name] = {"func": tool_func, "desc": description, "version": version}
    def list(self) -> list:
        return [{"name": k, **v} for k, v in self.tools.items()]
    def get(self, name: str):
        return self.tools.get(name)
    def execute(self, name: str, *args, **kwargs) -> dict:
        tool = self.get(name)
        if not tool:
            return {"error": f"Tool {name} not found"}
        try:
            result = tool["func"](*args, **kwargs)
            return {"tool": name, "result": result, "status": "ok"}
        except Exception as e:
            return {"tool": name, "error": str(e), "status": "failed"}
