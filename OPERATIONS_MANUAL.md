# Paramount RAG System — Operations Manual

**System:** Paramount Pictures Oracle Fusion 10/1 Go-Live Intelligence RAG  
**Maintained by:** Kevin Budz (OTC Track, Oracle Contractor)  
**Last updated:** 2026-07-05  
**Go-live date:** 2026-10-01

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Directory Map](#2-directory-map)
3. [Core Components](#3-core-components)
4. [Daily Operations — Email Sync & Ingest](#4-daily-operations--email-sync--ingest)
5. [Meeting Transcription & Ingest](#5-meeting-transcription--ingest)
6. [Document Ingest](#6-document-ingest)
7. [Dashboard — Doc Requests & Deliverables](#7-dashboard--doc-requests--deliverables)
8. [Audit & Health Checks](#8-audit--health-checks)
9. [Backup & Recovery](#9-backup--recovery)
10. [Flask Application — Routes Reference](#10-flask-application--routes-reference)
11. [Critical Rules — What Not To Do](#11-critical-rules--what-not-to-do)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. System Overview

The RAG (Retrieval-Augmented Generation) system ingests emails, documents,
meeting transcripts, and Asana exports into a vector database, then answers
natural-language questions about the Paramount Oracle Fusion project using
Claude Opus as the AI backbone.

**Technology stack:**
- Flask (Python) — web server, API, dashboard
- ChromaDB — vector store (SQLite-backed, ~484K+ chunks, 8.9GB)
- SentenceTransformers `all-MiniLM-L6-v2` — embedding model
- Claude Opus 4.8 (`claude-opus-4-8`) — answer generation
- SQLite `dashboard.db` — doc requests and deliverables tracking (separate from ChromaDB)

**Access:** `http://localhost:5000` (must be running on Kevin's local machine)

---

## 2. Directory Map

```
C:\GIT\
├── chroma_db\                    ← PRODUCTION vector database (never delete)
│   └── chroma.sqlite3            ← 8.9GB SQLite — all chunks + embeddings
├── dashboard.db                  ← Dashboard tracking DB (doc requests, deliverables)
├── rag-system\                   ← Flask application root
│   ├── app.py                    ← Main Flask app — all routes
│   ├── dashboard_db.py           ← Dashboard DB schema, CRUD, staleness logic
│   ├── sync_from_asana.py        ← Seeds dashboard.db from RICEFW CSV
│   ├── transcribe_backlog.py     ← Batch Zoom transcription + ingest
│   ├── transcribe_paramount_meetings.py  ← Alternative transcription script
│   ├── ingest_folder.py          ← Ingest a local folder of documents
│   ├── ingest_email.py           ← (Legacy) Graph API email ingest — DO NOT USE
│   ├── ingest_email_com.py       ← (Legacy) COM email ingest — use sync_outlook.py instead
│   ├── ingest_asana_raid.py      ← Ingest RAID CSV into ChromaDB
│   ├── ingest_asana_ricefw.py    ← Ingest RICEFW CSV into ChromaDB
│   ├── rag\
│   │   ├── store.py              ← ChromaDB wrapper (add_chunks, query, recent_items)
│   │   ├── chunker.py            ← Document chunking logic
│   │   ├── generator.py          ← Claude answer generation (model: claude-opus-4-8)
│   │   ├── email_json.py         ← Email JSON parser (HTML stripping, skip rules)
│   │   ├── schema.py             ← Metadata schema (WORKSTREAMS, DOC_TYPES, etc.)
│   │   └── graph\                ← Microsoft Graph API (email/calendar via OAuth)
│   │       ├── auth.py           ← OAuth2 token management
│   │       ├── mail.py           ← Graph mail fetcher
│   │       ├── calendar.py       ← Graph calendar fetcher
│   │       └── sharepoint.py     ← SharePoint document fetcher
│   ├── templates\
│   │   ├── dashboard.html        ← Phase 5 war-room dashboard
│   │   └── index.html            ← Main RAG chat UI
│   ├── static\
│   │   ├── dashboard.css         ← Dashboard styles
│   │   └── js\app.js             ← Chat UI JavaScript
│   └── uploads\                  ← Staging area for uploaded files (transient)
├── backup_rag.bat                ← Manual/nightly backup script to OneDrive
├── CHANGELOG.md                  ← Session-by-session change log
└── OPERATIONS_MANUAL.md          ← This file

C:\ParamountRAG\
├── scripts\
│   ├── sync_outlook.py           ← PRIMARY email + calendar sync (COM/Outlook)
│   ├── audit_chroma.py           ← Full KB audit report
│   ├── reconcile.py              ← Compare emails.json vs ChromaDB
│   ├── query_to_file.py          ← CLI: query RAG → write answer to file
│   ├── transcribe_mp4.py         ← Whisper MP4 → VTT transcription
│   ├── ingest_vtt.py             ← Ingest a VTT transcript into ChromaDB
│   ├── ingest_folder.py          ← Ingest a local folder (alt version)
│   ├── export_outlook_v7_com.py  ← Outlook COM exporter (called by sync_outlook.py)
│   ├── export_outlook_meetings_v1.py  ← Meeting export variant
│   └── check.py                  ← Quick health check
├── outlook\
│   ├── email\
│   │   ├── emails.json           ← Exported email archive (input to ingest)
│   │   ├── last_sync.txt         ← Incremental sync cursor (delete for full re-sync)
│   │   └── sharepoint_links.html ← SharePoint links extracted from emails
│   ├── calendar\
│   │   ├── meetings.json         ← Exported calendar/meeting archive
│   │   └── last_sync.txt         ← Calendar sync cursor
│   └── attachments\              ← Email attachment files
├── meetings\                     ← MP4 recordings + VTT transcripts
└── logs\
    ├── chroma_audit.txt          ← Output of audit_chroma.py
    ├── reconciliation_report.txt ← Output of reconcile.py
    └── transcribe_log.txt        ← Output of transcribe_mp4.py

C:\Paramount\
├── 10-1\
│   └── meetings\                 ← Alternate meeting recordings location
└── done\
    ├── Oracle_ERP_-_RAID.csv     ← Asana RAID export (re-export periodically)
    └── Oracle_ERP_-_RICEFW_Master.csv  ← Asana RICEFW export (re-export periodically)

C:\Users\kevin\OneDrive\Backups\
├── chroma_db\                    ← Nightly mirror of production ChromaDB
└── dashboard_db\                 ← Nightly mirror of dashboard.db
```

---

## 3. Core Components

### 3.1 Flask Application (`C:\GIT\rag-system\app.py`)

The central server. Must be running for anything to work.

**Start:**
```
cd C:\GIT\rag-system
python app.py
```

**Runs on:** `http://localhost:5000`  
**Debug mode:** ON (auto-reloads on file save — see Critical Rules)  
**ChromaDB path:** Hard-coded to `C:\GIT\chroma_db` via:
```python
CHROMA_DIR = str(_HERE.parent / "chroma_db")  # resolves to C:\GIT\chroma_db
```

### 3.2 ChromaDB (`C:\GIT\chroma_db\chroma.sqlite3`)

The production vector database. Contains all ingested content as searchable chunks.

- **Size:** ~8.9GB, ~484K+ chunks
- **Collection name:** `rag_documents`
- **Embedding model:** `all-MiniLM-L6-v2`
- **Chunk dedup:** MD5 hash of `source:chunk_index` — safe to re-ingest
- **NEVER** run scripts against this DB without `persist_dir=r"C:\GIT\chroma_db"`

### 3.3 Dashboard Database (`C:\GIT\dashboard.db`)

Separate SQLite DB for the dashboard tracking panels. Independent of ChromaDB.

**Tables:**
- `doc_requests` — FDD/TDD documentation requests blocking design sessions
- `deliverables` — O2C/AR team deliverables with status and blockers

**Safe to recreate:** `del C:\GIT\dashboard.db` then re-run `sync_from_asana.py`

### 3.4 Answer Generation (`C:\GIT\rag-system\rag\generator.py`)

Uses Claude Opus 4.8 (`claude-opus-4-8`) via Anthropic API. Requires
`ANTHROPIC_API_KEY` in `C:\GIT\rag-system\.env`.

---

## 4. Daily Operations — Email Sync & Ingest

### Easiest method — UI Scripts panel (recommended):

Open `http://localhost:5000` and use the **Scripts** panel in the sidebar:

| Button | What it does |
|--------|-------------|
| **Incremental Sync** | Pulls new Outlook emails since last sync, ingests them. Use daily. |
| **Full Sync** | Re-exports ALL Outlook email (ignores cursor), then ingests. Use when emails were missed. Takes several minutes. |
| **Ingest Todo Folder** | Ingests all files in `C:\Paramount\todo`, moves each to `C:\Paramount\done`. |

Live output streams into the log window below the buttons. Chunk count updates when done.

### Automatic schedule:
Two Windows Task Scheduler jobs run incremental sync automatically:
- **8:00 AM** daily — `ParamountRAG-Sync-8AM`
- **12:00 PM** daily — `ParamountRAG-Sync-Noon`
- **Flask must be running** at those times or ingest step will fail (Outlook export still completes — no emails lost, but they won't hit ChromaDB until next manual ingest)

### Command line (alternative):
```
cd C:\ParamountRAG\scripts

# Incremental (daily)
python sync_outlook.py --ingest

# Full historical re-sync
python sync_outlook.py --full --ingest
```

### Key files:
| File | Path | Purpose |
|------|------|---------|
| Email archive | `C:\ParamountRAG\outlook\email\emails.json` | Input to ingest |
| Sync cursor | `C:\ParamountRAG\outlook\email\last_sync.txt` | Tracks last sync time |
| Sync script | `C:\ParamountRAG\scripts\sync_outlook.py` | Outlook COM exporter |
| Email parser | `C:\GIT\rag-system\rag\email_json.py` | Parses JSON → chunks |

### CRITICAL:
- **NEVER use Graph API (`ingest_email.py`) for email** — always use the COM pipeline
- After ingest, check the skipped_empty count in the response — silent skips indicate a problem
- Skipped folders: Sync Issues, T&E, Asana, Training, Deleted Items, Drafts

---

## 5. Meeting Transcription & Ingest

### From MP4 recordings (Whisper transcription):

**Script:** `C:\ParamountRAG\scripts\transcribe_mp4.py`  
**Input:** MP4 files in `C:\ParamountRAG\meetings\`  
**Output:** VTT transcript alongside each MP4, then auto-ingested into ChromaDB  
**Model:** Whisper "small" (CPU FP32, ~25 min per 175MB file)

```
cd C:\ParamountRAG
python scripts\transcribe_mp4.py
```

Options:
```
python scripts\transcribe_mp4.py --dry-run          # preview only, no transcription
python scripts\transcribe_mp4.py --model medium     # more accurate, slower
```

### From Zoom recordings (batch backlog):

**Script:** `C:\GIT\rag-system\transcribe_backlog.py`  
**Input:** Zoom folder at `C:\Users\kevin\OneDrive\Documents\Zoom`  
**Skips:** Files already in ChromaDB (checks by source name)

```
cd C:\GIT\rag-system
python transcribe_backlog.py
```

### From existing VTT files:

**Script:** `C:\ParamountRAG\scripts\ingest_vtt.py`

```
cd C:\ParamountRAG
python scripts\ingest_vtt.py path\to\file.vtt
```

---

## 6. Document Ingest

### Todo folder workflow (recommended for ad-hoc files):

1. Drop files into `C:\Paramount\todo`
2. Click **Ingest Todo Folder** in the UI Scripts panel (or run `python ingest_todo.py`)
3. Files are ingested and automatically moved to `C:\Paramount\done`

Supported types: PDF, DOCX, XLSX, PPTX, TXT, MD, CSV, MSG, ZIP

**Script:** `C:\ParamountRAG\scripts\ingest_todo.py`

### Via the web UI drag-and-drop:
- Open `http://localhost:5000`
- Drag and drop files onto the Upload zone, or click "browse files"
- Files are ingested but NOT moved/tracked (use todo workflow if you want tracking)

### Via script (folder batch):

**Script:** `C:\GIT\rag-system\ingest_folder.py`

```
cd C:\GIT\rag-system
python ingest_folder.py "C:\path\to\folder"
```

### Via ZIP upload (large batches):
Use the ZIP upload endpoint in the UI — streams per-file progress, resumes on retry.

### Asana RAID/RICEFW exports:
Export from Asana as CSV → drop in `C:\Paramount\todo` → click **Ingest Todo Folder**.
The old method (dedicated ingest scripts) still works but the todo workflow is simpler.

**Note:** Asana CSVs go into ChromaDB (for RAG search). The dashboard
`sync_from_asana.py` is separate — it loads structured data into `dashboard.db`
for the tracking panels, not ChromaDB.

---

## 7. Dashboard — Doc Requests & Deliverables

Access: `http://localhost:5000/dashboard`

### Seeding data from Asana export:

**Step 1 — Export from Asana:**
- Open Oracle ERP - RICEFW Master project in Asana
- Export as CSV → save to `C:\Paramount\done\Oracle_ERP_-_RICEFW_Master.csv`

**Step 2 — Run sync:**
```
cd C:\GIT\rag-system
python sync_from_asana.py
```

- Filters to O2C (AR) workstream, Phase 4a, top-level tasks only
- Upserts on natural keys — safe to re-run; manual edits survive
- Skips Deferred and Out of Scope items
- 188 deliverables + 167 doc requests on initial seed (2026-07-03)

**Step 3 — Update date_requested:**
All doc_request rows default `date_requested` to the sync date. Update per item
via the dashboard edit modal (click any row in the Doc Requests panel).

### Dashboard panels:

| Panel | Data source | Filter |
|-------|-------------|--------|
| Deadlines & Overdue | Hardcoded milestones + RAG query | None |
| Open Items & Risks | RAG query | None |
| Workstream Status | RAG query | None |
| Recent Activity | ChromaDB metadata (48h) | None |
| Stakeholder Pulse | ChromaDB metadata | 8 named stakeholders |
| **Open Doc Requests** | `dashboard.db` → `doc_requests` | Phase dropdown |
| **Team Deliverables** | `dashboard.db` → `deliverables` | Phase dropdown |

### Editing rows on the dashboard:
- Click any row in the Doc Requests or Deliverables panel
- Edit modal opens with relevant fields
- Click Save → PATCH API call → panel reloads automatically

### Staleness logic (Doc Requests):
- **Open** = date_received is NULL
- **Received** = date_received set, reviewed = 0
- **Ready** = date_received set, reviewed = 1
- **Amber** = Open for 3–4 business days (Mon–Fri only)
- **Red** = Open for 5+ business days

### API endpoints for dashboard:
```
GET  /api/doc_requests?phase=Phase+4a+...    List doc requests (filtered)
POST /api/doc_requests                        Add new row
PATCH /api/doc_requests/<id>                  Update row
GET  /api/deliverables?phase=Phase+4a+...    List deliverables (filtered)
POST /api/deliverables                        Add new row
PATCH /api/deliverables/<id>                  Update row
GET  /api/phases                              List distinct phase values
```

---

## 8. Audit & Health Checks

### Full knowledge base audit:

**Script:** `C:\ParamountRAG\scripts\audit_chroma.py`  
**Output:** Console summary + `C:\ParamountRAG\logs\chroma_audit.txt`

```
cd C:\ParamountRAG
python scripts\audit_chroma.py
```

Reports: total chunks, unique sources, breakdown by doc type, date range,
author coverage, risk-flagged items.

### Email reconciliation (emails.json vs ChromaDB):

**Script:** `C:\ParamountRAG\scripts\reconcile.py`  
**Output:** Console summary + `C:\ParamountRAG\logs\reconciliation_report.txt`

```
cd C:\ParamountRAG
python scripts\reconcile.py
```

Reports: emails in JSON vs chunks in DB, missing emails by entry_id.

### Quick CLI query (RAG → file):

**Script:** `C:\ParamountRAG\scripts\query_to_file.py`

```
cd C:\ParamountRAG
python scripts\query_to_file.py "What is the status of INT631?" --out answers.txt
python scripts\query_to_file.py "Who is the backup for Prem James?" --heading "Prem OOO"
```

---

## 9. Backup & Recovery

### Nightly automated backup:

**Script:** `C:\GIT\backup_rag.bat`  
**Schedule:** Every night at 2:00 AM (Windows Task Scheduler — "RAG ChromaDB Backup")  
**Destination:**
- ChromaDB → `C:\Users\kevin\OneDrive\Backups\chroma_db\`
- dashboard.db → `C:\Users\kevin\OneDrive\Backups\dashboard_db\`

OneDrive then syncs to cloud automatically.

**Run manually anytime:**
```
C:\GIT\backup_rag.bat
```
Or via PowerShell:
```
robocopy "C:\GIT\chroma_db" "C:\Users\kevin\OneDrive\Backups\chroma_db" /MIR /Z
```

### Git recovery points:

List all recovery tags:
```
cd C:\GIT
git tag
```

Restore code to a recovery point (does NOT affect ChromaDB — code only):
```
cd C:\GIT
git checkout recovery/2026-07-03-dashboard-panels
```

Return to latest:
```
git checkout master
```

Current recovery tags:
| Tag | Date | What it captures |
|-----|------|-----------------|
| `recovery/2026-07-02-email-html-fix` | 2026-07-02 | After HTML email parsing fix |
| `recovery/2026-07-03-dashboard-panels` | 2026-07-03 | After dashboard panels added |

### If ChromaDB is lost:

ChromaDB is NOT in git (too large). Recovery requires:
1. Restore from `C:\Users\kevin\OneDrive\Backups\chroma_db\` (last nightly backup)
2. Re-run email ingest for any emails since the backup
3. Re-run document ingest for any docs since the backup

**This is why nightly backups are critical.**

---

## 10. Flask Application — Routes Reference

| Route | Method | Purpose |
|-------|--------|---------|
| `/` | GET | Main RAG chat UI |
| `/dashboard` | GET | Phase 5 war-room dashboard |
| `/api/query` | POST | SSE streaming RAG query + Claude answer |
| `/api/recent` | GET | Recent ingested items (last 48h) |
| `/api/stakeholder_pulse` | GET | Last contact date per stakeholder |
| `/api/sources` | GET | List all ingested sources + chunk count |
| `/api/sources/<name>` | DELETE | Remove a source from ChromaDB |
| `/api/upload` | POST | Upload + ingest document files |
| `/api/ingest/zip` | POST | Upload + ingest ZIP (streaming progress) |
| `/api/ingest/email-json` | POST | Ingest emails.json into ChromaDB |
| `/api/schema` | GET | Metadata schema (workstreams, doc types) |
| `/api/phases` | GET | Distinct phase values for dashboard filter |
| `/api/doc_requests` | GET/POST | List / add doc requests |
| `/api/doc_requests/<id>` | PATCH | Update doc request row |
| `/api/deliverables` | GET/POST | List / add deliverables |
| `/api/deliverables/<id>` | PATCH | Update deliverable row |
| `/auth/login` | GET | Microsoft OAuth2 login (Graph API) |
| `/auth/callback` | GET | OAuth2 callback |
| `/auth/logout` | POST | OAuth2 logout |
| `/auth/status` | GET | Check Graph API auth status |
| `/api/graph/mail` | POST | Ingest via Graph API (NOT for email — see §4) |
| `/api/graph/calendar` | POST | Ingest calendar via Graph API |
| `/api/graph/sharepoint` | POST | Ingest SharePoint documents |

---

## 11. Critical Rules — What Not To Do

### NEVER do these:

1. **Don't edit `app.py`, `store.py`, or `email_json.py` while Flask is running**  
   Debug auto-reload triggers HNSW index rebuild — UI shows 0 chunks. Not data loss
   but looks catastrophic. Stop Flask first, edit, restart.

2. **Don't run scripts without explicit `persist_dir=r"C:\GIT\chroma_db"`**  
   Scripts run from `C:\GIT\rag-system` default to `./chroma_db` which is
   `C:\GIT\rag-system\chroma_db` — a shadow DB the Flask app never reads.
   Always pass the path explicitly.

3. **Don't use Graph API for email**  
   `ingest_email.py` uses Microsoft Graph API. This is unreliable and not the
   production pipeline. Always use `sync_outlook.py` (COM/Outlook desktop).

4. **Don't delete `chroma_db\` without a confirmed backup**  
   This is irreplaceable without a full re-ingest (days of work).

5. **Don't use `collection.get()` for bulk metadata queries**  
   At 484K chunks it times out (>90s). Always query SQLite directly.

6. **Don't add new folders to skip without updating BOTH places:**  
   - `C:\GIT\rag-system\rag\email_json.py` → `_SKIP_FOLDERS`  
   - `C:\ParamountRAG\scripts\sync_outlook.py` → `_SKIP_FOLDER_FRAGMENTS`

---

## 12. Troubleshooting

### Dashboard shows 0 chunks / "DATABASE OFFLINE"
- Flask is not running → `cd C:\GIT\rag-system && python app.py`
- Flask just restarted (editing a file triggered debug reload) → wait 30–60s for HNSW rebuild

### Email query returns no results after sync
- Check if ingest actually ran (hit the Ingest button in UI, don't just sync)
- Check `skipped_empty` count in ingest response — may be silently skipping emails
- Verify the correct DB: scripts must use `persist_dir=r"C:\GIT\chroma_db"`

### sync_outlook.py only picks up 3–5 emails (not all)
- The cursor `last_sync.txt` exists → delete it for a full re-sync:
  `del C:\ParamountRAG\outlook\email\last_sync.txt`

### Doc requests / deliverables panel empty
- `dashboard.db` may not exist → run `python sync_from_asana.py`
- Check CSV exists: `C:\Paramount\done\Oracle_ERP_-_RICEFW_Master.csv`

### Staleness not calculating (all show 0bd)
- `date_requested` is set to the sync date (2026-07-03) for all seeded rows
- Update per item via edit modal with the real date you requested the document

### ChromaDB shadow DB confusion
- Symptom: health checks pass but RAG queries return wrong/stale results
- Cause: a script created `C:\GIT\rag-system\chroma_db` instead of using production
- Fix: always pass `persist_dir=r"C:\GIT\chroma_db"` explicitly in all scripts

---

*Manual maintained by Claude Code. Update CHANGELOG.md and this manual at the end of each session.*
