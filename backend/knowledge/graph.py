
import networkx as nx, json
class KnowledgeGraph:
    def __init__(self, db, store_path="data/knowledge_graph.json"):
        self.db = db
        self.store_path = store_path
        self.graph = nx.Graph()
        try:
            with open(store_path) as f:
                data = json.load(f)
                for node in data.get("nodes", []):
                    self.graph.add_node(node.get("id"), **node.get("props", {}))
                for edge in data.get("edges", []):
                    self.graph.add_edge(edge.get("source"), edge.get("target"), **edge.get("props", {}))
        except Exception:
            pass
    def add_entity(self, entity_id: str, label: str, properties: dict = None):
        props = (properties or {}).copy()
        props["entity_label"] = label
        self.graph.add_node(entity_id, **props)
    def add_relation(self, source: str, target: str, relation_type: str, properties: dict = None):
        self.graph.add_edge(source, target, relation=relation_type, **(properties or {}))
    def query_neighbors(self, entity_id: str, depth: int = 1) -> list:
        if entity_id not in self.graph:
            return []
        neighbors = list(self.graph.neighbors(entity_id))
        return neighbors
    def save(self):
        import json, os
        os.makedirs(os.path.dirname(self.store_path) or ".", exist_ok=True)
        data = {
            "nodes": [{"id": n, "props": dict(self.graph.nodes[n])} for n in self.graph.nodes()],
            "edges": [{"source": u, "target": v, "props": dict(self.graph[u][v])} for u, v in self.graph.edges()]
        }
        with open(self.store_path, "w") as f:
            json.dump(data, f)
    def get_stats(self) -> dict:
        return {"nodes": self.graph.number_of_nodes(), "edges": self.graph.number_of_edges()}
