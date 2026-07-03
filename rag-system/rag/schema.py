"""Paramount metadata schema for ChromaDB documents."""
import json
from datetime import datetime, timezone
from typing import Any

# ChromaDB can't store lists — these are JSON-serialized to strings
LIST_FIELDS = {"recipients", "mentioned_people", "action_items", "keywords"}

# Supports ChromaDB $eq / $in where filters
FILTERABLE_FIELDS = {
    "workstream", "doc_type", "priority", "risk_flag",
    "is_resolved", "author", "sentiment", "topic", "source",
}

WORKSTREAMS = ["O2C", "AR", "AGIS", "Billing", "Integrations"]
DOC_TYPES = ["Email", "Meeting Notes", "Action Item", "Decision", "Report", "Contract", "Ticket", "Other"]
PRIORITIES = ["Low", "Medium", "High", "Critical"]
SENTIMENTS = ["Positive", "Neutral", "Negative", "Mixed"]

# Canonical field order and defaults
FIELD_DEFAULTS: dict[str, Any] = {
    # Identity
    "doc_id":          "",
    "thread_id":       "",
    "parent_doc_id":   "",
    # Dates
    "date":            "",
    "ingested_at":     "",   # auto-set on ingest
    "meeting_date":    "",
    "due_date":        "",
    # People
    "author":          "",
    "sender_email":    "",
    "folder":          "",
    "recipients":      [],
    "mentioned_people": [],
    # Classification
    "workstream":      "",
    "milestone":       "",
    "doc_type":        "",
    "topic":           "",
    "source":          "",
    # Content
    "action_items":    [],
    "decision":        "",
    "keywords":        [],
    # Flags
    "risk_flag":       False,
    "sentiment":       "",
    "priority":        "",
    "is_resolved":     False,
}


def serialize_metadata(meta: dict) -> dict:
    """Flatten Python metadata to ChromaDB-safe str/int/float/bool values."""
    out: dict[str, Any] = {}
    now = datetime.now(timezone.utc).isoformat()

    for field, default in FIELD_DEFAULTS.items():
        val = meta.get(field, default)
        if val is None:
            val = default

        if field in LIST_FIELDS:
            out[field] = json.dumps(val) if isinstance(val, list) else (val if isinstance(val, str) else "[]")
        elif isinstance(default, bool):
            # Must check bool before int — bool is a subclass of int
            out[field] = bool(val)
        elif isinstance(default, (int, float)):
            out[field] = val
        else:
            out[field] = str(val)

    if not out.get("ingested_at"):
        out["ingested_at"] = now

    return out


def deserialize_metadata(meta: dict) -> dict:
    """Restore list fields from JSON strings."""
    out = dict(meta)
    for field in LIST_FIELDS:
        raw = out.get(field, "[]")
        if isinstance(raw, str):
            try:
                out[field] = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                out[field] = []
    return out


def build_where_clause(filters: dict) -> dict | None:
    """Build a ChromaDB where clause from a flat filter dict."""
    conditions = []
    for k, v in filters.items():
        if k not in FILTERABLE_FIELDS or v is None or v == "" or v == []:
            continue
        if isinstance(v, bool):
            conditions.append({k: {"$eq": v}})
        elif isinstance(v, list):
            conditions.append({k: {"$in": [str(i) for i in v]}})
        else:
            conditions.append({k: {"$eq": str(v)}})

    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}
