
class SchemaLibrary:
    def __init__(self):
        self.schemas = {}
    def register(self, schema_id: str, fields: list, description: str = ""):
        self.schemas[schema_id] = {"fields": fields, "description": description}
    def get(self, schema_id: str) -> dict:
        return self.schemas.get(schema_id, {})
    def list_schemas(self) -> list:
        return [{"id": k, **v} for k, v in self.schemas.items()]
