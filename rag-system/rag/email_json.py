"""
JSON email archive parser for the Paramount RAG pipeline.

Reads the emails.json produced by the Outlook COM exporter and converts
each message into chunk records ready for rag.store.add_chunks().

Field mapping:
  entry_id         → doc_id
  conversation_id  → thread_id
  sender_name      → author  (used when sender_email is an Exchange DN /o=…)
  sender_email     → author  (used when it is a real SMTP address)
  to + cc          → recipients (list of display names / addresses)
  received_time    → date (ISO date portion only)
  subject          → topic
  plain_body / body → text for chunking (either field accepted)
  importance       → priority ("High" | "")
  flag_status      → risk_flag (True when value == "flagged")
  has_attachments  → added to keywords as "has_attachments" when True
  folder_path      → last path segment used in source label
  linked_documents → names extracted into keywords list
"""
import json
import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from rag.chunker import _chunk_text


class _HTMLStripper(HTMLParser):
    _SKIP_TAGS = {"style", "script", "head"}

    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self):
        return re.sub(r'\n{3,}', '\n\n', "\n".join(self._parts)).strip()


def _strip_html(html: str) -> str:
    s = _HTMLStripper()
    s.feed(html or "")
    return s.get_text()

# ── Skip rules (applied before any processing) ────────────────────────────

_SKIP_SENDERS = {"postmaster@oracle.com"}

_SKIP_FOLDERS = {"Sync Issues", "T&E", "Asana", "Training", "Deleted Items", "Drafts"}

# ── Body cleaning ─────────────────────────────────────────────────────────

# "Oracle Confidential" banners appear at the top and bottom of many emails
_ORACLE_BANNER = re.compile(
    r"^\s*oracle\s+confidential\s*$", re.IGNORECASE | re.MULTILINE
)
# Zero-width Unicode characters injected by OWA link rendering
_ZERO_WIDTH = re.compile(r"[​‌‍﻿]")
# Collapse 3+ blank lines to 2
_MULTI_BLANK = re.compile(r"\n{3,}")


def _clean_body(text: str) -> str:
    text = _ZERO_WIDTH.sub("", text)
    text = _ORACLE_BANNER.sub("", text)
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip()


# ── Field helpers ─────────────────────────────────────────────────────────

def _is_exchange_dn(addr: str) -> bool:
    """True when addr is an internal Exchange Distinguished Name, not an SMTP address."""
    return (addr or "").lstrip().lower().startswith("/o=")


def _resolve_author(msg: dict) -> str:
    # new format: from = {"name": ..., "email": ...}
    from_field = msg.get("from")
    if isinstance(from_field, dict):
        email = from_field.get("email") or ""
        name  = from_field.get("name") or ""
    else:
        email = msg.get("sender_email") or ""
        name  = msg.get("sender_name") or ""
    # Always prefer display name when available; fall back to email only if no name
    if name:
        return name
    return "" if _is_exchange_dn(email) else email


def _parse_recipients(msg: dict) -> list[str]:
    parts: list[str] = []
    for field in ("to", "cc"):
        val = msg.get(field) or []
        if isinstance(val, list):
            # new format: [{"name": ..., "email": ..., "type": ...}, ...]
            for entry in val:
                if isinstance(entry, dict):
                    label = entry.get("name") or entry.get("email") or ""
                else:
                    label = str(entry)
                if label.strip():
                    parts.append(label.strip())
        elif isinstance(val, str):
            # old format: comma-separated string
            parts.extend(p.strip() for p in val.split(",") if p.strip())
    return parts


def _priority(msg: dict) -> str:
    imp = msg.get("importance")
    return "High" if imp == "high" or imp == 2 else ""


def _folder_leaf(folder_path: str) -> str:
    """Last segment of the folder path, e.g. 'Inbox' from 'account/.../Inbox'."""
    return (folder_path or "").rstrip("/").split("/")[-1]


def _keywords(msg: dict) -> list[str]:
    kws: list[str] = ["Paramount"]  # project tag
    # Linked document filenames
    for doc in msg.get("linked_documents") or []:
        name = doc.get("name", "").strip()
        if name:
            kws.append(name)
    # Attachment flag
    if msg.get("has_attachments"):
        kws.append("has_attachments")
    return kws


# ── Result types ──────────────────────────────────────────────────────────

@dataclass
class ParseResult:
    processed: int = 0
    skipped_folder: int = 0
    skipped_sender: int = 0
    skipped_empty: int = 0
    total_chunks: int = 0
    skip_detail: list[str] = field(default_factory=list)


# ── Main parser ───────────────────────────────────────────────────────────

def parse_email_json(path: str | Path) -> tuple[list[dict], ParseResult]:
    """
    Parse an emails.json archive file.

    Returns:
        (chunks, result) where chunks is a list of dicts each containing
        'text', 'source', 'chunk_index', and 'doc_meta' — ready for
        rag.store.add_chunks().
    """
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    messages: list[dict] = raw.get("emails") or raw.get("messages") or []

    result = ParseResult()
    all_chunks: list[dict] = []

    for msg in messages:
        folder = _folder_leaf(msg.get("folder_path", ""))
        from_field = msg.get("from")
        if isinstance(from_field, dict):
            sender_email = (from_field.get("email") or "").lower().strip()
        else:
            sender_email = (msg.get("sender_email") or "").lower().strip()
        subject = (msg.get("subject") or "").strip()
        entry_id = msg.get("entry_id", "")
        date = (msg.get("received_time") or "")[:10]

        # ── Skip rules ──────────────────────────────────────────────────
        if folder in _SKIP_FOLDERS:
            result.skipped_folder += 1
            result.skip_detail.append(f"folder={folder} | {subject[:50]}")
            continue

        if sender_email in _SKIP_SENDERS:
            result.skipped_sender += 1
            result.skip_detail.append(f"sender={sender_email} | {subject[:50]}")
            continue

        raw_body = msg.get("plain_body") or msg.get("body") or ""
        if not raw_body.strip() and msg.get("html_body"):
            raw_body = _strip_html(msg["html_body"])
        body = _clean_body(raw_body)
        # _clean_body may strip everything (e.g. HTML-only emails that reduce to
        # Oracle Confidential banners). Fall back to the raw stripped text so we
        # don't silently discard emails with real content.
        if not body and raw_body.strip():
            body = raw_body.strip()
        if not body:
            result.skipped_empty += 1
            result.skip_detail.append(f"empty body | {subject[:50]}")
            continue

        # ── Build chunk text ────────────────────────────────────────────
        author = _resolve_author(msg)
        from_field = msg.get("from")
        sender_name = (from_field.get("name") if isinstance(from_field, dict) else msg.get("sender_name")) or ""
        text_parts = [f"Subject: {subject}", f"From: {author}", f"Sender: {sender_name}", f"Date: {date}",
                      f"Folder: {folder}", "", body]
        full_text = "\n".join(text_parts)

        source = f"email:{date}:{subject[:55]}"

        # ── Paramount metadata ──────────────────────────────────────────
        from_field = msg.get("from")
        raw_email = (from_field.get("email") if isinstance(from_field, dict) else msg.get("sender_email")) or ""
        smtp_email = "" if _is_exchange_dn(raw_email) else raw_email.lower().strip()

        doc_meta = {
            "doc_id":          entry_id,
            "thread_id":       msg.get("conversation_id", ""),
            "doc_type":        "Email",
            "source":          source,
            "author":          author,
            "sender_email":    smtp_email,
            "folder":          folder,
            "recipients":      _parse_recipients(msg),
            "date":            date,
            "topic":           subject,
            "priority":        _priority(msg),
            "risk_flag":       msg.get("flag_status") == "flagged",
            "keywords":        _keywords(msg),
            "workstream":      "",   # caller can override per-batch
        }

        # ── Chunk and collect ───────────────────────────────────────────
        chunk_texts = _chunk_text(full_text)
        for i, chunk_text in enumerate(chunk_texts):
            all_chunks.append({
                "text":        chunk_text,
                "source":      source,
                "chunk_index": i,
                "doc_meta":    doc_meta,
            })

        result.processed += 1
        result.total_chunks += len(chunk_texts)

    return all_chunks, result
