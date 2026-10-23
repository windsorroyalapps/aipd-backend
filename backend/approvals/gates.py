
class ApprovalGate:
    def __init__(self, db):
        self.db = db
    def requires_approval(self, task_id: str, level: str = "L1") -> bool:
        return True
    def request_approval(self, task_id: str, approver_id: str, notes: str = "") -> str:
        return self.db.create_approval_request(task_id, approver_id, notes)
    def approve(self, approval_id: str, notes: str = "") -> bool:
        self.db.approve_task(approval_id, notes)
        return True
    def reject(self, approval_id: str, notes: str = "") -> bool:
        self.db.reject_task(approval_id, notes)
        return True
    def get_status(self, task_id: str) -> str:
        approval = self.db.get_approval_by_task(task_id)
        if not approval:
            return "pending"
        return approval.get("status", "pending")
