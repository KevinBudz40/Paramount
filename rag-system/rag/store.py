"""ChromaDB vector store wrapper."""
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from rag.schema import build_where_clause, deserialize_metadata, serialize_metadata

_COLLECTION = "rag_documents"
_EMBED_MODEL = "all-MiniLM-L6-v2"

# Module-level cache — one client + collection per persist_dir for the lifetime
# of the process. Avoids re-opening the DB on every call.
_cache: dict[str, object] = {}
_clients: dict[str, object] = {}


def _get_collection(persist_dir: str = "./chroma_db"):
    if persist_dir not in _cache:
        ef = SentenceTransformerEmbeddingFunction(model_name=_EMBED_MODEL)
        client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        _clients[persist_dir] = client
        _cache[persist_dir] = client.get_or_create_collection(_COLLECTION, embedding_function=ef)
    return _cache[persist_dir]


def checkpoint(persist_dir: str = "./chroma_db") -> None:
    """Flush the SQLite WAL to the main DB file. Call after large batch writes."""
    db_path = f"{persist_dir}/chroma.sqlite3"
    try:
        con = sqlite3.connect(db_path)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()
    except Exception:
        pass


def add_chunks(chunks: list[dict], doc_meta: dict | None = None, persist_dir: str = "./chroma_db") -> int:
    collection = _get_collection(persist_dir)
    base = serialize_metadata(doc_meta or {})

    ids, documents, metadatas = [], [], []
    for chunk in chunks:
        uid = hashlib.md5(f"{chunk['source']}:{chunk['chunk_index']}".encode()).hexdigest()
        ids.append(uid)
        documents.append(chunk["text"])
        metadatas.append({**base, "source": chunk["source"], "chunk_index": chunk["chunk_index"]})

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    # Checkpoint every 500 chunks to flush WAL and reduce corruption risk on kill
    if len(ids) >= 500:
        checkpoint(persist_dir)
    return len(ids)


def query(
    question: str,
    n_results: int = 10,
    filters: dict | None = None,
    persist_dir: str = "./chroma_db",
) -> list[dict]:
    collection = _get_collection(persist_dir)
    if collection.count() == 0:
        return []

    where = build_where_clause(filters or {})

    # Pull a large candidate pool so diversity reranking has material to work with
    candidate_n = min(n_results * 5, collection.count())
    kwargs: dict = dict(query_texts=[question], n_results=candidate_n)
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)
    items = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        item = deserialize_metadata(meta)
        item["text"] = doc
        item["score"] = round(1 - dist, 3)
        items.append(item)

    # If the user explicitly filtered (e.g. by source or doc_type), skip diversity
    # reranking — they already narrowed the pool intentionally.
    if not filters:
        items = _diverse_rerank(items, n_results)
    else:
        items = items[:n_results]

    return items


def _generate_hypothetical(question: str) -> str:
    """
    HyDE: ask Claude to write a short hypothetical document that would answer
    the question. Embedding this hypothetical matches document language better
    than embedding the question itself.
    """
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


def hyde_query(
    question: str,
    n_results: int = 10,
    filters: dict | None = None,
    persist_dir: str = "./chroma_db",
    hypothetical: str = "",
) -> list[dict]:
    """
    HyDE + multi-query retrieval:
    1. Use pre-generated hypothetical passage (generated in main request thread)
    2. Retrieve chunks using the hypothetical (catches document-language matches)
    3. Retrieve chunks using the original question (catches keyword matches)
    4. Merge, deduplicate by source, and rerank — best of both searches
    """
    collection = _get_collection(persist_dir)
    if collection.count() == 0:
        return []

    where = build_where_clause(filters or {})
    candidate_n = min(n_results * 5, collection.count())

    if not hypothetical:
        return query(question, n_results=n_results, filters=filters, persist_dir=persist_dir)

    # Query with both the pre-generated hypothetical and the original question
    query_texts = [hypothetical, question]
    kwargs: dict = dict(query_texts=query_texts, n_results=candidate_n)
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)

    # Merge results from both queries — keep best score per unique source+chunk
    seen: dict[str, dict] = {}  # key: "source:chunk_index"
    for query_idx in range(len(query_texts)):
        for doc, meta, dist in zip(
            results["documents"][query_idx],
            results["metadatas"][query_idx],
            results["distances"][query_idx],
        ):
            item = deserialize_metadata(meta)
            item["text"] = doc
            item["score"] = round(1 - dist, 3)
            dedup_key = f"{item.get('source','')}:{meta.get('chunk_index', 0)}"
            if dedup_key not in seen or item["score"] > seen[dedup_key]["score"]:
                seen[dedup_key] = item

    merged = list(seen.values())
    merged.sort(key=lambda x: x["score"], reverse=True)

    if not filters:
        return _diverse_rerank(merged, n_results)
    return merged[:n_results]


def _diverse_rerank(items: list[dict], n: int) -> list[dict]:
    """
    Return up to n items ensuring no single source type crowds out others.

    Strategy:
    - Emails (source starts with "email:") are capped at half the result set.
    - Within the email cap, only the highest-scoring email per thread is kept
      (thread_id dedup) to avoid the same conversation filling all email slots.
    - Remaining slots go to the highest-scoring non-email chunks.
    - If one bucket doesn't have enough items, the other fills the remainder.
    """
    email_cap = max(1, n // 2)

    emails: list[dict] = []
    docs: list[dict] = []
    seen_threads: set[str] = set()

    for item in items:
        src = item.get("source", "")
        if src.startswith("email:"):
            tid = item.get("thread_id", src)
            if tid in seen_threads:
                continue
            seen_threads.add(tid)
            emails.append(item)
        else:
            docs.append(item)

    # Fill slots: docs first up to (n - email_cap), then emails up to email_cap
    doc_slots  = n - email_cap
    selected   = docs[:doc_slots] + emails[:email_cap]

    # If either bucket was short, let the other fill the remainder
    if len(selected) < n:
        used = set(id(x) for x in selected)
        extras = [x for x in (docs + emails) if id(x) not in used]
        selected += extras[:n - len(selected)]

    # Re-sort by score so Claude sees best matches first
    selected.sort(key=lambda x: x["score"], reverse=True)
    return selected


def list_sources(persist_dir: str = "./chroma_db") -> list[dict]:
    """One representative deserialized metadata record per source file."""
    collection = _get_collection(persist_dir)
    total = collection.count()
    if total == 0:
        return []

    seen: dict[str, dict] = {}
    page = 2000
    offset = 0
    while offset < total:
        batch = collection.get(include=["metadatas"], limit=page, offset=offset)
        for meta in batch["metadatas"]:
            src = meta.get("source", "")
            if src and src not in seen:
                seen[src] = deserialize_metadata(meta)
        offset += page

    return [{"source": k, **v} for k, v in sorted(seen.items())]


def delete_source(source_name: str, persist_dir: str = "./chroma_db") -> int:
    collection = _get_collection(persist_dir)
    results = collection.get(where={"source": source_name}, include=["metadatas"])
    ids = results["ids"]
    if ids:
        collection.delete(ids=ids)
    return len(ids)


def doc_count(persist_dir: str = "./chroma_db") -> int:
    return _get_collection(persist_dir).count()


def ingested_sources(persist_dir: str = "./chroma_db") -> set[str]:
    """Return the set of all source values currently in the collection."""
    collection = _get_collection(persist_dir)
    total = collection.count()
    if total == 0:
        return set()

    sources: set[str] = set()
    page = 2000
    offset = 0
    while offset < total:
        batch = collection.get(include=["metadatas"], limit=page, offset=offset)
        for m in batch["metadatas"]:
            src = m.get("source", "")
            if src:
                sources.add(src)
        offset += page
    return sources


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


_METADATA_FIELDS = (
    "source", "doc_type", "author", "sender_email", "folder",
    "topic", "date", "ingested_at",
)


def _scan_all_metadata(persist_dir: str):
    """Yield metadata dicts for every chunk, read directly from SQLite (fast bulk path)."""
    db_path = f"{persist_dir}/chroma.sqlite3"
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in _METADATA_FIELDS)
        rows = con.execute(
            f"SELECT id, key, string_value FROM embedding_metadata "
            f"WHERE key IN ({placeholders}) ORDER BY id",
            _METADATA_FIELDS,
        ).fetchall()
    finally:
        con.close()

    by_id: dict[int, dict] = {}
    for r in rows:
        if r["string_value"] is not None:
            by_id.setdefault(r["id"], {})[r["key"]] = r["string_value"]
    return by_id.values()


def recent_items(hours: int = 48, limit: int = 20, persist_dir: str = "./chroma_db") -> list[dict]:
    """Most recently ingested sources (one row per source, not per chunk)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    by_source: dict[str, dict] = {}

    for m in _scan_all_metadata(persist_dir):
        raw_ts = m.get("ingested_at") or m.get("date") or ""
        dt = _parse_dt(raw_ts)
        if not dt or dt < cutoff:
            continue
        src = m.get("source", "")
        if not src:
            continue
        existing = by_source.get(src)
        if existing is None or dt > existing["_dt"]:
            by_source[src] = {
                "source":       src,
                "doc_type":     m.get("doc_type", ""),
                "author":       m.get("author", ""),
                "sender_email": m.get("sender_email", ""),
                "topic":        m.get("topic", ""),
                "date":         m.get("date", "") or raw_ts,
                "ingested_at":  m.get("ingested_at", ""),
                "_dt":          dt,
            }

    items = sorted(by_source.values(), key=lambda x: x["_dt"], reverse=True)[:limit]
    for it in items:
        it.pop("_dt", None)
    return items


def stakeholder_pulse(names: list[str], persist_dir: str = "./chroma_db") -> list[dict]:
    """Last-contact date/topic per stakeholder, matched against author / sender_email."""
    name_parts = {n: [p.lower() for p in n.split() if p] for n in names}
    latest: dict[str, dict] = {n: None for n in names}

    for m in _scan_all_metadata(persist_dir):
        author = (m.get("author") or "").lower()
        sender = (m.get("sender_email") or "").lower()
        if not author and not sender:
            continue
        for orig, parts in name_parts.items():
            full = " ".join(parts)
            matched = full in author or any(p and p in sender for p in parts if len(p) > 2)
            if not matched:
                continue
            raw_ts = m.get("date") or m.get("ingested_at") or ""
            dt = _parse_dt(raw_ts)
            if dt is None:
                continue
            cur = latest[orig]
            if cur is None or dt > cur["_dt"]:
                latest[orig] = {
                    "date":  m.get("date", "") or raw_ts,
                    "topic": m.get("topic", "") or m.get("source", ""),
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
