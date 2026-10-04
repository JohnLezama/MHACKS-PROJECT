"""Orchestration for WhiteBoardOS precomputed retrieval."""
import hashlib
import json
import time
from .context import compact_packet
from .embeddings import EmbeddingRouter
from .graph import rebuild_graph
from .ranker import direct_scores, expand_graph, query_terms


class RetrievalService:
    def __init__(self, catalog, embedder=None):
        self.catalog = catalog
        self.db = catalog.db
        self.embedder = embedder or EmbeddingRouter()
        self.ensure_schema()

    def ensure_schema(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS embeddings(
            file_id TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            semantic INTEGER NOT NULL,
            vector TEXT NOT NULL,
            created REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS file_relations(
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            kind TEXT NOT NULL,
            weight REAL NOT NULL,
            reason TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            target_hash TEXT NOT NULL,
            PRIMARY KEY(source,target,kind)
        );
        CREATE INDEX IF NOT EXISTS idx_file_relations_source ON file_relations(source,weight DESC);
        CREATE INDEX IF NOT EXISTS idx_file_relations_target ON file_relations(target,weight DESC);
        ''')
        self.db.commit()

    def cleanup(self):
        # Remove deleted files and stale content-derived state. Path-only changes keep
        # embeddings valid because paths are scored separately from semantic vectors.
        self.db.execute("DELETE FROM embeddings WHERE file_id NOT IN (SELECT id FROM files)")
        self.db.execute("DELETE FROM embeddings WHERE file_id IN (SELECT e.file_id FROM embeddings e JOIN files f ON f.id=e.file_id WHERE e.content_hash!=f.hash)")
        self.db.execute("DELETE FROM file_relations WHERE source NOT IN (SELECT id FROM files) OR target NOT IN (SELECT id FROM files)")
        self.db.execute("DELETE FROM file_relations WHERE source_hash!=(SELECT hash FROM files WHERE id=source) OR target_hash!=(SELECT hash FROM files WHERE id=target)")
        self.db.commit()

    def source_text(self, file_id):
        row = self.db.execute('''SELECT f.path,f.hash,f.description,m.summary,m.keywords,m.project,m.document_type,m.status,m.content_hash
            FROM files f LEFT JOIN metadata m ON m.file_id=f.id WHERE f.id=?''', (file_id,)).fetchone()
        if not row:
            raise ValueError("Unknown file ID")
        row = dict(row)
        fresh = row.get("content_hash") == row["hash"] and row.get("status") == "ready"
        if fresh:
            keywords = row.get("keywords") or "[]"
            try:
                keywords = ", ".join(json.loads(keywords))
            except ValueError:
                pass
            # Keep location out of the semantic vector. Path/folder are separate
            # retrieval signals, so moving or renaming a file does not invalidate
            # its content embedding. This makes organization operations cheap.
            text = "\n".join([
                "Project: " + (row.get("project") or ""),
                "Type: " + (row.get("document_type") or ""),
                "Summary: " + (row.get("summary") or ""),
                "Keywords: " + str(keywords),
            ])
        else:
            text = "Excerpt: " + (row.get("description") or "")
        return row, text

    def index_file(self, file_id, force=False):
        row, text = self.source_text(file_id)
        source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        current = self.db.execute("SELECT * FROM embeddings WHERE file_id=?", (file_id,)).fetchone()
        if current and not force and current["content_hash"] == row["hash"] and current["source_hash"] == source_hash:
            return {"indexed": False, "file_id": file_id, "provider": current["provider"], "model": current["model"]}
        embedded = self.embedder.embed(text)
        vector = embedded["vector"]
        self.db.execute('''INSERT OR REPLACE INTO embeddings
            (file_id,content_hash,source_hash,provider,model,dimensions,semantic,vector,created)
            VALUES(?,?,?,?,?,?,?,?,?)''',
            (file_id, row["hash"], source_hash, embedded["provider"], embedded["model"], len(vector),
             1 if embedded["semantic"] else 0, json.dumps(vector, separators=(",", ":")), time.time()))
        self.db.commit()
        return {"indexed": True, "file_id": file_id, "provider": embedded["provider"],
                "model": embedded["model"], "dimensions": len(vector), "semantic": embedded["semantic"]}

    def refresh(self, limit=1, force=False, rebuild=True):
        self.cleanup()
        if force:
            rows = self.db.execute("SELECT id FROM files ORDER BY path LIMIT ?",
                (max(1, min(int(limit), 200)),)).fetchall()
        else:
            target = self.embedder.target_identity()
            rows = self.db.execute('''SELECT f.id FROM files f LEFT JOIN embeddings e ON e.file_id=f.id
                WHERE e.file_id IS NULL OR e.content_hash!=f.hash
                   OR e.provider!=? OR e.model!=? OR e.semantic!=?
                ORDER BY f.path LIMIT ?''',
                (target["provider"], target["model"], 1 if target["semantic"] else 0,
                 max(1, min(int(limit), 200)))).fetchall()
        indexed = []
        for row in rows:
            indexed.append(self.index_file(row["id"], force=force))
        graph = rebuild_graph(self.db) if rebuild and indexed else self.graph_status()
        return {"indexed": indexed, "graph": graph}

    def rebuild_graph(self):
        return rebuild_graph(self.db)

    def warm(self):
        """Preload the semantic embedding model and report warmup latency."""
        return self.embedder.warm()

    def graph_status(self):
        return {"nodes": self.db.execute("SELECT count(*) FROM files").fetchone()[0],
                "edges": self.db.execute("SELECT count(*) FROM file_relations").fetchone()[0]}

    def status(self):
        total = self.db.execute("SELECT count(*) FROM files").fetchone()[0]
        embedded = self.db.execute("SELECT count(*) FROM embeddings e JOIN files f ON f.id=e.file_id WHERE e.content_hash=f.hash").fetchone()[0]
        semantic = self.db.execute("SELECT count(*) FROM embeddings e JOIN files f ON f.id=e.file_id WHERE e.content_hash=f.hash AND e.semantic=1").fetchone()[0]
        providers = [dict(r) for r in self.db.execute('''SELECT provider,model,semantic,count(*) AS files
            FROM embeddings GROUP BY provider,model,semantic ORDER BY files DESC''')]
        return {"files": total, "embedded": embedded, "semantic_embedded": semantic,
                "pending": max(0, total - embedded), "providers": providers,
                "graph": self.graph_status(), "embedding_router": self.embedder.status()}

    def _rows(self):
        rows = self.catalog.files()["files"]
        result = []
        for item in rows:
            embedding = self.db.execute("SELECT * FROM embeddings WHERE file_id=? AND content_hash=?", (item["id"], item["hash"])).fetchone()
            vector = None
            if embedding:
                try:
                    vector = json.loads(embedding["vector"])
                except ValueError:
                    vector = None
            result.append({
                "file_id": item["id"], "path": item["path"], "summary": item.get("summary") or "",
                "keywords": item.get("keywords") or [], "project": item.get("project") or "",
                "document_type": item.get("document_type") or "", "last_viewed": item.get("last_viewed"),
                "pinned": item.get("pinned", 0), "metadata_status": item.get("metadata_status"),
                "embedding_provider": embedding["provider"] if embedding else None,
                "embedding_model": embedding["model"] if embedding else None,
                "embedding_semantic": bool(embedding["semantic"]) if embedding else False,
                "_vector": vector,
            })
        return result


    @staticmethod
    def _folder_scope_scores(query, rows, embedded_query):
        """Infer relevant top-level folders, then boost descendants.

        This is intentionally lightweight hierarchical retrieval: folder names and
        common human synonyms provide lexical scope, while the centroid of child
        file embeddings provides semantic scope. No separate graph database is
        required.
        """
        from collections import defaultdict
        from .embeddings import cosine
        q=query.casefold()
        aliases={
            "classes": ("class", "classes", "school", "course", "courses", "coursework", "homework", "lecture", "academic"),
            "research": ("research", "paper", "papers", "study", "studies"),
            "projects": ("project", "projects", "build", "prototype"),
            "notes": ("note", "notes", "memo"),
            "downloads": ("download", "downloads"),
        }
        groups=defaultdict(list)
        for row in rows:
            top=row["path"].split("/",1)[0]
            groups[top].append(row)
        scores={}
        for folder,members in groups.items():
            lexical=0.0
            folded=folder.casefold()
            if folded in q:
                lexical=1.0
            for canonical,words in aliases.items():
                if folded==canonical and any(word in q for word in words):
                    lexical=1.0
            compatible=[r.get("_vector") for r in members if r.get("_vector") and
                r.get("embedding_provider")==embedded_query["provider"] and
                r.get("embedding_model")==embedded_query["model"] and
                bool(r.get("embedding_semantic"))==bool(embedded_query["semantic"])]
            semantic=0.0
            if compatible:
                dims=len(compatible[0]);vectors=[v for v in compatible if len(v)==dims]
                if vectors:
                    centroid=[sum(v[i] for v in vectors)/len(vectors) for i in range(dims)]
                    semantic=max(0.0,cosine(embedded_query["vector"],centroid))
            scores[folder]=max(lexical,semantic)
        return scores

    @staticmethod
    def _dynamic_select(query, ranked, min_results=4, max_results=32):
        if not ranked:
            return []
        q=query.casefold()
        broad=any(token in q for token in ("everything", "all files", "find files", "related", "organize", "group", "put them", "move them", "consolidate", "combine"))
        max_results=max_results if broad else min(max_results, 10)
        best=max(float(ranked[0].get("score",0.0)), 1e-9)
        # Broad queries tolerate a wider semantic neighborhood; narrow queries are stricter.
        relative_floor=0.48 if broad else 0.62
        absolute_floor=0.16 if broad else 0.20
        selected=[]
        previous=None
        for item in ranked[:max_results]:
            score=float(item.get("score",0.0))
            if len(selected) >= min_results:
                if score < absolute_floor or score < best * relative_floor:
                    break
                # A large confidence cliff is a useful natural stopping point.
                if previous is not None and (previous-score) >= 0.12 and score < best*0.75:
                    break
            selected.append(item)
            previous=score
        return selected

    def retrieve(self, query, limit=0, debug=False, include_excerpts=False, max_chars=12000):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query cannot be empty")
        start = time.monotonic()
        # Query latency must not depend on indexing work. The background worker keeps
        # embeddings/graph current. Only bootstrap synchronously when no usable index exists.
        self.cleanup()
        current = self.db.execute("SELECT count(*) FROM embeddings e JOIN files f ON f.id=e.file_id WHERE e.content_hash=f.hash").fetchone()[0]
        if current == 0:
            self.refresh(limit=8, rebuild=True)
        embedded_query = self.embedder.embed(query)
        terms = query_terms(query)
        rows = self._rows()
        # Never compare vectors from incompatible embedding spaces. If Ollama
        # temporarily fails and the query falls back to hashed vectors, old
        # all-minilm vectors remain useful on disk but are not mathematically
        # comparable to the fallback query vector. Metadata/path/graph scoring
        # still works until the semantic provider recovers.
        for row in rows:
            if (row.get("embedding_provider") != embedded_query["provider"]
                    or row.get("embedding_model") != embedded_query["model"]
                    or bool(row.get("embedding_semantic")) != bool(embedded_query["semantic"])):
                row["_vector"] = None
        ranked = direct_scores(rows, embedded_query["vector"], terms)
        folder_scores = self._folder_scope_scores(query, rows, embedded_query)
        for item in ranked:
            top=item["path"].split("/",1)[0]
            folder_scope=float(folder_scores.get(top,0.0))
            item["score_breakdown"]["folder_scope"]=folder_scope
            # Strong folder intent should dominate vague document-level matches,
            # but semantic folder centroids only provide a modest boost.
            boost=0.28*folder_scope if folder_scope>=0.95 else 0.10*folder_scope
            item["score"]=min(1.0,item["score"]+boost)
            item["direct_score"]=item["score"]
        ranked.sort(key=lambda item:(item["score"],item["path"]),reverse=True)
        ranked = expand_graph(self.db, ranked)

        # Dynamic result count by default. A fixed positive limit still overrides this.
        # The retriever should return enough context for broad queries without blindly
        # sending the same number of files every time.
        requested = int(limit or 0)
        if requested > 0:
            selected = ranked[:max(1, min(requested, 24))]
        else:
            selected = self._dynamic_select(query, ranked)
        if include_excerpts:
            for item in selected[:4]:
                try:
                    content = self.catalog.read_file(item["file_id"], 0, 1600)["content"]
                    item["excerpt"] = " ".join(content.split())[:1600]
                except Exception:
                    item["excerpt"] = ""
        packet = compact_packet(query, selected, max_chars=max_chars, include_excerpts=include_excerpts)
        result = {
            "matches": [{k: v for k, v in item.items() if not k.startswith("_")} for item in selected],
            "context": packet,
            "method": "hybrid embedding + local metadata + path + activity + graph expansion",
            "query_embedding": {"provider": embedded_query["provider"], "model": embedded_query["model"],
                                "semantic": embedded_query["semantic"], "dimensions": len(embedded_query["vector"])},
            "elapsed_ms": round((time.monotonic() - start) * 1000, 2),
        }
        if debug:
            result["debug"] = {
                "terms": terms,
                "retrieval_status": self.status(),
                "weights": {"semantic": 0.55, "metadata": 0.25, "path": 0.10,
                            "activity": 0.05, "pinned": 0.05, "folder_scope": "0.10 semantic / 0.28 explicit",
                            "graph_boost": 0.15},
            }
        return result
