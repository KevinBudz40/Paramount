# ingest_emails_batched.py
# [2026-08-21] Safe email ingest — direct to Qdrant, no Flask needed.
# [2026-08-22] Rewritten: bypasses Flask entirely, writes directly to qdrant_db.
#
# Usage:
#   python C:\GIT\ingest_emails_batched.py                  # default
#   python C:\GIT\ingest_emails_batched.py --json <path>    # override emails.json path

import argparse, sys, gc
from pathlib import Path

sys.path.insert(0, r"C:\GIT\rag-system-qdrant")
sys.path.insert(0, r"C:\ParamountRAG\scripts")

from rag.email_json import parse_email_json
from rag.store import add_chunks, close_client

QDRANT_DIR   = r"C:\GIT\qdrant_db"
DEFAULT_JSON = Path(r"C:\ParamountRAG\outlook\email\emails.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=str(DEFAULT_JSON), help="Path to emails.json")
    args = parser.parse_args()

    json_path = Path(args.json)
    if not json_path.exists():
        print(f"ERROR: {json_path} not found")
        sys.exit(1)

    print(f"Parsing {json_path} ...")
    chunks, result = parse_email_json(json_path)
    print(f"  {result.processed} emails → {result.total_chunks} chunks "
          f"(skipped: folder={result.skipped_folder} sender={result.skipped_sender} empty={result.skipped_empty})")

    # Group chunks by source (one source = one email)
    from collections import defaultdict
    by_source: dict[str, list[dict]] = defaultdict(list)
    for chunk in chunks:
        by_source[chunk["source"]].append(chunk)

    sources = list(by_source.keys())
    total_emails = len(sources)
    print(f"  {total_emails} unique emails\n")

    total_chunks_written = 0
    for i, src in enumerate(sources, 1):
        email_chunks = by_source[src]
        for chunk in email_chunks:
            add_chunks(
                [{"text": chunk["text"], "source": chunk["source"], "chunk_index": chunk["chunk_index"]}],
                doc_meta=chunk["doc_meta"],
                persist_dir=QDRANT_DIR,
            )
        total_chunks_written += len(email_chunks)
        if i % 50 == 0:
            gc.collect()
            print(f"  {i}/{total_emails} emails done ({total_chunks_written} chunks)")

    print(f"\nEmail ingest complete. {total_emails} emails, {total_chunks_written} chunks.")
    close_client(QDRANT_DIR)
    gc.collect()


if __name__ == "__main__":
    main()
