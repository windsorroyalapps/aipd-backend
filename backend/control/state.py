
class ControlState:
    def __init__(self, plane):
        self.plane = plane
    def save(self, key: str, value):
        self.plane.set_state(key, value)
    def load(self, key: str):
        return self.plane.get_state(key)
