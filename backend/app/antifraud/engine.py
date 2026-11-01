"""Антифрод-движок: граф + модель + сохранение узлов в SQLite.

Решение антифрода — только ПРЕДУПРЕЖДЕНИЕ (заявка уходит на ручную проверку), а не отказ:
честных людей не наказываем, окончательно решает человек.
В графе и в таблице fraud_nodes — только HMAC-отпечатки, никаких открытых телефонов, ИНН и паспортов.
"""

import math
import time

from backend.app.antifraud.attacks import SCHEME_RU
from backend.app.antifraud.features import FEATURE_LABELS, build_vector, known_months_of
from backend.app.antifraud.fingerprints import fingerprint
from backend.app.antifraud.graph import Edge, FraudGraph, Node
from backend.app.antifraud.model import FraudModel, reference_model
from backend.app.db import Database
from common.passport import is_magic

STRONG_KINDS = ("device", "phone", "passport")
MAX_VIEW_NODES = 1500  # самые свежие узлы, которые отдаёт /api/antifraud/graph
INHERIT_MIN_SCORE = 0.35  # заявка рядом с уже помеченной группой: хватает половины порога


def _minutes(seconds: float) -> str:
    return "меньше минуты" if seconds < 60 else f"{round(seconds / 60)} мин"


def plural(n: int, one: str, few: str, many: str) -> str:
    """1 заявка, 2 заявки, 5 заявок."""
    n10, n100 = n % 10, n % 100
    word = one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many
    return f"{n} {word}"


# как рассказать про признак человеческим языком: (значение признака, граф-контекст) -> фраза
FACT_TEMPLATES = {
    "n_dev_passports": lambda v: f"{plural(int(v) + 1, 'паспорт', 'паспорта', 'паспортов')} с одного устройства",
    "n_dev_1h": lambda v: f"{plural(int(v) + 1, 'заявка', 'заявки', 'заявок')} с этого устройства за час",
    "n_phone_passports": lambda v: f"один телефон указан у {plural(int(v) + 1, 'паспорта', 'паспортов', 'паспортов')}",
    "n_employer_1h": lambda v: f"{plural(int(v) + 1, 'заявка', 'заявки', 'заявок')} одного работодателя за час",
    "log_gap_device": lambda v: f"пауза до соседней заявки с устройства {_minutes(math.expm1(v))}",
    "log_gap_employer": lambda v: f"пауза до соседней заявки работодателя {_minutes(math.expm1(v))}",
    "log_comp_size": lambda v: f"связанная группа из {plural(round(math.expm1(v)), 'заявки', 'заявок', 'заявок')}",
    "log_comp_growth": lambda v: f"группа выросла на {plural(round(math.expm1(v)), 'заявку', 'заявки', 'заявок')} за час",
    "log_comp_passports": lambda v: f"в группе {round(math.expm1(v))} разных паспортов",
    "n_same_passport": lambda v: f"{plural(int(v) + 1, 'заявка', 'заявки', 'заявок')} по одному паспорту за сутки",
    "income_spread": lambda v: f"доход по заявкам одного человека отличается в {math.exp(v):.1f} раза",
    "n_loans": lambda v: f"в кредитной истории {int(v)} кредитов",
    "known_months": lambda v: f"известно {int(v)} мес. платёжной истории",
}


class AntifraudEngine:
    def __init__(self, db: Database, pepper: str, threshold: float = 0.6, model: FraudModel | None = None):
        self.db, self.pepper, self.threshold = db, pepper, threshold
        self.model = model or reference_model()
        self.graph = FraudGraph()
        self.last_ts = 0.0
        self._load()

    def _load(self) -> None:
        """После перезапуска восстанавливаем граф из сохранённых узлов (рёбра пересчитываются)."""
        rows = self.db.query_all("SELECT * FROM fraud_nodes ORDER BY ts DESC LIMIT 5000")
        for r in reversed(rows):
            fps = {"device": r["device"], "phone": r["phone"], "employer": r["employer"], "passport": r["passport"]}
            node = Node(r["app_id"], r["ts"], {k: v for k, v in fps.items() if v},
                        r["amount"], r["income"], r["term"], r["n_loans"], r["known_months"],
                        channel=r["channel"] or "office", score=r["score"], pattern=r["pattern"],
                        flagged=bool(r["flagged"]))
            node.arrived = min(node.ts, time.time())  # реальное время появления после перезапуска неизвестно
            self.graph.add(node)
            self.last_ts = max(self.last_ts, node.ts)

    def assess(self, app_id: str, data, loans: list[dict], ts: float | None = None) -> tuple[dict, list[Edge]]:
        ts = ts if ts is not None else time.time()
        fp = lambda kind, value: fingerprint(self.pepper, kind, value)  # noqa: E731
        fps = {"phone": fp("phone", data.phone), "employer": fp("inn", data.employer_inn),
               "passport": fp("passport", app_id if is_magic(data.passport) else data.passport)}
        # «устройство» — только устройство КЛИЕНТА. Без client_device_id (заявка из офиса) ребра устройства нет вообще:
        # браузер и IP оператора склеили бы всех его честных клиентов в одну «фабрику»
        if data.client_device_id:
            fps["device"] = fp("device", data.client_device_id)
        node = Node(app_id, ts, fps, amount=data.loan_amount, income=data.monthly_income, term=data.loan_term_months,
                    n_loans=len(loans), known_months=known_months_of(loans),
                    channel="online" if data.client_device_id else "office")
        node.arrived = time.time()
        new_edges = self.graph.add(node)
        vec = build_vector(self.graph, node)
        scored = self.model.score_vector(vec)
        node.score, node.pattern = scored["score"], scored["pattern"]

        members = self.graph.component_nodes(app_id)
        flagged_neighbors = {e.b for e in new_edges if e.kind in STRONG_KINDS and self.graph.nodes[e.b].flagged}
        inherited = bool(flagged_neighbors) and node.score >= INHERIT_MIN_SCORE
        node.flagged = node.score >= self.threshold or inherited
        self.last_ts = max(self.last_ts, ts)

        result = {
            "fraud_score": round(node.score, 3), "pattern": node.pattern if node.flagged else None,
            "pattern_ru": SCHEME_RU.get(node.pattern) if node.flagged else None,
            "similarity": round(scored["similarity"], 3), "anomaly": round(scored["anomaly"], 3),
            "channel": node.channel, "flagged": node.flagged, "inherited_from_cluster": inherited and node.score < self.threshold,
            "explanation": scored["contributions"], "component_size": len(members),
            "cluster_id": self.graph.component_id(app_id),
            "text": None,
        }
        if node.flagged:
            result["text"] = self._text(node, scored, members, vec)
        self._save(node)
        return result, new_edges

    def _text(self, node: Node, scored: dict, members: list[Node], vec: dict) -> str:
        scheme = SCHEME_RU.get(node.pattern or scored["pattern"] or "", "подозрительная схема")
        facts = [FACT_TEMPLATES[c["feature"]](vec[c["feature"]]) for c in scored["contributions"]
                 if c["feature"] in FACT_TEMPLATES]
        if len(members) > 1:
            span = abs(node.ts - min(m.ts for m in members))
            facts.append(f"{plural(len(members), 'связанная заявка', 'связанные заявки', 'связанных заявок')} "
                         + ("меньше чем за минуту" if span < 60 else f"за {round(span / 60)} мин"))
        return (f"Похожа на схему «{scheme}» на {scored['similarity']:.0%}"
                + (": " + "; ".join(dict.fromkeys(facts)) if facts else ""))

    def _save(self, node: Node) -> None:
        f = node.fps
        self.db.execute(
            "INSERT OR REPLACE INTO fraud_nodes (app_id, ts, device, phone, employer, passport, amount, income, term,"
            " n_loans, known_months, score, pattern, flagged, channel) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (node.id, node.ts, f.get("device"), f["phone"], f["employer"], f["passport"], node.amount, node.income,
             node.term, node.n_loans, node.known_months, node.score, node.pattern, int(node.flagged), node.channel))

    # --- представление для API ------------------------------------------
    def view(self, minutes: int) -> dict:
        """Узлы, рёбра и группы, появившиеся за последние minutes минут РЕАЛЬНОГО времени (ts у симулятора виртуальное, по нему окно строить нельзя)."""
        horizon = time.time() - minutes * 60
        recent = [i for i, n in self.graph.nodes.items() if n.arrived >= horizon]
        # потолок на размер ответа: иначе за сутки в графе могут быть десятки тысяч узлов, и каждый запрос
        # (его может слать любой риск-менеджер/аудитор, а фронт ещё и опрашивает) грузит процессор и сеть
        recent.sort(key=lambda i: self.graph.nodes[i].ts, reverse=True)
        ids = set(recent[:MAX_VIEW_NODES])
        status = {r["id"]: r["status"] for r in self.db.query_all(
            f"SELECT id, status FROM applications WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))} if ids else {}
        nodes = [{"id": i, "status": status.get(i, "processing"), "fraud_score": round(self.graph.nodes[i].score or 0, 3),
                  "ts": self.graph.nodes[i].ts} for i in sorted(ids, key=lambda i: self.graph.nodes[i].ts)]
        edges = [{"a": e.a, "b": e.b, "kind": e.kind} for e in self.graph.edges if e.a in ids and e.b in ids]
        clusters, seen = [], set()
        for i in ids:
            root = self.graph.component_id(i)
            if root in seen:
                continue
            seen.add(root)
            members = [n for n in self.graph.component_nodes(i) if n.id in ids]
            if len(members) < 2:
                continue
            patterns = [n.pattern for n in members if n.flagged and n.pattern]
            clusters.append({"id": root, "nodes": sorted(n.id for n in members),
                             "score": round(max(n.score or 0 for n in members), 3),
                             "pattern": max(set(patterns), key=patterns.count) if patterns else None})
        clusters.sort(key=lambda c: -c["score"])
        return {"nodes": nodes, "edges": edges, "clusters": clusters}
