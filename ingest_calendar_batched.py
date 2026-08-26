# ingest_calendar_batched.py
# [2026-08-22] Calendar ingest — direct to Qdrant, no Flask needed.
#
# Usage:
#   python C:\GIT\ingest_calendar_batched.py                   # default
#   python C:\GIT\ingest_calendar_batched.py --json <path>     # override meetings.json path
#   python C:\GIT\ingest_calendar_batched.py --since 2026-08-01  # only meetings on/after date

import argparse
import gc
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, r"C:\GIT\rag-system-qdrant")
sys.path.insert(0, r"C:\ParamountRAG\scripts")

from rag.chunker import _chunk_text
from rag.store import add_chunks, close_client  # [2026-08-26] added close_client import

QDRANT_DIR   = r"C:\GIT\qdrant_db"
DEFAULT_JSON = Path(r"C:\ParamountRAG\outlook\calendar\meetings.json")
GC_EVERY     = 50


class _HTMLStripper(HTMLParser):
    _SKIP = {"style", "script", "head"}
    def __init__(self):
        super().__init__(); self._parts = []; self._skip = 0
    def handle_starttag(self, tag, attrs):
        if tag.lower() in self._SKIP: self._skip += 1
    def handle_endtag(self, tag):
        if tag.lower() in self._SKIP and self._skip > 0: self._skip -= 1
    def handle_data(self, data):
        if not self._skip: self._parts.append(data)
    def get_text(self):
        return re.sub(r'\n{3,}', '\n\n', "\n".join(self._parts)).strip()


def _strip_html(html: str) -> str:
    s = _HTMLStripper(); s.feed(html or ""); return s.get_text()


def _meeting_to_chunks(m: dict) -> tuple[list[dict], dict] | None:
    """Convert a meeting record to (chunks, doc_meta). Returns None to skip."""
    title   = m.get("title") or m.get("subject") or ""
    start   = m.get("start_time", "")
    end     = m.get("end_time", "")
    org     = m.get("organizer", "")
    loc     = m.get("location", "")
    dur     = m.get("duration_minutes", "")
    body    = _strip_html(m.get("body", "") or "").strip()
    cats    = ", ".join(m.get("categories") or [])

    # Attendee list
    attendees = m.get("attendees") or []
    att_lines = [
        f"  - {a.get('name','')} <{a.get('email','')}> ({a.get('status','')})"
        for a in attendees
        if a.get("name") or a.get("email")
    ]

    # Build full text block
    parts = [f"Meeting: {title}", f"Date: {start} – {end} ({dur} min)"]
    if org:  parts.append(f"Organizer: {org}")
    if loc:  parts.append(f"Location: {loc}")
    if cats: parts.append(f"Categories: {cats}")
    if att_lines:
        parts.append("Attendees:")
        parts.extend(att_lines)
    if body:
        parts.append("")
        parts.append(body)

    full_text = "\n".join(parts).strip()
    if not full_text:
        return None

    # Source key: calendar:YYYY-MM-DD:Title
    date_part = start[:10] if start else "unknown"
    safe_title = re.sub(r'[^\w\s-]', '', title)[:80].strip()
    source_key = f"calendar:{date_part}:{safe_title}"

    chunks_text = _chunk_text(full_text)
    if not chunks_text:
        return None

    chunks = [
        {"text": t, "source": source_key, "chunk_index": i}
        for i, t in enumerate(chunks_text)
    ]

    # date field: ISO date only
    doc_meta = {
        "doc_type":    "Meeting",
        "author":      org,
        "date":        date_part,
        "topic":       title,
        "doc_id":      m.get("entry_id", ""),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }
    return chunks, doc_meta


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json",  default=str(DEFAULT_JSON), help="Path to meetings.json")
    parser.add_argument("--since", default="",               help="Only ingest meetings on/after YYYY-MM-DD")
    args = parser.parse_args()

    json_path = Path(args.json)
    if not json_path.exists():
        print(f"ERROR: {json_path} not found"); sys.exit(1)

    print(f"Loading {json_path} ...")
    data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
    meetings = data.get("meetings", data) if isinstance(data, dict) else data
    print(f"  {len(meetings)} meetings in file")

    if args.since:
        meetings = [m for m in meetings if (m.get("start_time") or "") >= args.since]
        print(f"  {len(meetings)} meetings on/after {args.since}")

    total      = len(meetings)
    ingested   = skipped = errors = total_chunks = 0

    for i, m in enumerate(meetings, 1):
        title = m.get("title") or m.get("subject") or "(no title)"
        try:
            result = _meeting_to_chunks(m)
            if result is None:
                print(f"  [{i}/{total}] SKIP (empty)  {title[:60]}")
                skipped += 1
                continue

            chunks, doc_meta = result
            add_chunks(chunks, doc_meta=doc_meta, persist_dir=QDRANT_DIR)
            total_chunks += len(chunks)
            print(f"  [{i}/{total}] OK  {len(chunks):>3} chunks  {title[:60]}")
            ingested += 1

        except Exception as exc:
            print(f"  [{i}/{total}] ERR  {title[:60]} — {exc}")
            errors += 1

        if i % GC_EVERY == 0:
            gc.collect()
            print(f"  --- {i}/{total} done ---")

    print(f"\nCalendar ingest complete: {ingested} ingested, {skipped} skipped, {errors} errors, {total_chunks} chunks total.")
    close_client(QDRANT_DIR)  # [2026-08-26] explicit close before exit — prevents QdrantClient.__del__ ImportError on shutdown
    gc.collect()


if __name__ == "__main__":
    main()
