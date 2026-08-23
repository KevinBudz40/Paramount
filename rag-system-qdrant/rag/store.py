"""Qdrant vector store wrapper — replaces ChromaDB store.
[2026-08-21] Full rewrite from chromadb to qdrant-client (embedded mode).
Embedding model unchanged: all-MiniLM-L6-v2 (384 dims).
Public API identical to the ChromaDB version so app.py needs no changes.
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)
from sentence_transformers import SentenceTransformer

from rag.schema import FILTERABLE_FIELDS, deserialize_metadata, serialize_metadata

_COLLECTION   = "rag_documents"
_EMBED_MODEL  = "all-MiniLM-L6-v2"
_VECTOR_SIZE  = 384

# Module-level cache — one client + model per persist_dir for the lifetime of the process
_clients: dict[str, QdrantClient] = {}
_models:  dict[str, SentenceTransformer] = {}


def _get_client(persist_dir: str) -> QdrantClient:
    if persist_dir not in _clients:
        client = QdrantClient(path=persist_dir)
        if not client.collection_exists(_COLLECTION):
            client.create_collection(
                _COLLECTION,
                vectors_config=VectorParams(size=_VECTOR_SIZE, distance=Distance.COSINE),
            )
        _clients[persist_dir] = client
    return _clients[persist_dir]


def _get_model(persist_dir: str) -> SentenceTransformer:
    if persist_dir not in _models:
        _models[persist_dir] = SentenceTransformer(_EMBED_MODEL)
    return _models[persist_dir]


def _chunk_id(source: str, chunk_index: int) -> str:
    """Stable UUID derived from source + chunk_index (same algorithm as before)."""
    hex_str = hashlib.md5(f"{source}:{chunk_index}".encode()).hexdigest()
    return str(uuid.UUID(hex_str))


def _build_filter(filters: dict) -> Filter | None:
    """Convert a flat filter dict to a Qdrant Filter."""
    conditions = []
    for k, v in filters.items():
        if k not in FILTERABLE_FIELDS or v is None or v == "" or v == []:
            continue
        if isinstance(v, list):
            conditions.append(FieldCondition(key=k, match=MatchAny(any=[str(i) for i in v])))
        else:
            conditions.append(FieldCondition(key=k, match=MatchValue(value=str(v))))
    return Filter(must=conditions) if conditions else None


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def add_chunks(chunks: list[dict], doc_meta: dict | None = None, persist_dir: str = "./qdrant_db") -> int:
    client = _get_client(persist_dir)
    model  = _get_model(persist_dir)
    base   = serialize_metadata(doc_meta or {})

    texts, ids, payloads = [], [], []
    for chunk in chunks:
        texts.append(chunk["text"])
        ids.append(_chunk_id(chunk["source"], chunk["chunk_index"]))
        payloads.append({**base, "source": chunk["source"], "chunk_index": chunk["chunk_index"], "text": chunk["text"]})

    vectors = model.encode(texts, show_progress_bar=False).tolist()

    BATCH = 500
    for i in range(0, len(ids), BATCH):
        points = [
            PointStruct(id=ids[j], vector=vectors[j], payload=payloads[j])
            for j in range(i, min(i + BATCH, len(ids)))
        ]
        client.upsert(collection_name=_COLLECTION, points=points)

    return len(ids)


def delete_source(source_name: str, persist_dir: str = "./qdrant_db") -> int:
    client = _get_client(persist_dir)
    f = Filter(must=[FieldCondition(key="source", match=MatchValue(value=source_name))])
    result = client.delete(collection_name=_COLLECTION, points_selector=f)
    return getattr(result, "deleted", 0)


# ---------------------------------------------------------------------------
# Read / query
# ---------------------------------------------------------------------------

def query(
    question: str,
    n_results: int = 10,
    filters: dict | None = None,
    persist_dir: str = "./qdrant_db",
) -> list[dict]:
    client = _get_client(persist_dir)
    model  = _get_model(persist_dir)

    if doc_count(persist_dir) == 0:
        return []

    vector = model.encode([question], show_progress_bar=False)[0].tolist()
    qf = _build_filter(filters or {})

    # [2026-08-23] qdrant-client 1.7+ replaced search() with query_points()
    result = client.query_points(
        collection_name=_COLLECTION,
        query=vector,
        limit=n_results * 5,
        query_filter=qf,
        with_payload=True,
    )

    items = []
    for hit in result.points:
        payload = hit.payload or {}
        item = deserialize_metadata(payload)
        item["text"]  = payload.get("text", "")
        item["score"] = round(hit.score, 3)
        items.append(item)

    if not filters:
        items = _diverse_rerank(items, n_results)
    else:
        items = items[:n_results]

    return items


def hyde_query(
    question: str,
    n_results: int = 10,
    filters: dict | None = None,
    persist_dir: str = "./qdrant_db",
    hypothetical: str = "",
    extra_queries: list[str] | None = None,
) -> list[dict]:
    """HyDE + multi-query retrieval. Merges results from all query variants."""
    client = _get_client(persist_dir)
    model  = _get_model(persist_dir)

    if doc_count(persist_dir) == 0:
        return []

    if not hypothetical and not extra_queries:
        return query(question, n_results=n_results, filters=filters, persist_dir=persist_dir)

    query_texts = [question]
    if hypothetical:
        query_texts.append(hypothetical)
    for q in (extra_queries or []):
        if q and q not in query_texts:
            query_texts.append(q)

    vectors = model.encode(query_texts, show_progress_bar=False).tolist()
    qf = _build_filter(filters or {})
    candidate_n = n_results * 5

    seen: dict[str, dict] = {}
    for vec in vectors:
        result = client.query_points(
            collection_name=_COLLECTION,
            query=vec,
            limit=candidate_n,
            query_filter=qf,
            with_payload=True,
        )
        for hit in result.points:
            payload = hit.payload or {}
            item = deserialize_metadata(payload)
            item["text"]  = payload.get("text", "")
            item["score"] = round(hit.score, 3)
            key = f"{item.get('source','')}:{payload.get('chunk_index', 0)}"
            if key not in seen or item["score"] > seen[key]["score"]:
                seen[key] = item

    merged = sorted(seen.values(), key=lambda x: x["score"], reverse=True)

    if not filters:
        return _diverse_rerank(merged, n_results)
    return merged[:n_results]


# ---------------------------------------------------------------------------
# Neighbour / context expansion
# ---------------------------------------------------------------------------

def _fetch_neighbours(
    source: str,
    chunk_index: int,
    persist_dir: str = "./qdrant_db",
) -> tuple[str, str]:
    """Return (prev_text, next_text) for adjacent chunks of the same source."""
    client = _get_client(persist_dir)
    prev_text = next_text = ""

    # text is stored in payload under key "text" — retrieve by stable chunk ID
    for target_idx, slot in ((chunk_index - 1, "prev"), (chunk_index + 1, "next")):
        if target_idx < 0:
            continue
        target_id = _chunk_id(source, target_idx)
        results = client.retrieve(
            collection_name=_COLLECTION,
            ids=[target_id],
            with_payload=True,
            with_vectors=False,
        )
        if results and results[0].payload:
            text = results[0].payload.get("text", "")
            if slot == "prev":
                prev_text = text
            else:
                next_text = text

    return prev_text, next_text


def expand_context(items: list[dict], persist_dir: str = "./qdrant_db") -> list[dict]:
    expanded = []
    for item in items:
        source      = item.get("source", "")
        chunk_index = item.get("chunk_index", 0)
        if not source:
            expanded.append(item)
            continue
        try:
            chunk_idx = int(chunk_index)
        except (TypeError, ValueError):
            expanded.append(item)
            continue

        prev_text, next_text = _fetch_neighbours(source, chunk_idx, persist_dir)

        parts = []
        if prev_text:
            parts.append(f"[preceding context]\n{prev_text}\n[/preceding context]")
        parts.append(item["text"])
        if next_text:
            parts.append(f"[following context]\n{next_text}\n[/following context]")

        enriched = dict(item)
        enriched["text"] = "\n\n".join(parts)
        expanded.append(enriched)

    return expanded


# ---------------------------------------------------------------------------
# Bulk reads
# ---------------------------------------------------------------------------

def _scroll_all_payloads(persist_dir: str) -> list[dict]:
    """Return all payloads from the collection via paginated scroll."""
    client = _get_client(persist_dir)
    all_payloads = []
    offset = None
    while True:
        hits, next_offset = client.scroll(
            collection_name=_COLLECTION,
            limit=2000,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for hit in hits:
            if hit.payload:
                all_payloads.append(hit.payload)
        if next_offset is None:
            break
        offset = next_offset
    return all_payloads


def list_sources(persist_dir: str = "./qdrant_db") -> list[dict]:
    """One representative metadata record per source file."""
    seen: dict[str, dict] = {}
    for payload in _scroll_all_payloads(persist_dir):
        src = payload.get("source", "")
        if src and src not in seen:
            seen[src] = deserialize_metadata(payload)
    return [{"source": k, **v} for k, v in sorted(seen.items())]


def doc_count(persist_dir: str = "./qdrant_db") -> int:
    client = _get_client(persist_dir)
    return client.get_collection(_COLLECTION).points_count


def ingested_sources(persist_dir: str = "./qdrant_db") -> set[str]:
    sources: set[str] = set()
    for payload in _scroll_all_payloads(persist_dir):
        src = payload.get("source", "")
        if src:
            sources.add(src)
    return sources


def recent_items(hours: int = 48, limit: int = 20, persist_dir: str = "./qdrant_db") -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    by_source: dict[str, dict] = {}

    for payload in _scroll_all_payloads(persist_dir):
        raw_ts = payload.get("ingested_at") or payload.get("date") or ""
        dt = _parse_dt(raw_ts)
        if not dt or dt < cutoff:
            continue
        src = payload.get("source", "")
        if not src:
            continue
        existing = by_source.get(src)
        if existing is None or dt > existing["_dt"]:
            by_source[src] = {
                "source":       src,
                "doc_type":     payload.get("doc_type", ""),
                "author":       payload.get("author", ""),
                "sender_email": payload.get("sender_email", ""),
                "topic":        payload.get("topic", ""),
                "date":         payload.get("date", "") or raw_ts,
                "ingested_at":  payload.get("ingested_at", ""),
                "_dt":          dt,
            }

    items = sorted(by_source.values(), key=lambda x: x["_dt"], reverse=True)[:limit]
    for it in items:
        it.pop("_dt", None)
    return items


def stakeholder_pulse(names: list[str], persist_dir: str = "./qdrant_db") -> list[dict]:
    name_parts = {n: [p.lower() for p in n.split() if p] for n in names}
    latest: dict[str, dict] = {n: None for n in names}

    for payload in _scroll_all_payloads(persist_dir):
        author = (payload.get("author") or "").lower()
        sender = (payload.get("sender_email") or "").lower()
        if not author and not sender:
            continue
        for orig, parts in name_parts.items():
            full = " ".join(parts)
            matched = full in author or any(p and p in sender for p in parts if len(p) > 2)
            if not matched:
                continue
            raw_ts = payload.get("date") or payload.get("ingested_at") or ""
            dt = _parse_dt(raw_ts)
            if dt is None:
                continue
            cur = latest[orig]
            if cur is None or dt > cur["_dt"]:
                latest[orig] = {
                    "date":  payload.get("date", "") or raw_ts,
                    "topic": payload.get("topic", "") or payload.get("source", ""),
                    "_dt":   dt,
                }

    now = datetime.now(timezone.utc)
    out = []
    for orig in names:
        rec = latest[orig]
        if rec:
            out.append({
                "name":       orig,
                "last_date":  rec["date"],
                "last_topic": rec["topic"],
                "days_since": (now - rec["_dt"]).days,
            })
        else:
            out.append({"name": orig, "last_date": "", "last_topic": "", "days_since": None})
    return out


# ---------------------------------------------------------------------------
# Query rewriting / HyDE helpers (unchanged — no ChromaDB dependency)
# ---------------------------------------------------------------------------

def _rewrite_queries(question: str) -> list[str]:
    import anthropic
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        system=(
            "You are a search query optimizer for an Oracle Fusion ERP project "
            "knowledge base containing emails, documents, and meeting notes. "
            "Given a question, output 2 alternative search queries that would "
            "retrieve different but relevant documents. Use specific Oracle/ERP "
            "terminology. Output ONLY the queries, one per line, no numbering, "
            "no explanation."
        ),
        messages=[{"role": "user", "content": question}],
    )
    lines = [l.strip() for l in resp.content[0].text.strip().splitlines() if l.strip()]
    return lines[:2]


def _generate_hypothetical(question: str) -> str:
    import anthropic
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=(
            "You are a project document generator for the Paramount Pictures "
            "Oracle Fusion ERP implementation. When given a question, write a "
            "short (3-5 sentence) passage that a real project document, email, "
            "or meeting note might contain that would directly answer it. "
            "Use the specific terminology, names, and language that would appear "
            "in Oracle ERP project documents. Do not answer the question directly — "
            "write the passage AS IF it were extracted from a project artifact."
        ),
        messages=[{"role": "user", "content": question}],
    )
    return resp.content[0].text.strip()


# ---------------------------------------------------------------------------
# Diversity reranking (unchanged)
# ---------------------------------------------------------------------------

def _diverse_rerank(items: list[dict], n: int) -> list[dict]:
    email_cap = max(1, n // 2)

    emails: list[dict] = []
    docs:   list[dict] = []
    seen_threads:     set[str] = set()
    seen_doc_sources: set[str] = set()

    for item in items:
        src = item.get("source", "")
        if src.startswith("email:"):
            tid = item.get("thread_id", src)
            if tid in seen_threads:
                continue
            seen_threads.add(tid)
            emails.append(item)
        else:
            if src in seen_doc_sources:
                continue
            seen_doc_sources.add(src)
            docs.append(item)

    doc_slots = n - email_cap
    selected  = docs[:doc_slots] + emails[:email_cap]

    if len(selected) < n:
        used   = set(id(x) for x in selected)
        extras = [x for x in (docs + emails) if id(x) not in used]
        selected += extras[:n - len(selected)]

    selected.sort(key=lambda x: x["score"], reverse=True)
    return selected


# ---------------------------------------------------------------------------
# Keyword search (INT### / RICEFW-### exact match)
# ---------------------------------------------------------------------------

def keyword_search(term: str, max_results: int = 5, persist_dir: str = "./qdrant_db") -> list[dict]:
    """Return chunks whose text contains the given term (case-insensitive scroll scan).
    Used for exact ID codes (INT###, RICEFW-###) that semantic search misses.
    Qdrant embedded doesn't support full-text index on payload text, so we scroll
    and filter in Python — fast enough at 150K chunks for a handful of ID lookups.
    """
    client = _get_client(persist_dir)
    term_lower = term.lower()
    hits: list[dict] = []
    offset = None
    while len(hits) < max_results:
        batch, next_offset = client.scroll(
            collection_name=_COLLECTION,
            limit=500,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in batch:
            if not point.payload:
                continue
            text = point.payload.get("text", "")
            if term_lower in text.lower():
                item = deserialize_metadata(point.payload)
                item["text"]  = text
                item["score"] = 1.0
                hits.append(item)
                if len(hits) >= max_results:
                    break
        if next_offset is None:
            break
        offset = next_offset
    return hits


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_dt(raw: str):
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None
