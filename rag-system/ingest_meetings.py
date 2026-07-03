"""
ingest_meetings.py — Ingest meetings.json calendar export into ChromaDB.

Usage:
    python ingest_meetings.py
    python ingest_meetings.py --json C:\\ParamountRAG\\outlook\\calendar\\meetings.json
"""
import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from rag.chunker import _chunk_text
from rag.store import add_chunks, doc_count

_DEFAULT_JSON = Path(r"C:\ParamountRAG\outlook\calendar\meetings.json")
_DEFAULT_CHROMA = str(_SCRIPT_DIR.parent / "chroma_db")
_BATCH = 500

# Keyword → workstream mapping (checked against lowercased title)
_WORKSTREAM_KEYWORDS = [
    (["agis", "intercompany", " ic "],          "AGIS"),
    (["o2c", "order to cash", "ar billing",
      "ar connect", "billing"],                  "O2C/AR"),
    (["integration", "integrations", "fbdi",
      "boleto", "inbound", "outbound"],          "Integrations"),
    (["project", "costing", "burden"],           "Projects/Costing"),
    (["procurement", "s2p", "source to pay",
      "purchase", "supplier"],                   "Procurement"),
    (["gl ", " gl", "general ledger", "ledger",
      "record to report", "r2r", "close"],       "Finance/GL"),
    (["uat", "testing", "cutover", "go-live",
      "go live", "hypercare"],                   "Cutover/UAT"),
    (["training", "curriculum", "change impact",
      "change management"],                      "Change Management"),
]


def _infer_workstream(title: str) -> str:
    t = (title or "").lower()
    for keywords, ws in _WORKSTREAM_KEYWORDS:
        if any(kw in t for kw in keywords):
            return ws
    return ""


def _attendee_names(attendees: list) -> list[str]:
    return [a.get("name", "") for a in (attendees or []) if a.get("name")]


def ingest_meetings(json_path: Path, chroma_dir: str) -> tuple[int, int]:
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    meetings = raw.get("meetings") or []

    total_chunks = 0
    processed = 0
    skipped_empty = 0

    for mtg in meetings:
        subject   = (mtg.get("subject") or mtg.get("title") or "").strip()
        start     = mtg.get("start_time") or "unknown"
        end       = mtg.get("end_time") or ""
        organizer = mtg.get("organizer") or ""
        location  = mtg.get("location") or ""
        body      = (mtg.get("body") or "").strip()
        duration  = mtg.get("duration_minutes")
        entry_id  = mtg.get("entry_id", "")
        attendees = _attendee_names(mtg.get("attendees") or [])
        categories = mtg.get("categories") or []
        workstream = _infer_workstream(subject)

        # Build chunk text
        parts = [f"Meeting: {subject}", f"Date: {start}"]
        if organizer:
            parts.append(f"Organizer: {organizer}")
        if attendees:
            parts.append(f"Attendees: {', '.join(attendees)}")
        if location:
            parts.append(f"Location: {location}")
        if duration:
            parts.append(f"Duration: {duration} minutes")
        if body:
            parts.append("")
            parts.append(body)

        full_text = "\n".join(parts).strip()
        if not full_text or not subject:
            skipped_empty += 1
            continue

        source = f"calendar:{start[:10] if start != 'unknown' else 'unknown'}:{subject[:55]}"

        doc_meta = {
            "doc_id":           entry_id,
            "doc_type":         "Meeting",
            "source":           source,
            "author":           organizer,
            "date":             start[:10] if start != "unknown" else "",
            "topic":            subject,
            "workstream":       workstream,
            "recipients":       attendees,
            "keywords":         ["Paramount", "calendar"] + categories,
            "meeting_date":     start[:10] if start != "unknown" else "",
        }

        chunks = [
            {"text": t, "source": source, "chunk_index": i}
            for i, t in enumerate(_chunk_text(full_text))
        ]
        if not chunks:
            skipped_empty += 1
            continue

        for batch_start in range(0, len(chunks), _BATCH):
            add_chunks(chunks[batch_start:batch_start + _BATCH],
                       doc_meta=doc_meta, persist_dir=chroma_dir)

        total_chunks += len(chunks)
        processed += 1
        ws_label = f"  [{workstream}]" if workstream else ""
        print(f"  [{processed}] {start[:10] if start != 'unknown' else 'no date'}{ws_label}  {subject[:80]}  -> {len(chunks)} chunks")

    return processed, total_chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json",   default=str(_DEFAULT_JSON))
    parser.add_argument("--chroma", default=_DEFAULT_CHROMA)
    args = parser.parse_args()

    json_path = Path(args.json)
    if not json_path.exists():
        print(f"ERROR: {json_path} not found")
        sys.exit(1)

    print(f"\nIngesting calendar meetings")
    print(f"  Source : {json_path}")
    print(f"  ChromaDB: {args.chroma}\n")

    before = doc_count(args.chroma)
    processed, chunks_added = ingest_meetings(json_path, args.chroma)
    after = doc_count(args.chroma)

    print(f"\nDone.")
    print(f"  Meetings processed : {processed}")
    print(f"  Chunks added       : {chunks_added}")
    print(f"  Total KB chunks    : {after}  (was {before})")


if __name__ == "__main__":
    main()
