"""In-process tool registry: register, lookup, list, and execute."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

DEFINITIONS_SQL = """
CREATE TABLE IF NOT EXISTS tool_definitions (
    name TEXT PRIMARY KEY,
    description TEXT,
    version TEXT,
    schema_json TEXT,
    has_handler INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

_TYPE_MAP = {
    "str": str,
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "number": (int, float),
    "bool": bool,
    "boolean": bool,
    "dict": dict,
    "object": dict,
    "list": list,
    "array": list,
}


def validate_arguments(schema: Optional[Dict], args: tuple, kwargs: Dict) -> Optional[str]:
    """Return an error message if args do not match the optional schema, else None."""
    if not schema:
        return None
    params = schema.get("arguments") or schema.get("params") or []
    if not isinstance(params, list):
        return "Invalid tool schema: 'arguments' must be a list"
    allow_extra = schema.get("additional_properties", True)
    kwargs = dict(kwargs or {})
    positional = list(args or ())

    required = [p for p in params if p.get("required", True) and "default" not in p]
    if len(positional) > len(params):
        if not allow_extra:
            return f"Expected at most {len(params)} positional arguments, got {len(positional)}"
    for index, param in enumerate(params):
        name = param.get("name") or f"arg{index}"
        if index < len(positional):
            value = positional[index]
            if name in kwargs:
                return f"Argument {name!r} was supplied both positionally and as a keyword"
        elif name in kwargs:
            value = kwargs.pop(name)
        elif "default" in param:
            continue
        elif param.get("required", True):
            return f"Missing required argument {name!r}"
        else:
            continue
        type_error = _type_error(name, value, param.get("type"))
        if type_error:
            return type_error

    if kwargs and not allow_extra:
        extra = ", ".join(sorted(kwargs))
        return f"Unexpected arguments: {extra}"
    return None


def _type_error(name: str, value: Any, expected: Optional[str]) -> Optional[str]:
    if not expected:
        return None
    key = str(expected).lower()
    if key in {"int", "integer"}:
        if type(value) is bool or not isinstance(value, int):
            return f"Argument {name!r} must be of type {expected}"
        return None
    if key in {"float", "number"}:
        if type(value) is bool or not isinstance(value, (int, float)):
            return f"Argument {name!r} must be of type {expected}"
        return None
    py_type = _TYPE_MAP.get(key)
    if py_type and not isinstance(value, py_type):
        return f"Argument {name!r} must be of type {expected}"
    return None


class ToolRegistry:
    def __init__(self, db=None):
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.db = db
        if self.db is not None:
            self.db.execute(DEFINITIONS_SQL)
            self._load_persisted()

    def _load_persisted(self) -> None:
        rows = self.db.execute("SELECT * FROM tool_definitions", fetch=True)
        for row in rows:
            name = row["name"]
            if name in self.tools:
                continue
            try:
                schema = json.loads(row["schema_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                schema = {}
            self.tools[name] = {
                "func": None,
                "desc": row.get("description") or "",
                "version": row.get("version") or "1.0.0",
                "schema": schema if isinstance(schema, dict) else {},
            }

    def register(self, name: str, tool_func, description: str = "", version: str = "1.0.0", schema: Optional[Dict] = None):
        if not name or not isinstance(name, str):
            raise ValueError("Tool name must be a non-empty string")
        if tool_func is not None and not callable(tool_func):
            raise ValueError(f"Tool {name!r} handler must be callable or None")
        entry = {
            "func": tool_func,
            "desc": description,
            "version": version or "1.0.0",
            "schema": schema or {},
        }
        self.tools[name] = entry
        if self.db is not None:
            self._persist(name, entry)
        return name

    def _persist(self, name: str, entry: Dict) -> None:
        schema_json = json.dumps(entry.get("schema") or {})
        has_handler = 1 if entry.get("func") is not None else 0
        existing = self.db.execute("SELECT name FROM tool_definitions WHERE name = ?", (name,), fetch=True)
        if existing:
            self.db.execute(
                "UPDATE tool_definitions SET description = ?, version = ?, schema_json = ?, has_handler = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                (entry["desc"], entry["version"], schema_json, has_handler, name),
            )
        else:
            self.db.execute(
                "INSERT INTO tool_definitions (name, description, version, schema_json, has_handler) VALUES (?, ?, ?, ?, ?)",
                (name, entry["desc"], entry["version"], schema_json, has_handler),
            )

    def unregister(self, name: str) -> bool:
        existed = self.tools.pop(name, None) is not None
        if existed and self.db is not None:
            self.db.execute("DELETE FROM tool_definitions WHERE name = ?", (name,))
        return existed

    def list(self) -> list:
        return [{"name": k, **v} for k, v in self.tools.items()]

    def get(self, name: str):
        return self.tools.get(name)

    def execute(self, name: str, *args, **kwargs) -> dict:
        tool = self.get(name)
        if not tool:
            return {"error": f"Tool {name} not found", "status": "not_found"}
        if tool.get("func") is None:
            return {
                "tool": name,
                "status": "no_handler",
                "error": "Tool is registered but has no callable handler",
            }
        problem = validate_arguments(tool.get("schema"), args, kwargs)
        if problem:
            return {"tool": name, "status": "invalid", "error": problem}
        try:
            result = tool["func"](*args, **kwargs)
            return {"tool": name, "result": result, "status": "ok"}
        except Exception as exc:
            return {"tool": name, "error": str(exc), "status": "failed"}
