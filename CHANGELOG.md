# RAG System Change Log

All significant changes recorded here. Format: date, what changed, files touched, recovery point tag.

---

## 2026-07-03 — Phase 5 Dashboard Extension (Doc Requests + Deliverables)

**Recovery point:** `git tag recovery/2026-07-03-dashboard-panels`

### What was built
- Two new full-width panels on `localhost:5000/dashboard`
- Panel 6: Open Doc Requests — tracks FDD/TDD docs blocking design sessions
- Panel 7: Team Deliverables — tracks O2C/AR integration deliverables

### New files
- `rag-system/dashboard_db.py` — SQLite schema, CRUD, business-day staleness math
- `rag-system/sync_from_asana.py` — seeds dashboard.db from RICEFW CSV export
- `backup_rag.bat` — nightly robocopy of chroma_db + dashboard.db to OneDrive
- `.gitignore` — excludes chroma_db, uploads, logs, WAL files

### Modified files
- `rag-system/app.py` — added 7 new routes: /api/phases, /api/doc_requests (GET/POST/PATCH), /api/deliverables (GET/POST/PATCH)
- `rag-system/templates/dashboard.html` — added 2 panels, edit modal, phase filter dropdown, JS for both panels
- `rag-system/static/dashboard.css` — added panel styles, edit form styles, row highlight classes

### Database
- `dashboard.db` at `C:\GIT\dashboard.db` (separate from ChromaDB — safe to recreate)
- Seeded from: `C:\Paramount\done\Oracle_ERP_-_RICEFW_Master.csv` (Asana export 2026-07-01)
- 188 deliverables, 167 doc_requests (O2C AR, Phase 4a filter active by default)
- Re-seed: `cd C:\GIT\rag-system && python sync_from_asana.py`

### Backup
- First backup completed: 8.881 GB to `C:\Users\kevin\OneDrive\Backups\chroma_db`
- Nightly scheduled task registered: "RAG ChromaDB Backup" at 2:00 AM

### Known open items
- `date_requested` on all doc_requests = 2026-07-03 placeholder — update per item via edit modal
- Phase filter defaults to Phase 4a (93 items) — Kevin wants to match "Phase 4a O2C Integrations" Asana view (~13 items); need to identify the 4 Asana filters active in that view
- `blocked_by` on Blocked deliverables shows `[check Asana]` where Asana had no dependency text

---

## 2026-07-02 — Email HTML Parsing Fix

**Recovery point:** `git tag recovery/2026-07-02-email-html-fix`

### What was fixed
- HTML-only emails were silently skipped — `plain_body` was empty, `html_body` had content
- `_HTMLStripper` was extracting CSS from `<style>` tags as text body
- `_clean_body` then stripped Oracle Confidential banners leaving nothing → email skipped

### Modified files
- `rag-system/rag/email_json.py` — rewrote `_HTMLStripper` to skip style/script/head tags; added fallback so `_clean_body` nuking content doesn't silently drop emails

### Impact
- Full re-sync and re-ingest performed: 341 emails ingested (was 326)
- George Palahunik's Prem James OOO email now queryable

---
