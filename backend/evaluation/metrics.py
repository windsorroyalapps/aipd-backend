
class MetricsCollector:
    def __init__(self):
        self.metrics = {}
    def record(self, metric_name: str, value: float, tags: dict = None):
        if metric_name not in self.metrics:
            self.metrics[metric_name] = []
        self.metrics[metric_name].append({"value": value, "tags": tags or {}})
    def get_average(self, metric_name: str) -> float:
        values = [m["value"] for m in self.metrics.get(metric_name, [])]
        return sum(values) / len(values) if values else 0.0
    def summarize(self) -> dict:
        return {name: {"count": len(values), "avg": self.get_average(name)} for name, values in self.metrics.items()}
