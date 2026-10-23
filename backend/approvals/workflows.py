
class ApprovalWorkflow:
    def __init__(self, gate, escalation):
        self.gate = gate
        self.escalation = escalation
    def process_approval_chain(self, task_id: str, approvers: list) -> dict:
        results = []
        for approver in approvers:
            approval_id = self.gate.request_approval(task_id, approver)
            results.append({"approver": approver, "approval_id": approval_id, "status": "requested"})
        return {"task_id": task_id, "chain": results}
