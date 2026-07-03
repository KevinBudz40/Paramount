"""
Ingest Outlook Calendar events via Microsoft Graph API.

Scope required: Calendars.Read (delegated)
Graph docs: https://learn.microsoft.com/graph/api/user-list-calendarview
"""
import re
from datetime import datetime, timedelta, timezone

from rag.graph.client import get_paginated

_STRIP_HTML = re.compile(r"<[^>]+>")
_COLLAPSE_WS = re.compile(r"\s+")

_SELECT_FIELDS = ",".join([
    "id", "subject", "body", "start", "end",
    "attendees", "organizer", "location",
    "importance", "categories", "seriesMasterId",
    "isOnlineMeeting", "onlineMeetingUrl",
])


def _clean_html(html: str) -> str:
    return _COLLAPSE_WS.sub(" ", _STRIP_HTML.sub(" ", html)).strip()


def fetch_events(
    days_back: int = 30,
    days_forward: int = 90,
    calendar_id: str = "primary",
) -> list[dict]:
    """
    Fetch calendar events within a rolling window and return normalized chunk records.

    Uses /me/calendarView rather than /me/events so recurring instances are
    expanded (each occurrence gets its own entry with the correct date).

    Args:
        days_back:    How many past days to include.
        days_forward: How many future days to include.
        calendar_id:  'primary' uses the default calendar; pass a calendar ID for others.
    """
    now = datetime.now(timezone.utc)
    start_dt = (now - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_dt   = (now + timedelta(days=days_forward)).strftime("%Y-%m-%dT%H:%M:%SZ")

    if calendar_id == "primary":
        path = "/me/calendarView"
    else:
        path = f"/me/calendars/{calendar_id}/calendarView"

    params = {
        "startDateTime": start_dt,
        "endDateTime":   end_dt,
        "$select":       _SELECT_FIELDS,
        "$top":          100,
        "$orderby":      "start/dateTime",
    }

    events = get_paginated(path, params=params)
    return [_normalize(e) for e in events]


def _normalize(evt: dict) -> dict:
    organizer_addr = evt.get("organizer", {}).get("emailAddress", {})
    organizer = organizer_addr.get("address", organizer_addr.get("name", ""))

    attendees = [
        a["emailAddress"]["address"]
        for a in evt.get("attendees", [])
        if a.get("emailAddress", {}).get("address")
    ]

    start_iso = evt.get("start", {}).get("dateTime", "")
    date = start_iso[:10]
    start_time = start_iso[11:16] if len(start_iso) > 10 else ""
    end_time = (evt.get("end", {}).get("dateTime", "") or "")[11:16]
    location = evt.get("location", {}).get("displayName", "")
    subject = evt.get("subject") or "(no title)"

    body_html = evt.get("body", {}).get("content", "")
    body_text = _clean_html(body_html)

    parts = [f"Meeting: {subject}", f"Date: {date}"]
    if start_time and end_time:
        parts.append(f"Time: {start_time}–{end_time} UTC")
    if location:
        parts.append(f"Location: {location}")
    if organizer:
        parts.append(f"Organizer: {organizer}")
    if attendees:
        parts.append(f"Attendees: {', '.join(attendees[:10])}")
    if body_text:
        parts.append(f"\n{body_text}")

    text = "\n".join(parts)
    source = f"calendar:{date}:{subject[:60]}"

    priority = "High" if evt.get("importance") == "high" else ""

    return {
        "text": text,
        "source": source,
        "doc_meta": {
            "doc_id":        evt.get("id", ""),
            "thread_id":     evt.get("seriesMasterId", ""),
            "doc_type":      "Meeting Notes",
            "source":        source,
            "author":        organizer,
            "recipients":    attendees,
            "date":          date,
            "meeting_date":  date,
            "topic":         subject,
            "priority":      priority,
            "keywords":      evt.get("categories", []),
        },
    }
