"""Граф связей заявок. Узел — заявка, ребро — общий отпечаток (устройство, телефон, паспорт, работодатель).

Граф живёт в памяти и обновляется по одной заявке (инкрементально). Компоненты связности держим через union-find.
Чтобы группа из N заявок с одним устройством не давала N² рёбер, новая заявка связывается не более чем
с MAX_LINKS последними заявками с тем же отпечатком — группа всё равно остаётся связной.
"""

from dataclasses import dataclass, field

# насколько «свежей» должна быть общая связь, чтобы считаться ребром (секунды)
WINDOWS = {"device": 6 * 3600, "phone": 7 * 86400, "passport": 30 * 86400, "employer": 3600}
MAX_LINKS = 3
KINDS = tuple(WINDOWS)


@dataclass
class Node:
    id: str
    ts: float
    fps: dict[str, str]  # kind -> отпечаток
    amount: int = 0
    income: int = 0
    term: int = 0
    n_loans: int = 0
    known_months: int = 0
    channel: str = "office"  # office | online
    arrived: float = 0.0  # реальное время появления в графе (для окна ?minutes=...; ts у симулятора виртуальное)
    score: float | None = None
    pattern: str | None = None
    flagged: bool = False


@dataclass
class Edge:
    a: str
    b: str
    kind: str
    ts: float


@dataclass
class FraudGraph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    by_fp: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    _parent: dict[str, str] = field(default_factory=dict)
    _members: dict[str, set[str]] = field(default_factory=dict)  # корень компоненты -> её узлы
    _edge_count: dict[str, int] = field(default_factory=dict)

    # --- union-find -----------------------------------------------------
    def _find(self, x: str) -> str:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def _union(self, a: str, b: str) -> None:
        ra, rb = self._find(a), self._find(b)
        if ra == rb:
            self._edge_count[ra] += 1
            return
        if len(self._members[ra]) < len(self._members[rb]):
            ra, rb = rb, ra
        self._parent[rb] = ra
        self._members[ra] |= self._members.pop(rb)
        self._edge_count[ra] += self._edge_count.pop(rb) + 1

    # --- добавление заявки ----------------------------------------------
    def add(self, node: Node) -> list[Edge]:
        """Кладёт заявку в граф и возвращает новые рёбра."""
        self.nodes[node.id] = node
        self._parent[node.id] = node.id
        self._members[node.id] = {node.id}
        self._edge_count[node.id] = 0
        new_edges = []
        for kind in KINDS:
            fp = node.fps.get(kind)
            if not fp:
                continue
            bucket = self.by_fp.setdefault((kind, fp), [])
            linked = []  # идём с конца и останавливаемся на MAX_LINKS: список целиком (как раньше) на 60 тысячах заявок
            for o in reversed(bucket):  # с одним устройством тормозил весь сервер, время растёт квадратично
                if abs(self.nodes[o].ts - node.ts) <= WINDOWS[kind]:
                    linked.append(o)
                    if len(linked) == MAX_LINKS:
                        break
            for other in linked:
                edge = Edge(node.id, other, kind, node.ts)
                self.edges.append(edge)
                new_edges.append(edge)
                self._union(node.id, other)
            bucket.append(node.id)
        return new_edges

    # --- запросы --------------------------------------------------------
    def same(self, node: Node, kind: str, within: float) -> list[Node]:
        """Другие заявки с тем же отпечатком в пределах within секунд."""
        fp = node.fps.get(kind)
        if not fp:
            return []
        return [self.nodes[o] for o in self.by_fp.get((kind, fp), [])
                if o != node.id and abs(self.nodes[o].ts - node.ts) <= within]

    def component_id(self, node_id: str) -> str:
        return self._find(node_id)

    def component_nodes(self, node_id: str) -> list[Node]:
        return [self.nodes[i] for i in self._members[self._find(node_id)]]

    def component_edges(self, node_id: str) -> int:
        return self._edge_count[self._find(node_id)]

    def components(self, min_size: int = 2) -> list[list[str]]:
        return [sorted(m) for m in self._members.values() if len(m) >= min_size]
