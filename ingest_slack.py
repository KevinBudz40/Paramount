# ingest_slack.py
# [2026-10-12] NEW: Slack conversation ingest — direct to Qdrant, no Flask needed.
#
# Input: one JSON file per conversation in C:\ParamountRAG\slack\ (exported in-session
# via the Slack MCP connector — the connector cannot be called from Python).
#   {"conversation", "channel_id", "type", "workspace", "members_note",
#    "messages": [{"dt": "YYYY-MM-DD HH:MM:SS", "author", "text", "replies": [{"author","text"}]}]}
#
# One RAG document = one conversation-day.  source = "slack:<conversation>:<YYYY-MM-DD>"
# Re-running is idempotent (chunk IDs are md5(source:chunk_index) → Qdrant upsert).
#
# Chunking: message-boundary windows (~800 chars, last 2 messages carried as overlap),
# each chunk prefixed with a header line.  Does NOT use rag.chunker._chunk_text — that
# splits on sentence punctuation, which chat logs mostly lack (would produce huge chunks).
#
# Usage:
#   python C:\GIT\ingest_slack.py            # dry run — counts + sample, writes nothing
#   python C:\GIT\ingest_slack.py --apply    # write to Qdrant + source_log
#   Stop Streamlit / Flask first (Qdrant embedded lock).

import argparse, gc, json, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, r"C:\GIT\rag-system-qdrant")
sys.path.insert(0, r"C:\ParamountRAG\scripts")

QDRANT_DIR  = r"C:\GIT\qdrant_db"
SLACK_DIR   = Path(r"C:\ParamountRAG\slack")
CUTOFF      = "2026-06-01"   # never ingest content before this date (project rule)
MIN_CHARS   = 150            # skip conversation-days that are only chit-chat
CHUNK_SIZE  = 800
OVERLAP_MSG = 2
SELF_NAME   = "Kevin Budziszewski"


def _msg_lines(m: dict) -> list[str]:
    time = m["dt"][11:16]
    lines = [f"[{time}] {m['author']}: {m['text'].strip()}"]
    for r in m.get("replies", []):
        lines.append(f"    ↳ {r['author']}: {r['text'].strip()}")
    return lines


def _chunk_messages(blocks: list[str], header: str) -> list[str]:
    chunks, current, cur_len = [], [], 0
    for b in blocks:
        if cur_len + len(b) > CHUNK_SIZE and current:
            chunks.append(header + "\n" + "\n".join(current))
            current = current[-OVERLAP_MSG:]
            cur_len = sum(len(x) for x in current)
        current.append(b)
        cur_len += len(b)
    if current:
        chunks.append(header + "\n" + "\n".join(current))
    return chunks


def build_documents(path: Path) -> list[dict]:
    conv = json.loads(path.read_text(encoding="utf-8"))
    name = conv["conversation"]
    by_day: dict[str, list[dict]] = defaultdict(list)
    for m in conv["messages"]:
        day = m["dt"][:10]
        if day < CUTOFF:
            continue
        by_day[day].append(m)

    docs = []
    for day, msgs in sorted(by_day.items()):
        msgs.sort(key=lambda m: m["dt"])
        body_chars = sum(len(m["text"]) + sum(len(r["text"]) for r in m.get("replies", [])) for m in msgs)
        if body_chars < MIN_CHARS:
            continue
        authors = sorted({m["author"] for m in msgs} | {r["author"] for m in msgs for r in m.get("replies", [])})
        header = (f"Slack {conv['type'].replace('_', ' ')}: {name} | Date: {day} | "
                  f"Participants: {', '.join(authors)}")
        blocks = ["\n".join(_msg_lines(m)) for m in msgs]
        source = f"slack:{name}:{day}"
        mentioned = SELF_NAME in authors or any(SELF_NAME in m["text"] or "@Kevin" in m["text"] for m in msgs)
        docs.append({
            "source": source,
            "chunks": _chunk_messages(blocks, header),
            "doc_meta": {
                "doc_id":           f"{conv['channel_id']}:{day}",
                "thread_id":        conv["channel_id"],
                "doc_type":         "Slack",
                "source":           source,
                "author":           ", ".join(authors),
                "folder":           f"Slack/{conv['workspace']}/{name}",
                "recipients":       authors,
                "mentioned_people": authors,
                "date":             day,
                "topic":            f"Slack {name} {day}",
                "keywords":         ["Paramount", "Slack"] + (["Kevin"] if mentioned else []),
                "workstream":       "",
            },
            "path": path,
        })
    return docs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write to Qdrant (default: dry run)")
    ap.add_argument("--dir", default=str(SLACK_DIR))
    args = ap.parse_args()

    files = sorted(Path(args.dir).glob("*.json"))
    all_docs = []
    for f in files:
        docs = build_documents(f)
        n_chunks = sum(len(d["chunks"]) for d in docs)
        print(f"  {f.name:45s} {len(docs):3d} days  {n_chunks:4d} chunks")
        all_docs.extend(docs)

    total_chunks = sum(len(d["chunks"]) for d in all_docs)
    print(f"\n{len(files)} files → {len(all_docs)} conversation-days → {total_chunks} chunks")

    if not args.apply:
        if all_docs:
            print("\n--- sample chunk ---\n" + all_docs[0]["chunks"][0][:1200])
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return

    from rag.store import add_chunks, close_client
    import source_log

    written = 0
    for i, d in enumerate(all_docs, 1):
        chunks = [{"text": t, "source": d["source"], "chunk_index": j} for j, t in enumerate(d["chunks"])]
        add_chunks(chunks, doc_meta=d["doc_meta"], persist_dir=QDRANT_DIR)
        source_log.upsert(
            source_key=d["source"],
            full_path=str(d["path"]),
            content_hash=source_log.file_md5(d["path"]),
            doc_type="Slack",
            file_size=d["path"].stat().st_size,
            chunk_count=len(chunks),
        )
        written += len(chunks)
        if i % 25 == 0:
            gc.collect()
            print(f"  {i}/{len(all_docs)} docs ({written} chunks)")

    print(f"\nSlack ingest complete. {len(all_docs)} conversation-days, {written} chunks.")
    close_client(QDRANT_DIR)
    gc.collect()


if __name__ == "__main__":
    main()
