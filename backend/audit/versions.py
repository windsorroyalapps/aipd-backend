
class VersionTracker:
    def __init__(self, db):
        self.db = db
    def create_version(self, artifact_type: str, artifact_id: str, version_tag: str, content_hash: str, created_by: str = "system"):
        self.db.record_version(artifact_type, artifact_id, version_tag, content_hash, created_by)
        self.db.audit("version.created", created_by, "version", artifact_id, '{"tag":"' + version_tag + '","hash":"' + content_hash + '"}')
    def get_history(self, artifact_type: str, artifact_id: str) -> list:
        return self.db.get_versions_for_artifact(artifact_type, artifact_id)
    def get_latest_version(self, artifact_type: str, artifact_id: str) -> dict:
        versions = self.get_history(artifact_type, artifact_id)
        return versions[0] if versions else {}
