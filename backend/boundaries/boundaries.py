
class DataBoundary:
    def __init__(self, isolation_level: str = "project"):
        self.isolation_level = isolation_level
        self.boundaries = {}
    def define_boundary(self, boundary_id: str, allowed_resources: list, allowed_users: list):
        self.boundaries[boundary_id] = {"resources": allowed_resources, "users": allowed_users}
    def check_access(self, user_id: str, resource: str, boundary_id: str) -> bool:
        b = self.boundaries.get(boundary_id, {"resources": [resource], "users": [user_id]})
        return user_id in b["users"] and resource in b["resources"]
    def enforce(self, user_id: str, resource: str, boundary_id: str) -> bool:
        allowed = self.check_access(user_id, resource, boundary_id)
        if not allowed:
            raise PermissionError(f"Access denied to {resource} in boundary {boundary_id} for user {user_id}")
        return True
