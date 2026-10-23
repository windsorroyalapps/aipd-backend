
import yaml, os
CONFIG_PATH = "config/default.yaml"
class Config:
    def __init__(self, path=CONFIG_PATH):
        with open(path, "r") as fh:
            self.data = yaml.safe_load(fh) or {}
    def get(self, path, default=None):
        keys = path.split(".")
        val = self.data
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val
