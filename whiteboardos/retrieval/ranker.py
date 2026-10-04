"""Hybrid retrieval scoring and graph expansion."""
import math
import re
import time
from pathlib import PurePosixPath
from .embeddings import cosine

STOP = {
    "find","show","give","get","me","my","the","a","an","all","everything","anything",
    "related","relevant","about","for","to","of","in","on","with","and","or","files","file",
    "documents","document","stuff","things","please","workspace","what","are","is","our"
}


def query_terms(query):
    raw = re.findall(r"[A-Za-z0-9][A-Za-z0-9_+.-]*", query.casefold())
    filtered = [term for term in raw if len(term) > 1 and term not in STOP]
    return filtered or raw


def _metadata_score(row, terms):
    if not terms:
        return 0.0
    project = (row.get("project") or "").casefold()
    document_type = (row.get("document_type") or "").casefold()
    summary = (row.get("summary") or "").casefold()
    keywords = [str(value).casefold() for value in (row.get("keywords") or [])]
    per_term = []
    for term in terms:
        score = 0.0
        if term == project or term in project:
            score = max(score, 1.0)
        if any(term == keyword or term in keyword for keyword in keywords):
            score = max(score, 0.95)
        if term in document_type:
            score = max(score, 0.75)
        if term in summary:
            score = max(score, 0.65)
        per_term.append(score)
    return sum(per_term) / len(per_term)


def _path_score(path, terms):
    if not terms:
        return 0.0
    folded = path.casefold()
    name = PurePosixPath(path).name.casefold()
    values = []
    for term in terms:
        values.append(1.0 if term in name else (0.75 if term in folded else 0.0))
    return sum(values) / len(values)


def _activity_score(last_viewed, now=None):
    if not last_viewed:
        return 0.0
    age_days = max(0.0, ((now or time.time()) - float(last_viewed)) / 86400.0)
    return math.exp(-age_days / 7.0)


def direct_scores(rows, query_vector, terms, now=None):
    ranked = []
    for row in rows:
        vector = row.get("_vector")
        semantic = max(0.0, cosine(query_vector, vector)) if vector and len(vector) == len(query_vector) else 0.0
        metadata = _metadata_score(row, terms)
        path = _path_score(row["path"], terms)
        activity = _activity_score(row.get("last_viewed"), now)
        pinned = 1.0 if row.get("pinned") else 0.0
        score = 0.55 * semantic + 0.25 * metadata + 0.10 * path + 0.05 * activity + 0.05 * pinned
        ranked.append({**row, "score": score, "direct_score": score,
                       "score_breakdown": {"semantic": semantic, "metadata": metadata,
                                           "path": path, "activity": activity, "pinned": pinned},
                       "related_via": []})
    ranked.sort(key=lambda item: (item["score"], item["path"]), reverse=True)
    return ranked


def expand_graph(db, ranked, seed_count=5):
    by_id = {item["file_id"]: item for item in ranked}
    seeds = ranked[:max(1, seed_count)]
    for seed in seeds:
        edges = db.execute('''SELECT r.target,r.kind,r.weight,r.reason,f.path
            FROM file_relations r JOIN files f ON f.id=r.target
            JOIN files s ON s.id=r.source
            WHERE r.source=? AND r.source_hash=s.hash AND r.target_hash=f.hash
            ORDER BY r.weight DESC LIMIT 12''', (seed["file_id"],)).fetchall()
        for edge in edges:
            target = by_id.get(edge["target"])
            if not target:
                continue
            boost = float(seed["direct_score"]) * float(edge["weight"])
            old = target.get("graph_boost", 0.0)
            if boost > old:
                target["graph_boost"] = boost
            target["related_via"].append({"seed": seed["path"], "kind": edge["kind"],
                                           "weight": round(float(edge["weight"]), 4)})
    for item in ranked:
        graph = item.get("graph_boost", 0.0)
        item["score_breakdown"]["graph"] = graph
        item["score"] = min(1.0, item["direct_score"] + 0.15 * graph)
    ranked.sort(key=lambda item: (item["score"], item["path"]), reverse=True)
    return ranked
