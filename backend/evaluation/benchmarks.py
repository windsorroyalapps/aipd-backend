
import time, json
class BenchmarkRunner:
    def __init__(self):
        self.results = {}
    def run_benchmark(self, benchmark_id: str, func, *args, **kwargs) -> dict:
        start = time.time()
        try:
            result = func(*args, **kwargs)
            duration = time.time() - start
            self.results[benchmark_id] = {"status": "passed", "duration": duration, "result": str(result)[:200]}
            return self.results[benchmark_id]
        except Exception as e:
            duration = time.time() - start
            self.results[benchmark_id] = {"status": "failed", "duration": duration, "error": str(e)}
            return self.results[benchmark_id]
