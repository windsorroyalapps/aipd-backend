
import time, threading
class Scheduler:
    def __init__(self, db, engine):
        self.db = db
        self.engine = engine
    def schedule_workflow(self, wf_id: str, delay_seconds: int = 0):
        def trigger():
            time.sleep(delay_seconds)
            self.engine.submit({"type": "workflow", "id": wf_id})
        threading.Thread(target=trigger, daemon=True).start()
