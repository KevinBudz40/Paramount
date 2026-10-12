# Paramount RAG System — Operations Manual

**System:** Paramount Pictures Oracle Fusion Phase 4a Go-Live Intelligence RAG  
**Maintained by:** Kevin Budz (OTC Track, Oracle Contractor)  
**Last updated:** 2026-08-23  
**Go-live date:** 2027-01-01 (Phase 4a — moved from 2026-10-01)

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Directory Map](#2-directory-map)
3. [Core Components](#3-core-components)
4. [Daily Operations — Email Sync & Ingest](#4-daily-operations--email-sync--ingest)
5. [Document Ingest — Primary](#5-document-ingest--primary)
6. [Meeting Transcription & Ingest](#6-meeting-transcription--ingest)
7. [Labeling Chunks](#7-labeling-chunks)
8. [Agentic RAG (Streamlit UI)](#8-agentic-rag-streamlit-ui)
9. [Dashboard — Doc Requests & Deliverables](#9-dashboard--doc-requests--deliverables)
10. [Audit & Health Checks](#10-audit--health-checks)
11. [Backup & Recovery](#11-backup--recovery)
12. [Flask Application — Routes Reference](#12-flask-application--routes-reference)
13. [Critical Rules — What Not To Do](#13-critical-rules--what-not-to-do)
14. [Troubleshooting](#14-troubleshooting)
15. [Kevin's Task Tracker — Kevin_Todo.xlsx](#15-kevins-task-tracker--kevin_todoxlsx)
16. [Revert to ChromaDB](#16-revert-to-chromadb)

---

## 1. System Overview

The RAG (Retrieval-Augmented Generation) system ingests emails, documents,
meeting transcripts, and Asana exports into a vector database, then answers
natural-language questions about the Paramount Oracle Fusion project using
Claude Opus as the AI backbone.

**Technology stack:**
- **Qdrant** (embedded mode) — vector store (migrated from ChromaDB 2026-08-22)
- SentenceTransformers `all-MiniLM-L6-v2` — embedding model (384 dimensions)
- Claude Opus 4.8 (`claude-opus-4-8`) — answer generation
- Streamlit — Agentic RAG UI (`localhost:8501`)
- Flask (Python) — dashboard and `/open` file links only (`localhost:5000`)
- SQLite `dashboard.db` — doc requests and deliverables tracking (separate from Qdrant)

**Why Qdrant:** ChromaDB's HNSW compaction crashed on large batch upserts, requiring
multiple full rebuilds. Qdrant embedded mode has no such compaction process.

**Qdrant embedded lock:** Only ONE process can open `C:\GIT\qdrant_db` at a time.
Streamlit (ARAG) and Flask cannot run simultaneously. Ingest scripts must not run
while either is open. Stop whichever is running before ingesting or labeling.

---

## 2. Directory Map

```
C:\GIT\
├── qdrant_db\                    ← PRODUCTION vector database (never delete)
│   └── source_log.db             ← SQLite tracking ingested files, skip list
├── chroma_db\                    ← PRESERVED revert path (old ChromaDB — do NOT delete)
├── dashboard.db                  ← Dashboard tracking DB (doc requests, deliverables)
├── rag-system-qdrant\            ← Flask application root (ACTIVE)
│   ├── app.py                    ← Main Flask app — dashboard + /open route
│   ├── dashboard_db.py           ← Dashboard DB schema, CRUD, staleness logic
│   ├── sync_from_asana.py        ← Seeds dashboard.db from RICEFW CSV
│   ├── transcribe_backlog.py     ← Batch Zoom transcription + ingest
│   ├── transcribe_paramount_meetings.py  ← Alternative transcription script
│   ├── ingest_folder.py          ← Ingest a local folder of documents
│   ├── rag\
│   │   ├── store.py              ← Qdrant wrapper (add_chunks, query, close_client)
│   │   ├── chunker.py            ← Document chunking logic
│   │   ├── generator.py          ← Claude answer generation (model: claude-opus-4-8)
│   │   ├── email_json.py         ← Email JSON parser (HTML stripping, skip rules)
│   │   └── schema.py             ← Metadata schema (WORKSTREAMS, DOC_TYPES, etc.)
│   └── templates\ / static\      ← UI assets
├── rag-system\                   ← PRESERVED revert path (old ChromaDB Flask app)
├── ingest_phase4a_4b_local.py    ← PRIMARY document ingest (direct to Qdrant, no Flask)
├── ingest_emails_batched.py      ← PRIMARY email ingest (direct to Qdrant, no Flask)
├── ingest_calendar_batched.py    ← PRIMARY calendar ingest (direct to Qdrant, no Flask)
├── prep_qdrant_db.py             ← One-time: create qdrant_db with skip_list from chroma_db
├── scan_todos.py                 ← Kevin_Todo.xlsx dedup + append layer
├── add_todo.py                   ← Manually append a single task to Kevin_Todo.xlsx
├── todo_cursor.json              ← Last-scan timestamps per source
├── todo_scan_output.json         ← Intermediate scan candidates
├── CHANGELOG.md                  ← Session-by-session change log
└── OPERATIONS_MANUAL.md          ← This file

C:\ParamountRAG\
├── scripts\
│   ├── sync_outlook.py           ← PRIMARY email + calendar sync (COM/Outlook)
│   ├── agent.py                  ← Agentic RAG orchestrator (Qdrant, Claude tool-use loop)
│   ├── app_agent.py              ← Streamlit UI for agentic RAG
│   ├── agent_query_to_file.py    ← CLI: run agent → append answer to daily .md file
│   ├── ingest_batched.py         ← Legacy batch ingest (superseded by ingest_phase4a_4b_local.py)
│   ├── sync_tactiq.py            ← Tactiq meeting summary sync (subprocess broken — use MCP instead)
│   ├── ingest_tactiq.py          ← ingest_meeting() helper for Tactiq
│   ├── label_chunks.py           ← Backfill doc_type labels (Qdrant scroll+set_payload)
│   ├── source_log.py             ← source_log.db helper (DB: C:\GIT\qdrant_db\source_log.db)
│   ├── audit_chroma.py           ← Full KB audit report
│   ├── reconcile.py              ← Compare emails.json vs vector store
│   ├── query_to_file.py          ← CLI: query RAG → write answer to file
│   ├── transcribe_mp4.py         ← Whisper MP4 → VTT transcription
│   ├── ingest_vtt.py             ← Ingest a VTT transcript
│   └── check.py                  ← Quick health check
├── outlook\
│   ├── email\
│   │   ├── emails.json           ← Exported email archive (input to ingest)
│   │   └── last_sync.txt         ← Incremental sync cursor
│   └── calendar\
│       └── meetings.json         ← Exported calendar/meeting archive
└── meetings\                     ← MP4 recordings + VTT transcripts

C:\Paramount\
├── local_docs\                   ← PRIMARY drop folder for ad-hoc files
│   ├── Oracle_ERP_-_RAID.csv     ← Asana RAID export (re-export periodically)
│   ├── Oracle_ERP_-_RICEFW_Master.csv  ← Asana RICEFW export
│   ├── Project_Roster.docx       ← Program participant roster
│   └── Kevin_Todo.xlsx           ← Kevin's personal task tracker (see §15)
└── ARAG_DOCS\                    ← Agentic RAG output — daily briefing .md files
```

---

## 3. Core Components

### 3.1 Qdrant Vector Store (`C:\GIT\qdrant_db\`)

The production vector database. **~36,800 chunks** as of 2026-08-23.

- **Collection name:** `rag_documents`
- **Embedding model:** `all-MiniLM-L6-v2` (384 dimensions)
- **Source log:** `C:\GIT\qdrant_db\source_log.db` — SQLite tracking full paths, MD5 hashes, chunk counts. Powers dedup and change detection.
- **Embedded lock:** Only one process at a time. Stop Flask OR Streamlit before ingest.
- **NEVER** delete `qdrant_db\` without a confirmed backup on Z:\

### 3.2 Qdrant store wrapper (`C:\GIT\rag-system-qdrant\rag\store.py`)

Key functions:
| Function | Purpose |
|----------|---------|
| `add_chunks(chunks, doc_meta, persist_dir)` | Embed + upsert chunks into Qdrant |
| `query(query, n_results, persist_dir)` | Semantic vector search |
| `keyword_search(term, max_results, persist_dir)` | Full-text scan via scroll |
| `close_client(persist_dir)` | Explicitly release Qdrant file lock |
| `delete_source(source_name, persist_dir)` | Remove chunks by source key |

**`close_client()` must be called before any subprocess that opens Qdrant** (e.g. label_chunks). The `_clients` dict caches the client for the process lifetime — without explicit close, the lock is never released.

### 3.3 Flask Application (`C:\GIT\rag-system-qdrant\app.py`)

**Flask is NOT needed for ingest or agentic RAG queries.** It serves:
- `/dashboard` — doc requests & deliverables tracking UI
- `/open?path=...` — open a local file in Windows Explorer

**Start:**
```
cd C:\GIT\rag-system-qdrant
python app.py
```
**Runs on:** `http://localhost:5000`

> Flask and Streamlit (ARAG) cannot run at the same time — both hold the Qdrant lock.

### 3.4 Agentic RAG (`C:\ParamountRAG\scripts\agent.py`)

Claude Opus 4.8 iterative search agent over Qdrant. Read-only — safe to run without Flask.
Scripts: `agent.py` (orchestrator) + `app_agent.py` (Streamlit UI) + `agent_query_to_file.py` (CLI).

### 3.5 Dashboard Database (`C:\GIT\dashboard.db`)

Separate SQLite DB for dashboard tracking panels. Independent of Qdrant.
**Safe to recreate:** `del C:\GIT\dashboard.db` then `python sync_from_asana.py`

### 3.6 Answer Generation (`C:\GIT\rag-system-qdrant\rag\generator.py`)

Uses Claude Opus 4.8 (`claude-opus-4-8`) via Anthropic API. Requires
`ANTHROPIC_API_KEY` in `C:\GIT\rag-system-qdrant\.env` or `C:\GIT\.env`.

---

## 4. Daily Operations — Email Sync & Ingest

**No Flask needed.** Stop Flask or Streamlit first (Qdrant lock). Two-step process:

```powershell
# Step 1 — export from Outlook (incremental):
python C:\ParamountRAG\scripts\sync_outlook.py --mail

# Step 2 — ingest into Qdrant (direct, no Flask):
python C:\GIT\ingest_emails_batched.py
```

**IMPORTANT:**
- Use `--mail` (incremental). **NEVER `--full`** for routine syncs — re-exports all 600+ emails.
- `ingest_emails_batched.py` writes directly to Qdrant. Stop Streamlit/Flask first.
- After ingest, check the `skipped_empty` count in output — high skips indicate a problem.

### Key files:
| File | Path | Purpose |
|------|------|---------|
| Email archive | `C:\ParamountRAG\outlook\email\emails.json` | Input to ingest |
| Sync cursor | `C:\ParamountRAG\outlook\email\last_sync.txt` | Tracks last sync time |
| Sync script | `C:\ParamountRAG\scripts\sync_outlook.py` | Outlook COM exporter |
| Email ingest | `C:\GIT\ingest_emails_batched.py` | Direct to Qdrant |

### Targeted re-sync by date window:
```
python C:\ParamountRAG\scripts\sync_outlook.py --mail --since 2026-07-20T00:00:00
python C:\GIT\ingest_emails_batched.py
```

### CRITICAL:
- **NEVER use Graph API** for email — always use the COM pipeline (`sync_outlook.py`)
- Skipped folders: Sync Issues, T&E, Asana, Training, Deleted Items, Drafts

---

## 5. Document Ingest — Primary

**Script:** `C:\GIT\ingest_phase4a_4b_local.py`  
**Flask required:** NO — writes directly to Qdrant  
**Stop Streamlit/Flask first** (Qdrant embedded lock)

```
python C:\GIT\ingest_phase4a_4b_local.py
```

### Watch roots:
1. `C:\Users\kevin\Paramount\Paramount Oracle Assessment - ERP Phase 4a October 2026 Go-Live`
2. `C:\Users\kevin\Paramount\Paramount Oracle Assessment - ERP Phase 4b January 2027 Go-Live`
3. `C:\Paramount\local_docs`

### How it works:
- Scans all watch roots recursively for supported file types
- Skips files on the skip list, empty files, files >30MB, temp files (`~$*`)
- **source_key = full file path** — dedup via MD5 hash in `source_log.db`
- Files are **never moved** — source location tracked in source_log
- Calls `close_client()` then `gc.collect()` before handing off to `label_chunks.py --apply`

### Dropping files for ingest:
Copy files into `C:\Paramount\local_docs` and run the ingest script.

### Skip list management:
```python
from source_log import add_skip
add_skip("Archive", "folder", "Skip archive folders")
add_skip("filename.xlsx", "filename", "Raw data dump")
```

---

## 5b. Calendar Ingest

**Script:** `C:\GIT\ingest_calendar_batched.py`  
**Input:** `C:\ParamountRAG\outlook\calendar\meetings.json` (exported by `sync_outlook.py`)  
**Flask required:** NO — writes directly to Qdrant

```powershell
# Step 1 — export from Outlook:
python C:\ParamountRAG\scripts\sync_outlook.py --calendar

# Step 2 — ingest into Qdrant:
python C:\GIT\ingest_calendar_batched.py
```

---

## 5c. Slack Ingest (added 2026-10-12)

**Script:** `C:\GIT\ingest_slack.py`  
**Input:** `C:\ParamountRAG\slack\*.json` — one file per conversation  
**Flask required:** NO — writes directly to Qdrant. Stop Streamlit/Flask first.

The Slack MCP connector can't be called from Python, so export is done **in a Claude Code session**:
ask Claude to "pull Slack since <date>" → it reads each conversation via `slack_read_channel`
and writes/updates the JSON files. Then:

```
python C:\GIT\ingest_slack.py           # dry run — counts + sample
python C:\GIT\ingest_slack.py --apply   # write
```

- Also runs automatically as **Step 4 of `ingest_all.py`** (skip with `--skip-slack`) — ingests whatever JSON is on disk.
- One doc per conversation-day, source = `slack:<conversation>:<YYYY-MM-DD>`, `doc_type = "Slack"`
- Idempotent (re-run upserts same IDs). Skips days < 150 chars of text and anything before 2026-06-01.
- **Workspace:** connector is signed into **Skydance** (Paramount grid) only. Oracle One channels
  (e.g. `#psky_45_ppc_viacom`, C0BPPML1ZFS) and DMs with Oracle colleagues are NOT reachable —
  `slack_read_channel` returns an SSE parse error.
- **Oracle One via export zip:** `python -I C:\GIT\convert_slack_export.py <zip>` converts a Slack
  partial-archive zip (messages.jsonl + coverage.json) into `oracle-one-*.json` files here, then run
  `ingest_slack.py --apply`. Project channels + DMs only (edit `INCLUDE_CHANNELS`); Oracle corporate
  channels excluded. First load 2026-10-12: 29 conversations, 2,672 msgs, +549 chunks.
- Corporate channels (#announcements etc.) are curated by hand into `corp-channels-operational.json` —
  Aim High newsletters are excluded as noise.

---

## 6. Meeting Transcription & Ingest

### From MP4 recordings (Whisper transcription):

**Script:** `C:\ParamountRAG\scripts\transcribe_mp4.py`

```
cd C:\ParamountRAG
python scripts\transcribe_mp4.py
```

### From Zoom recordings (batch backlog):

**Script:** `C:\GIT\rag-system-qdrant\transcribe_backlog.py`

```
cd C:\GIT\rag-system-qdrant
python transcribe_backlog.py
```

### From Tactiq (meeting summaries):

**sync_tactiq.py subprocess is broken.** Use Tactiq MCP tools directly in-session:
1. `list_recent_meetings` — get meetings since last ingest
2. `get_meeting` — read `detailedSummary.content`
3. Call `python C:\ParamountRAG\scripts\ingest_tactiq.py --file <json>` per meeting

Filter to meetings since 2026-06-01 only.

---

## 7. Labeling Chunks

**Script:** `C:\ParamountRAG\scripts\label_chunks.py`

Labels chunks with doc type (Spreadsheet, FDD, Meeting, Tracker, etc.) using filename rules.
Uses Qdrant `scroll()` + `set_payload()` — requires exclusive lock on `qdrant_db`.

**Stop Flask and Streamlit before running manually.**

```
# Dry run — shows what would change:
python C:\ParamountRAG\scripts\label_chunks.py

# Apply changes:
python C:\ParamountRAG\scripts\label_chunks.py --apply
```

Runs automatically at end of `ingest_phase4a_4b_local.py` (after `close_client()` releases lock).

---

## 8. Agentic RAG (Streamlit UI)

A Claude tool-use agent that iteratively searches Qdrant (read-only).

**Start:** `C:\GIT\start_streamlit.bat`  
**URL:** `http://localhost:8501`  
**Model:** `claude-opus-4-8` with adaptive thinking  
**Scripts:** `C:\ParamountRAG\scripts\agent.py` + `app_agent.py`

**Search modes (added 2026-09-28):** sidebar toggle, default ⚡ Quick.
- ⚡ **Quick** — 2 search rounds, 8 results, `effort: low`, short cited answer + "Open items" (~15–30s)
- 🔍 **Deep** — 8 search rounds, 15 results, `effort: xhigh`, full numbered briefing + Recommended Follow-Ups (slower, costlier than pre-2026-09-28)
- "🔍 Go deeper" button under a Quick answer re-runs it in Deep mode. Settings live in `_MODES` in `agent.py`.
- `fileWatcherType = "none"` in `.streamlit/config.toml` — **restart Streamlit after editing agent.py / app_agent.py**.

**Qdrant lock:** Streamlit holds the lock while running. Stop it before any ingest or label_chunks run.

**Source links:** Links in the Streamlit UI use `file:///` URLs. Chrome blocks navigation
to `file:///` from web pages — links may not open directly. If you need clickable file links,
run Flask (`localhost:5000`) and switch link format back to `/open?path=...`.

**CLI mode (no Streamlit):**
```
python C:\ParamountRAG\scripts\agent_query_to_file.py "your question" --heading "Section Title" [--mode quick]   # default --mode deep
```
Output appends to `C:\Paramount\ARAG_DOCS\ARAG_Briefing_YYYY-MM-DD.md`.

---

## 9. Dashboard — Doc Requests & Deliverables

Access: `http://localhost:5000/dashboard` (requires Flask)

### Seeding data from Asana export:
```
cd C:\GIT\rag-system-qdrant
python sync_from_asana.py
```

CSV must be at: `C:\Paramount\local_docs\Oracle_ERP_-_RICEFW_Master.csv`

### Dashboard panels:
| Panel | Data source |
|-------|-------------|
| Deadlines & Overdue | Hardcoded milestones + RAG query |
| Open Items & Risks | RAG query |
| Recent Activity | Qdrant metadata (48h) |
| Stakeholder Pulse | Qdrant metadata |
| **Open Doc Requests** | `dashboard.db` → `doc_requests` |
| **Team Deliverables** | `dashboard.db` → `deliverables` |

---

## 10. Audit & Health Checks

### Quick CLI query:
```
cd C:\ParamountRAG
python scripts\query_to_file.py "What is the status of INT631?" --out answers.txt
```

### Check chunk count:
```python
from qdrant_client import QdrantClient
c = QdrantClient(path=r"C:\GIT\qdrant_db")
print(c.get_collection("rag_documents").points_count)
c.close()
```

### Check source_log:
```
sqlite3 C:\GIT\qdrant_db\source_log.db "SELECT COUNT(*) FROM source_log;"
```

---

## 11. Backup & Recovery

### On-demand backup (before any bulk ingest):

**Stop Flask and Streamlit first** — both hold the Qdrant lock.

```powershell
$ts = Get-Date -Format "yyyyMMdd_HHmm"
Copy-Item -Recurse "C:\GIT\qdrant_db" "Z:\ChromaBackups\qdrant_db_backup_$ts"
```

Always to `Z:\ChromaBackups\`. **Never** backup to `C:\GIT` or use robocopy.
Each call creates a new timestamped folder.

### If qdrant_db is lost or corrupt:
1. Stop Flask and Streamlit
2. Rename `C:\GIT\qdrant_db` to `qdrant_db_YYYYMMDD_broken`
3. Restore from `Z:\ChromaBackups\qdrant_db_backup_YYYYMMDD_HHMM\`
4. Re-run email ingest for any emails since the backup
5. Re-run document ingest for any docs since the backup

### Full Rebuild from scratch:
1. Stop Flask and Streamlit
2. Backup current DB (see above)
3. Rename `C:\GIT\qdrant_db` → `qdrant_db_pre_rebuild_YYYYMMDD`
4. `python C:\GIT\prep_qdrant_db.py` — creates fresh DB with skip list
4b. **Copy skip list from old DB into new DB (CRITICAL — do NOT skip):**
    ```python
    import sqlite3
    old = sqlite3.connect(r'C:\GIT\qdrant_db_pre_rebuild_YYYYMMDD\source_log.db')
    new = sqlite3.connect(r'C:\GIT\qdrant_db\source_log.db')
    rows = old.execute("SELECT pattern, match_type, note, added_at FROM skip_list").fetchall()
    new.executemany("INSERT OR IGNORE INTO skip_list (pattern, match_type, note, added_at) VALUES (?,?,?,?)", rows)
    new.commit(); old.close(); new.close()
    print(f"Copied {len(rows)} skip list entries")
    ```
    **Why:** The skip list lives inside source_log.db. A fresh rebuild starts with an empty
    skip list — any files previously blocked (e.g. GL recon dumps, data dumps) will be
    ingested again unless you copy the entries over before the first ingest run.
5. **Export from Outlook FIRST (essential — do NOT skip):**
   ```powershell
   python C:\ParamountRAG\scripts\sync_outlook.py --mail --full
   python C:\ParamountRAG\scripts\sync_outlook.py --calendar
   ```
   `--mail --full` ignores the cursor and exports ALL email history (not just since last sync).  
   `--all` without `--full` only does incremental mail — **wrong for a full rebuild**.  
   Calendar is always full-range so `--calendar` alone is sufficient.
6. Ingest documents: `python C:\GIT\ingest_phase4a_4b_local.py`
7. Ingest emails: `python C:\GIT\ingest_emails_batched.py`
8. Ingest calendar: `python C:\GIT\ingest_calendar_batched.py`
9. Ingest Tactiq meetings via MCP in-session (sync_tactiq.py subprocess is broken)
10. Restart Streamlit: `streamlit run C:\ParamountRAG\scripts\app_agent.py`

### Revert to ChromaDB (see §16):
Both `C:\GIT\chroma_db` and `C:\GIT\rag-system` are preserved and untouched.

---

## 12. Flask Application — Routes Reference

Flask is only needed for the dashboard and `/open` file links. Ingest and ARAG work without it.

| Route | Method | Purpose |
|-------|--------|---------|
| `/` | GET | Main RAG chat UI (legacy — use Streamlit ARAG instead) |
| `/dashboard` | GET | Phase 5 war-room dashboard |
| `/api/query` | POST | SSE streaming RAG query + Claude answer |
| `/api/recent` | GET | Recent ingested items (last 48h) |
| `/api/stakeholder_pulse` | GET | Last contact date per stakeholder |
| `/api/sources` | GET | List all ingested sources + chunk count |
| `/api/upload` | POST | Upload + ingest document files (ad-hoc) |
| `/open` | GET | Open a file in Windows (`?path=C:/...`) |
| `/api/schema` | GET | Metadata schema |
| `/api/phases` | GET | Distinct phase values for dashboard filter |
| `/api/doc_requests` | GET/POST | List / add doc requests |
| `/api/doc_requests/<id>` | PATCH | Update doc request row |
| `/api/deliverables` | GET/POST | List / add deliverables |
| `/api/deliverables/<id>` | PATCH | Update deliverable row |

---

## 13. Critical Rules — What Not To Do

1. **Don't use `--full` for routine email syncs**  
   Use `sync_outlook.py --mail` (incremental). `--full` re-exports all 3,000+ emails — only correct for a full rebuild (§11).

2. **Don't use Graph API for email**  
   `ingest_email.py` uses Microsoft Graph API. Always use `sync_outlook.py` (COM/Outlook).

3. **Don't delete `qdrant_db\` without a confirmed backup**  
   This is irreplaceable without a full re-ingest (~36K chunks).

4. **Don't delete `chroma_db\` or `rag-system\`**  
   These are the revert path to ChromaDB (126K chunks, 2026-08-18 rebuild).

5. **Don't ingest MP4 files**  
   They are large and useless for RAG. Transcribe to VTT first.

6. **Don't ingest anything dated before 2026-06-01** without asking.

7. **Don't run two Qdrant processes simultaneously**  
   Qdrant embedded lock is exclusive. Flask, Streamlit, ingest scripts, and label_chunks
   all need the lock. Only one at a time.

8. **Don't delete chunks from Qdrant**  
   Use skip list only. Wait for next rebuild.

9. **Don't add new email skip folders without updating BOTH places:**  
   - `C:\GIT\rag-system-qdrant\rag\email_json.py` → `_SKIP_FOLDERS`  
   - `C:\ParamountRAG\scripts\sync_outlook.py` → `_SKIP_FOLDER_FRAGMENTS`

10. **Don't ingest RICEFW/RAID CSVs via `ingest_asana_ricefw.py` or `ingest_asana_raid.py`**  
    These are ingested by `ingest_phase4a_4b_local.py` from `local_docs`. Don't double-ingest.

---

## 14. Troubleshooting

### `AlreadyLocked` / `Storage folder already accessed`
Another process has the Qdrant lock. Stop Flask, Streamlit, or any running Python process
that opened `qdrant_db`. Check: `Get-Process python`

### Dashboard shows 0 chunks / "DATABASE OFFLINE"
- Flask is not running → `cd C:\GIT\rag-system-qdrant && python app.py`

### Email query returns no results after sync
- Check ingest script output — look at `skipped_empty` count
- Verify source_log.db path: `C:\GIT\qdrant_db\source_log.db`

### sync_outlook.py only picks up 3–5 emails
- The cursor `last_sync.txt` exists → delete it for a full re-export:
  `del C:\ParamountRAG\outlook\email\last_sync.txt`

### Files re-ingesting every run (0 updated shown)
- source_key collision — two files with the same name in different folders
- Fixed in `ingest_phase4a_4b_local.py` (2026-08-23): source_key = full path

### label_chunks writes 0 labels
- Labeling runs after ingest. If it ran before the new chunks were committed, re-run manually:
  `python C:\ParamountRAG\scripts\label_chunks.py --apply`

### Two Flask processes → 404 on known routes
- Check: `netstat -ano | findstr ":5000"` — should be ONE listener.
- Kill all, restart once.

### Doc requests / deliverables panel empty
- `dashboard.db` may not exist → run `python sync_from_asana.py`

### Qdrant `UserWarning: Local mode is not recommended for collections with more than 20,000 points`
- Informational only — ignore. At ~36K points performance is fine. Not a reason to migrate to Docker.

---

## 15. Kevin's Task Tracker — Kevin_Todo.xlsx

**File:** `C:\Paramount\local_docs\Kevin_Todo.xlsx`

Always close Excel before running any script that writes to it.

### Column layout (A–G)
| Col | Field | Notes |
|-----|-------|-------|
| A | Status | Open / In Progress / Done / Blocked |
| B | Task | Wrap text |
| C | Workstream | AR / Integrations, AR / Billing, AR / Tax, GL / R2R, etc. |
| D | Source | "Meeting: Title YYYY-MM-DD" or "Email: subject + date" |
| E | Date Added | YYYY-MM-DD |
| F | Due Date | YYYY-MM-DD or blank |
| G | Notes | Brief context |

### Scripts
| Script | Purpose |
|--------|---------|
| `C:\GIT\scan_todos.py` | Dedup + append layer for automated scan |
| `C:\GIT\add_todo.py` | Manually append a single task |
| `C:\GIT\todo_cursor.json` | Last-scan timestamps per source |

### "Scan my todos" workflow
Triggered by saying **"scan my todos"** to Claude Code. Uses Tactiq MCP + emails.json + Slack.
Slack search: use `to:<@U0BA98BHRSQ>` — NOT generic "Kevin" (returns baseball noise).

---

## 16. Revert to ChromaDB

Both the old Flask app and ChromaDB are preserved intact. To revert:

1. **Stop Qdrant Flask and Streamlit** (Ctrl+C in their terminals)

2. **Restore source_log.py DB path:**
   Edit `C:\ParamountRAG\scripts\source_log.py` line 12:
   ```python
   _DB_PATH = Path(r"C:\GIT\chroma_db\source_log.db")
   ```

3. **Restore label_chunks.py** (Qdrant version committed — revert in git):
   ```
   git -C C:\ParamountRAG show HEAD~1:scripts/label_chunks.py > scripts/label_chunks.py
   ```

4. **Start old Flask:**
   ```
   cd C:\GIT\rag-system
   python app.py
   ```

ChromaDB at `C:\GIT\chroma_db` contains the 2026-08-18 full rebuild (126,664 chunks).

---

*Manual maintained by Claude Code. Update this manual at the end of each session.*
