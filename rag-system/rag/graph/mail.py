"""
Ingest Exchange Online emails via Microsoft Graph API.

Scope required: Mail.Read (delegated)
Graph docs: https://learn.microsoft.com/graph/api/user-list-messages
"""
import re
from rag.graph.client import get_paginated


_STRIP_HTML = re.compile(r"<[^>]+>")
_COLLAPSE_WS = re.compile(r"\s+")

_SELECT_FIELDS = ",".join([
    "id", "subject", "bodyPreview", "body",
    "from", "toRecipients", "ccRecipients",
    "receivedDateTime", "conversationId",
    "importance", "hasAttachments", "categories",
])


def _clean_html(html: str) -> str:
    text = _STRIP_HTML.sub(" ", html)
    return _COLLAPSE_WS.sub(" ", text).strip()


def fetch_messages(
    folder: str = "inbox",
    top: int = 50,
    filter_query: str | None = None,
) -> list[dict]:
    """
    Fetch messages from an Exchange folder and return normalized chunk records.

    Args:
        folder:       Well-known folder name ('inbox', 'sentitems', 'drafts', etc.)
                      or a folder ID.
        top:          Max messages per ingestion run (Graph hard-limits pages to 999).
        filter_query: Optional OData $filter, e.g. "receivedDateTime ge 2025-01-01T00:00:00Z"
    """
    params: dict = {
        "$select": _SELECT_FIELDS,
        "$orderby": "receivedDateTime desc",
        "$top": min(top, 999),
    }
    if filter_query:
        params["$filter"] = filter_query

    messages = get_paginated(f"/me/mailFolders/{folder}/messages", params=params)
    return [_normalize(m) for m in messages[:top]]


def _normalize(msg: dict) -> dict:
    from_addr = msg.get("from", {}).get("emailAddress", {})
    author = from_addr.get("address", from_addr.get("name", ""))

    to_recipients = [
        r["emailAddress"]["address"]
        for r in msg.get("toRecipients", [])
        if r.get("emailAddress")
    ]
    cc_recipients = [
        r["emailAddress"]["address"]
        for r in msg.get("ccRecipients", [])
        if r.get("emailAddress")
    ]
    recipients = to_recipients + cc_recipients

    body_html = msg.get("body", {}).get("content", msg.get("bodyPreview", ""))
    body_text = _clean_html(body_html)
    subject = msg.get("subject") or "(no subject)"
    date = (msg.get("receivedDateTime") or "")[:10]

    text = f"Subject: {subject}\nFrom: {author}\nDate: {date}\n\n{body_text}"
    source = f"email:{date}:{subject[:60]}"

    priority = ""
    importance = msg.get("importance", "")
    if importance == "high":
        priority = "High"
    elif importance == "low":
        priority = "Low"

    return {
        "text": text,
        "source": source,
        "doc_meta": {
            "doc_id":    msg.get("id", ""),
            "thread_id": msg.get("conversationId", ""),
            "doc_type":  "Email",
            "source":    source,
            "author":    author,
            "recipients": recipients,
            "date":      date,
            "priority":  priority,
            "topic":     subject,
            "keywords":  msg.get("categories", []),
        },
    }
