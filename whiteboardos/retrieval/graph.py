"""Sparse, deterministic file relationship graph stored in SQLite.

The graph is deliberately bounded. Earlier builds created near-cliques for large
same-project/folder groups, which made a 600-file workspace explode past 90k
edges. This implementation keeps the strongest local relationships per file,
so graph maintenance stays predictable as the workspace grows.
"""
import json
from collections import defaultdict
from pathlib import PurePosixPath
from .embeddings import cosine

SEMANTIC_NEIGHBORS = 8
KEYWORD_NEIGHBORS = 6
PROJECT_NEIGHBORS = 8
FOLDER_NEIGHBORS = 6


def _keywords(row):
    value = row.get("keywords") or []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = []
    return {str(item).casefold().strip() for item in value if str(item).strip()}


def rebuild_graph(db):
    rows = [dict(r) for r in db.execute('''
        SELECT f.id,f.path,f.hash,m.project,m.keywords,m.status,e.vector,e.provider,e.model,e.semantic,e.content_hash AS embedding_hash
        FROM files f
        LEFT JOIN metadata m ON m.file_id=f.id AND m.content_hash=f.hash
        LEFT JOIN embeddings e ON e.file_id=f.id AND e.content_hash=f.hash
        ORDER BY f.path
    ''')]
    db.execute("DELETE FROM file_relations")

    vectors = {}
    keywords = {}
    projects = defaultdict(list)
    folders = defaultdict(list)
    semantic_candidates = defaultdict(list)
    keyword_candidates = defaultdict(list)

    for row in rows:
        try:
            vectors[row["id"]] = json.loads(row["vector"]) if row.get("vector") else None
        except ValueError:
            vectors[row["id"]] = None
        keywords[row["id"]] = _keywords(row)
        project = (row.get("project") or "").strip().casefold()
        if project:
            projects[project].append(row)
        folder = PurePosixPath(row["path"]).parent.as_posix()
        if folder != ".":
            folders[folder].append(row)

    # Pairwise semantic/keyword similarity is still simple enough at hackathon scale,
    # but only the strongest K neighbors are retained for each node.
    for i, left in enumerate(rows):
        for right in rows[i + 1:]:
            kw_left, kw_right = keywords[left["id"]], keywords[right["id"]]
            union = kw_left | kw_right
            if union:
                overlap = len(kw_left & kw_right) / len(union)
                if overlap >= 0.25:
                    weight = min(0.90, 0.45 + 0.50 * overlap)
                    keyword_candidates[left["id"]].append((weight, right))
                    keyword_candidates[right["id"]].append((weight, left))

            vec_left, vec_right = vectors.get(left["id"]), vectors.get(right["id"])
            same_space = (left.get("provider") == right.get("provider")
                          and left.get("model") == right.get("model")
                          and bool(left.get("semantic")) == bool(right.get("semantic")))
            if same_space and vec_left and vec_right and len(vec_left) == len(vec_right):
                similarity = max(0.0, cosine(vec_left, vec_right))
                if similarity >= 0.72:
                    semantic_candidates[left["id"]].append((similarity, right))
                    semantic_candidates[right["id"]].append((similarity, left))

    edges = {}
    row_by_id = {r["id"]: r for r in rows}

    def put(source, target, kind, weight, reason):
        if source["id"] == target["id"]:
            return
        key = (source["id"], target["id"], kind)
        current = edges.get(key)
        if current is None or weight > current[0]:
            edges[key] = (float(weight), reason, source["hash"], target["hash"])

    def add_symmetric(a, b, kind, weight, reason):
        put(a, b, kind, weight, reason)
        put(b, a, kind, weight, reason)

    # Sparse explicit groups: connect each file to nearby members instead of a clique.
    for members in projects.values():
        members = sorted(members, key=lambda r: r["path"])
        for idx, left in enumerate(members):
            others = members[:idx] + members[idx + 1:]
            # Prefer semantically closest project peers when vectors are available.
            scored = []
            for right in others:
                vl, vr = vectors.get(left["id"]), vectors.get(right["id"])
                sim = cosine(vl, vr) if vl and vr and len(vl) == len(vr) else 0.0
                scored.append((sim, right))
            for _, right in sorted(scored, key=lambda x: (-x[0], x[1]["path"]))[:PROJECT_NEIGHBORS]:
                add_symmetric(left, right, "same_project", 0.95, "same local-model project")

    for members in folders.values():
        members = sorted(members, key=lambda r: r["path"])
        for idx, left in enumerate(members):
            # Local adjacency is enough to encode folder membership without O(n^2) edges.
            lo = max(0, idx - FOLDER_NEIGHBORS // 2)
            hi = min(len(members), idx + FOLDER_NEIGHBORS // 2 + 1)
            for right in members[lo:hi]:
                if right["id"] != left["id"]:
                    add_symmetric(left, right, "same_folder", 0.40, "same folder")

    for source_id, candidates in keyword_candidates.items():
        source = row_by_id[source_id]
        for weight, target in sorted(candidates, key=lambda x: (-x[0], x[1]["path"]))[:KEYWORD_NEIGHBORS]:
            add_symmetric(source, target, "keyword_overlap", weight, "shared local-model keywords")

    for source_id, candidates in semantic_candidates.items():
        source = row_by_id[source_id]
        for weight, target in sorted(candidates, key=lambda x: (-x[0], x[1]["path"]))[:SEMANTIC_NEIGHBORS]:
            add_symmetric(source, target, "semantic_neighbor", weight, "embedding similarity")

    for (source, target, kind), (weight, reason, source_hash, target_hash) in edges.items():
        db.execute('''INSERT OR REPLACE INTO file_relations
            (source,target,kind,weight,reason,source_hash,target_hash)
            VALUES(?,?,?,?,?,?,?)''',
            (source, target, kind, round(weight, 6), reason, source_hash, target_hash))
    db.commit()
    return {"edges": len(edges), "nodes": len(rows), "sparse": True,
            "caps": {"semantic": SEMANTIC_NEIGHBORS, "keyword": KEYWORD_NEIGHBORS,
                     "project": PROJECT_NEIGHBORS, "folder": FOLDER_NEIGHBORS}}
