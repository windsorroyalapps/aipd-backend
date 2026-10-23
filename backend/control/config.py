
class ConfigController:
    def __init__(self, config):
        self.config = config
    def reload(self):
        self.config = __import__('yaml').safe_load(open(__import__('os').path.abspath("config/default.yaml"), "r"))
    def get(self, path, default=None):
        keys = path.split(".")
        val = self.config.get("data", self.config)
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val
