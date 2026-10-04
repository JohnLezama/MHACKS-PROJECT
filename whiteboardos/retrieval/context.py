"""Build token-conscious LLM context packets from ranked results."""


def compact_packet(query, results, max_chars=12000, include_excerpts=False):
    max_chars = max(1000, min(int(max_chars), 50000))
    packet = {
        "query": query,
        "retrieval_method": "whiteboard_hybrid_v1",
        "files": [],
    }
    used = len(query) + 120
    for result in results:
        item = {
            "file_id": result["file_id"],
            "path": result["path"],
            "score": round(result["score"], 4),
            "summary": result.get("summary") or "",
            "keywords": result.get("keywords") or [],
            "project": result.get("project") or "",
            "document_type": result.get("document_type") or "",
            "related_via": result.get("related_via") or [],
        }
        if include_excerpts and result.get("excerpt"):
            item["excerpt"] = result["excerpt"]
        cost = sum(len(str(value)) for value in item.values()) + 80
        if packet["files"] and used + cost > max_chars:
            break
        packet["files"].append(item)
        used += cost
    packet["approx_chars"] = used
    return packet
