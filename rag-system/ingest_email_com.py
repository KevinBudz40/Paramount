"""
ingest_email_com.py — Ingest Outlook emails into ChromaDB via win32com (local Outlook).

No Graph API / OAuth needed — reads directly from your signed-in Outlook desktop app.

Usage:
    python ingest_email_com.py                         # inbox, last 500
    python ingest_email_com.py --folder "Sent Items"   # sent items
    python ingest_email_com.py --top 1000              # fetch more
    python ingest_email_com.py --since 2026-06-01      # date filter
    python ingest_email_com.py --folder inbox --folder "Sent Items"
"""
import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from rag.chunker import _chunk_text
from rag.store import add_chunks, checkpoint, doc_count

_DEFAULT_CHROMA = str(_SCRIPT_DIR / "chroma_db")
_BATCH = 200

FOLDER_ALIASES = {
    "inbox":      "Inbox",
    "sentitems":  "Sent Items",
    "sent":       "Sent Items",
    "deleted":    "Deleted Items",
    "drafts":     "Drafts",
}


def get_outlook():
    import win32com.client
    try:
        outlook = win32com.client.GetActiveObject("Outlook.Application")
        print("  Connected to running Outlook instance")
    except Exception:
        outlook = win32com.client.Dispatch("Outlook.Application")
        print("  Launched new Outlook instance")
    return outlook


def find_folder(namespace, folder_name: str):
    """Walk the store's top-level folders looking for a case-insensitive match."""
    target = folder_name.lower()
    for store in namespace.Stores:
        root = store.GetRootFolder()
        for folder in root.Folders:
            if folder.Name.lower() == target:
                return folder
    # Try default inbox as fallback
    return namespace.GetDefaultFolder(6)  # 6 = olFolderInbox


def fetch_from_outlook(folder_name: str, top: int, since_date: datetime | None) -> list[dict]:
    namespace = get_outlook().GetNamespace("MAPI")

    resolved_name = FOLDER_ALIASES.get(folder_name.lower(), folder_name)
    folder = find_folder(namespace, resolved_name)
    if folder is None:
        print(f"  WARNING: folder '{resolved_name}' not found, using Inbox")
        folder = namespace.GetDefaultFolder(6)

    print(f"  Reading from folder: {folder.Name}  ({folder.Items.Count} total items)")

    items = folder.Items
    items.Sort("[ReceivedTime]", True)  # newest first

    messages = []
    skipped = 0
    count = 0

    for item in items:
        if count >= top:
            break

        try:
            msg_class = getattr(item, "MessageClass", "")
            if not str(msg_class).startswith("IPM.Note"):
                skipped += 1
                continue

            received = getattr(item, "ReceivedTime", None)
            if received is None:
                received = getattr(item, "SentOn", None)

            if since_date and received:
                # win32com returns a datetime-like pywintypes.datetime
                received_dt = datetime(received.year, received.month, received.day,
                                       tzinfo=timezone.utc)
                if received_dt < since_date:
                    # Items are sorted newest-first; once we're past the date we can stop
                    break

            subject   = str(getattr(item, "Subject", "") or "")
            sender    = str(getattr(item, "SenderName", "") or "")
            sender_em = str(getattr(item, "SenderEmailAddress", "") or "")
            to        = str(getattr(item, "To", "") or "")
            cc        = str(getattr(item, "CC", "") or "")
            body      = str(getattr(item, "Body", "") or "")

            # Build readable text block
            date_str = f"{received.year}-{received.month:02d}-{received.day:02d}" if received else "unknown"
            header = (
                f"Email Date: {date_str}\n"
                f"From: {sender} <{sender_em}>\n"
                f"To: {to}\n"
                f"CC: {cc}\n"
                f"Subject: {subject}\n"
                f"---\n"
            )
            full_text = header + body.strip()

            source = f"email:{date_str}:{re.sub(r'[^a-zA-Z0-9_-]', '_', subject[:60])}"

            messages.append({
                "text": full_text,
                "source": source,
                "doc_meta": {
                    "date": date_str,
                    "topic": subject,
                    "sender": sender,
                    "folder": folder.Name,
                },
            })
            count += 1

        except Exception as e:
            skipped += 1
            if skipped <= 5:
                print(f"  [skip] {e}")

    print(f"  Fetched {len(messages)} emails  (skipped {skipped} non-mail items)")
    return messages


def ingest_folder(folder_name: str, top: int, since_date: datetime | None, chroma_dir: str):
    messages = fetch_from_outlook(folder_name, top, since_date)

    total_chunks = 0
    processed = 0

    for i, msg in enumerate(messages, 1):
        text = msg["text"].strip()
        if not text or len(text) < 30:
            continue

        source = msg["source"]
        doc_meta = msg["doc_meta"]

        raw_chunks = _chunk_text(text)
        chunks = [
            {"text": t, "source": source, "chunk_index": idx}
            for idx, t in enumerate(raw_chunks)
        ]
        if not chunks:
            continue

        for batch_start in range(0, len(chunks), _BATCH):
            add_chunks(chunks[batch_start:batch_start + _BATCH], doc_meta=doc_meta, persist_dir=chroma_dir)

        total_chunks += len(chunks)
        processed += 1
        subj = doc_meta.get("topic", "")[:65]
        date = doc_meta.get("date", "")
        print(f"  [{processed}] {date}  {subj}  -> {len(chunks)} chunk(s)")

    checkpoint(chroma_dir)
    return processed, total_chunks


def main():
    parser = argparse.ArgumentParser(description="Ingest local Outlook emails into ChromaDB via COM")
    parser.add_argument("--folder", action="append", default=[], metavar="FOLDER",
                        help="Folder name: inbox, 'Sent Items', etc. Repeatable. Default: inbox")
    parser.add_argument("--top",   type=int, default=500,
                        help="Max emails per folder (default 500)")
    parser.add_argument("--since", default=None,
                        help="Only emails on/after this date, e.g. 2026-06-01")
    parser.add_argument("--chroma", default=_DEFAULT_CHROMA,
                        help="ChromaDB directory (default: ./chroma_db)")
    args = parser.parse_args()

    since_date = None
    if args.since:
        since_date = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)

    folders = args.folder or ["inbox"]

    print(f"\nIngesting Outlook emails via COM (local Outlook)")
    print(f"  Folders : {', '.join(folders)}")
    print(f"  Since   : {args.since or 'all time'}")
    print(f"  Max/fldr: {args.top}")
    print(f"  ChromaDB: {args.chroma}\n")

    before = doc_count(args.chroma)
    grand_processed = 0
    grand_chunks = 0

    for folder in folders:
        print(f"\n--- Folder: {folder} ---")
        processed, chunks = ingest_folder(folder, args.top, since_date, args.chroma)
        grand_processed += processed
        grand_chunks += chunks

    after = doc_count(args.chroma)
    print(f"\nDone.")
    print(f"  Emails processed : {grand_processed}")
    print(f"  Chunks added     : {grand_chunks}")
    print(f"  Total DB chunks  : {after}  (was {before})")


if __name__ == "__main__":
    main()
