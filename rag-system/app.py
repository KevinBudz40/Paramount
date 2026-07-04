"""RAG system Flask application."""
import json
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask, Response, jsonify, redirect, render_template,
    request, session, stream_with_context, url_for,
)

load_dotenv()

from rag.chunker import chunk_document, chunk_zip
from rag.generator import generate_answer
from rag.schema import DOC_TYPES, PRIORITIES, SENTIMENTS, WORKSTREAMS
from rag.store import (
    add_chunks, delete_source, doc_count, list_sources,
    query, hyde_query, recent_items, stakeholder_pulse,
)

_HERE = Path(__file__).resolve().parent
UPLOAD_DIR = _HERE / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
CHROMA_DIR = str(_HERE.parent / "chroma_db")
ALLOWED_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".csv", ".svg", ".vtt",
    ".docx", ".doc",
    ".xlsx", ".xls",
    ".pptx",
    ".zip",
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = None  # No limit — local dev server with trusted uploads
# Secret key for session-based CSRF state in OAuth flow.
# In production, set SECRET_KEY in .env to a fixed value so sessions
# survive server restarts.
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": f"Internal server error: {e}"}), 500

_AZURE_CONFIGURED = all(
    os.environ.get(k) for k in ("AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID")
)


def _allowed(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def _redirect_uri() -> str:
    return os.environ.get("AZURE_REDIRECT_URI", url_for("auth_callback", _external=True))


# ── Core routes ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


_STAKEHOLDERS = [
    "George Palahunik", "Karen Marx", "Sara Karpati", "Lawson James",
    "Najam Khan", "Ashwin Ayancha", "Kiki Vadivel", "Hemanth Gujjola",
]


@app.route("/api/recent")
def api_recent():
    try:
        hours = int(request.args.get("hours", 48))
    except ValueError:
        hours = 48
    items = recent_items(hours=hours, limit=20, persist_dir=CHROMA_DIR)
    return jsonify({"items": items, "total_chunks": doc_count(CHROMA_DIR)})


@app.route("/api/stakeholder_pulse")
def api_stakeholder_pulse():
    data = stakeholder_pulse(_STAKEHOLDERS, persist_dir=CHROMA_DIR)
    return jsonify({"stakeholders": data})


@app.route("/api/schema")
def schema_info():
    return jsonify({
        "workstreams": WORKSTREAMS,
        "doc_types": DOC_TYPES,
        "priorities": PRIORITIES,
        "sentiments": SENTIMENTS,
    })


# ── File upload ────────────────────────────────────────────────────────────

@app.route("/api/upload", methods=["POST"])
def upload():
    try:
        return _upload_inner()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _upload_inner():
    if "files" not in request.files:
        return jsonify({"error": "No files provided"}), 400

    raw_meta = request.form.get("metadata", "{}")
    try:
        doc_meta = json.loads(raw_meta)
    except json.JSONDecodeError:
        doc_meta = {}

    # Optional relative paths sent by the folder-upload UI (preserves folder structure)
    try:
        rel_paths = json.loads(request.form.get("paths", "[]"))
    except json.JSONDecodeError:
        rel_paths = []

    results = []
    for idx, file in enumerate(request.files.getlist("files")):
        if not file.filename or not _allowed(file.filename):
            results.append({"file": file.filename, "error": "Unsupported file type"})
            continue
        # Use caller-supplied relative path as source when available
        source_label = rel_paths[idx] if idx < len(rel_paths) else file.filename
        dest = UPLOAD_DIR / file.filename
        try:
            file.save(dest)
            if Path(file.filename).suffix.lower() == ".zip":
                chunks, report = chunk_zip(dest)
                count = add_chunks(chunks, doc_meta=doc_meta, persist_dir=CHROMA_DIR)
                results.append({
                    "file":            source_label,
                    "status":          "ok",
                    "chunks":          count,
                    "files_found":     report.files_found,
                    "files_ingested":  report.files_ingested,
                    "files_skipped":   report.files_skipped,
                    "skipped":         report.skipped,
                })
            else:
                chunks = chunk_document(dest)
                # Override source with relative path so folder structure is preserved
                for c in chunks:
                    c["source"] = source_label
                count = add_chunks(chunks, doc_meta=doc_meta, persist_dir=CHROMA_DIR)
                results.append({"file": source_label, "chunks": count, "status": "ok"})
        except Exception as e:
            app.logger.exception("Upload failed for %s", file.filename)
            results.append({"file": source_label, "error": str(e)})

    return jsonify({"results": results, "total_chunks": doc_count(CHROMA_DIR)})


# ── Sources ────────────────────────────────────────────────────────────────

@app.route("/api/ingest/zip", methods=["POST"])
def ingest_zip_stream():
    """
    Upload a zip file and stream per-file ingestion progress as SSE.
    Events: start → (file|skip)* → done | error
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename or Path(file.filename).suffix.lower() != ".zip":
        return jsonify({"error": "Expected a .zip file"}), 400

    raw_meta = request.form.get("metadata", "{}")
    try:
        doc_meta = json.loads(raw_meta)
    except json.JSONDecodeError:
        doc_meta = {}

    dest = UPLOAD_DIR / file.filename
    try:
        file.save(dest)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    def _generate():
        import shutil
        import tempfile
        from rag.chunker import (
            INGESTIBLE_EXTENSIONS, SKIP_EXTENSIONS,
            ZipReport, _chunk_text, _extract_zip, load_document,
        )
        from rag.store import ingested_sources

        report = ZipReport(zip_name=dest.name)
        tmp_dir = Path(tempfile.mkdtemp(prefix="rag_zip_"))

        def evt(payload: dict) -> str:
            return f"data:{json.dumps(payload)}\n\n"

        try:
            _extract_zip(dest, tmp_dir)

            all_files = sorted(f for f in tmp_dir.rglob("*") if f.is_file())
            total = len(all_files)

            # Load already-ingested sources once so we can skip them on retry
            already_done = ingested_sources(CHROMA_DIR)

            pending_chunks: list[dict] = []
            pending_events: list[dict] = []

            yield evt({"type": "start", "zip": dest.name, "total": total})

            for idx, fpath in enumerate(all_files):
                rel = fpath.relative_to(tmp_dir)
                rel_str = rel.as_posix()
                suffix = fpath.suffix.lower()
                report.files_found += 1
                i = idx + 1

                # Skip already-ingested files (resume after network drop)
                source = f"{dest.name}/{rel_str}"
                if source in already_done:
                    report.files_ingested += 1
                    yield evt({"type": "resume", "name": rel_str, "i": i, "total": total})
                    continue

                if any(p.startswith("__MACOSX") or p.startswith(".") for p in rel.parts):
                    report.files_skipped += 1
                    yield evt({"type": "skip", "name": rel_str, "i": i, "total": total})
                    continue

                if suffix in SKIP_EXTENSIONS or suffix not in INGESTIBLE_EXTENSIONS:
                    report.files_skipped += 1
                    yield evt({"type": "skip", "name": rel_str, "i": i, "total": total})
                    continue

                try:
                    text = load_document(fpath)
                    if not text or not text.strip():
                        report.files_skipped += 1
                        yield evt({"type": "skip", "name": rel_str, "i": i, "total": total})
                        continue

                    raw_chunks = _chunk_text(text)
                    pending_chunks.extend(
                        {"text": t, "source": source, "chunk_index": ci}
                        for ci, t in enumerate(raw_chunks)
                    )
                    pending_events.append({"type": "file", "name": rel_str,
                                           "chunks": len(raw_chunks), "i": i, "total": total})
                    report.files_ingested += 1

                    # Flush every 200 chunks — embeds a full batch at once
                    if len(pending_chunks) >= 200:
                        add_chunks(pending_chunks, doc_meta=doc_meta, persist_dir=CHROMA_DIR)
                        pending_chunks.clear()
                        for e in pending_events:
                            yield evt(e)
                        pending_events.clear()

                except Exception as exc:
                    report.files_skipped += 1
                    yield evt({"type": "skip", "name": rel_str, "i": i, "total": total,
                                "reason": str(exc)})

            # Flush remainder
            if pending_chunks:
                add_chunks(pending_chunks, doc_meta=doc_meta, persist_dir=CHROMA_DIR)
                for e in pending_events:
                    yield evt(e)

        except Exception as exc:
            yield evt({"type": "error", "error": str(exc)})
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        yield evt({
            "type":         "done",
            "ingested":     report.files_ingested,
            "skipped":      report.files_skipped,
            "found":        report.files_found,
            "total_chunks": doc_count(CHROMA_DIR),
        })
        yield "data:[DONE]\n\n"

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/sources")
def sources():
    return jsonify({
        "sources": list_sources(CHROMA_DIR),
        "total_chunks": doc_count(CHROMA_DIR),
    })


@app.route("/api/sources/<path:source_name>", methods=["DELETE"])
def remove_source(source_name):
    deleted = delete_source(source_name, CHROMA_DIR)
    return jsonify({"deleted_chunks": deleted, "total_chunks": doc_count(CHROMA_DIR)})


# ── Query / generation ────────────────────────────────────────────────────

@app.route("/api/query", methods=["POST"])
def query_rag():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    n = int(data.get("n_results", 5))
    filters = data.get("filters") or {}

    # HyDE: generate hypothetical passage in the main request thread (before SSE opens)
    # then merge with a standard query for best-of-both retrieval.
    from rag.store import _generate_hypothetical
    try:
        hypothetical = _generate_hypothetical(question)
        chunks = hyde_query(question, n_results=n, filters=filters, persist_dir=CHROMA_DIR,
                            hypothetical=hypothetical)
    except Exception:
        # Fallback to standard query if HyDE generation fails
        chunks = query(question, n_results=n, filters=filters, persist_dir=CHROMA_DIR)

    sources_used = [
        {
            "source":    c["source"],
            "score":     c["score"],
            "author":    c.get("author", ""),
            "date":      c.get("date", ""),
            "workstream": c.get("workstream", ""),
            "doc_type":  c.get("doc_type", ""),
            "risk_flag": c.get("risk_flag", False),
            "priority":  c.get("priority", ""),
        }
        for c in chunks
    ]

    def full_stream():
        yield f"data:sources:{json.dumps(sources_used)}\n\n"
        try:
            for token in generate_answer(question, chunks):
                # JSON-encode each token so embedded newlines never break SSE framing
                yield f"data:t:{json.dumps(token)}\n\n"
        except Exception as exc:
            yield f"data:error:{json.dumps(str(exc))}\n\n"
        yield "data:[DONE]\n\n"

    return Response(
        stream_with_context(full_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Microsoft Graph OAuth2 ─────────────────────────────────────────────────

@app.route("/auth/login")
def auth_login():
    if not _AZURE_CONFIGURED:
        return jsonify({"error": "Azure credentials not configured in .env"}), 503
    from rag.graph.auth import get_auth_url
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    return redirect(get_auth_url(redirect_uri=_redirect_uri(), state=state))


@app.route("/auth/callback")
def auth_callback():
    """
    Microsoft redirects here after the user signs in.
    Verifies state (CSRF), exchanges code for tokens, redirects to /.
    """
    error = request.args.get("error")
    if error:
        desc = request.args.get("error_description", error)
        return render_template("auth_error.html", error=desc), 400

    returned_state = request.args.get("state", "")
    expected_state = session.pop("oauth_state", None)
    if not expected_state or returned_state != expected_state:
        return jsonify({"error": "OAuth state mismatch — possible CSRF"}), 400

    code = request.args.get("code")
    if not code:
        return jsonify({"error": "No authorization code in callback"}), 400

    from rag.graph.auth import exchange_code
    try:
        exchange_code(code, redirect_uri=_redirect_uri())
    except ValueError as exc:
        return render_template("auth_error.html", error=str(exc)), 400

    return redirect(url_for("index"))


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    from rag.graph.auth import logout
    logout()
    return jsonify({"status": "logged out"})


@app.route("/auth/status")
def auth_status():
    if not _AZURE_CONFIGURED:
        return jsonify({"configured": False, "authenticated": False})
    try:
        from rag.graph.auth import get_account, is_authenticated
        account = get_account()
    except ValueError:
        # MSAL raises ValueError when tenant ID is still a placeholder
        return jsonify({"configured": False, "authenticated": False,
                        "error": "Invalid Azure credentials — check AZURE_TENANT_ID in .env"})
    return jsonify({
        "configured":     True,
        "authenticated":  account is not None,
        "username":       account.get("username") if account else None,
    })


# ── Graph ingestion endpoints ──────────────────────────────────────────────

def _require_graph():
    """Return 503 if Azure is not configured, 401 if not authenticated."""
    if not _AZURE_CONFIGURED:
        return jsonify({"error": "Azure credentials not configured"}), 503
    from rag.graph.auth import is_authenticated
    if not is_authenticated():
        return jsonify({"error": "Not authenticated — visit /auth/login"}), 401
    return None


@app.route("/api/graph/mail", methods=["POST"])
def graph_mail():
    err = _require_graph()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    folder = data.get("folder", "inbox")
    top    = int(data.get("top", 50))
    doc_meta_override = data.get("doc_meta", {})

    from rag.graph.mail import fetch_messages
    from rag.chunker import _chunk_text

    try:
        records = fetch_messages(folder=folder, top=top)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    total = 0
    for rec in records:
        chunks_text = _chunk_text(rec["text"])
        chunks = [
            {"text": t, "source": rec["source"], "chunk_index": i}
            for i, t in enumerate(chunks_text)
        ]
        meta = {**rec["doc_meta"], **doc_meta_override}
        total += add_chunks(chunks, doc_meta=meta, persist_dir=CHROMA_DIR)

    return jsonify({"ingested_messages": len(records), "chunks": total,
                    "total_chunks": doc_count(CHROMA_DIR)})


@app.route("/api/graph/calendar", methods=["POST"])
def graph_calendar():
    err = _require_graph()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    days_back    = int(data.get("days_back", 30))
    days_forward = int(data.get("days_forward", 90))
    doc_meta_override = data.get("doc_meta", {})

    from rag.graph.calendar import fetch_events
    from rag.chunker import _chunk_text

    try:
        records = fetch_events(days_back=days_back, days_forward=days_forward)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    total = 0
    for rec in records:
        chunks_text = _chunk_text(rec["text"])
        chunks = [
            {"text": t, "source": rec["source"], "chunk_index": i}
            for i, t in enumerate(chunks_text)
        ]
        meta = {**rec["doc_meta"], **doc_meta_override}
        total += add_chunks(chunks, doc_meta=meta, persist_dir=CHROMA_DIR)

    return jsonify({"ingested_events": len(records), "chunks": total,
                    "total_chunks": doc_count(CHROMA_DIR)})


@app.route("/api/graph/sharepoint", methods=["POST"])
def graph_sharepoint():
    """
    Ingest documents from a SharePoint site.

    Body (JSON):
      site_url    — required — e.g. "oracle.sharepoint.com:/sites/ProjectAlpha"
      drive_names — optional — list of library names to limit ingestion
      doc_meta    — optional — Paramount metadata overrides for all chunks
      use_my_drives — optional bool — fall back to /me/drives if Sites.Read.All blocked
    """
    err = _require_graph()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    site_url          = (data.get("site_url") or "").strip()
    drive_names       = data.get("drive_names") or None
    doc_meta_override = data.get("doc_meta") or {}
    use_my_drives     = bool(data.get("use_my_drives", False))

    from rag.graph import sharepoint as sp

    try:
        if use_my_drives:
            all_chunks, errors = sp.ingest_my_drives()
        else:
            if not site_url:
                return jsonify({"error": "site_url is required"}), 400
            all_chunks, errors = sp.ingest_site(site_url, drive_names=drive_names)
    except Exception as exc:
        # Surface SharePoint-specific errors clearly
        msg = str(exc)
        status = 403 if "403" in msg else 404 if "404" in msg else 500
        return jsonify({"error": msg}), status

    total = 0
    for chunk in all_chunks:
        meta = {**chunk.get("doc_meta", {}), **doc_meta_override}
        total += add_chunks(
            [{"text": chunk["text"], "source": chunk["source"],
              "chunk_index": chunk["chunk_index"]}],
            doc_meta=meta,
            persist_dir=CHROMA_DIR,
        )

    return jsonify({
        "ingested_files": len({c["source"] for c in all_chunks}),
        "chunks":         total,
        "errors":         errors,
        "total_chunks":   doc_count(CHROMA_DIR),
    })


@app.route("/api/graph/sharepoint/sites")
def graph_sharepoint_sites():
    """Search SharePoint sites (requires Sites.Read.All admin consent)."""
    err = _require_graph()
    if err:
        return err

    q = request.args.get("q", "*")
    from rag.graph.sharepoint import search_sites
    try:
        sites = search_sites(q)
        return jsonify({"sites": sites})
    except Exception as exc:
        msg = str(exc)
        status = 403 if "403" in msg else 500
        return jsonify({"error": msg, "sites": []}), status


# ── JSON email archive ingestion ──────────────────────────────────────────

_DEFAULT_EMAIL_JSON = Path(r"C:\ParamountRAG\outlook\email\emails.json")


@app.route("/api/ingest/email-json", methods=["POST"])
def ingest_email_json():
    """
    Ingest the Paramount emails.json archive into ChromaDB.

    Body (JSON, all optional):
      path        — override the default archive path
      workstream  — Paramount workstream tag to apply to all messages
    """
    data = request.get_json(silent=True) or {}
    archive_path = Path(data.get("path") or _DEFAULT_EMAIL_JSON)
    workstream   = data.get("workstream", "")

    if not archive_path.exists():
        return jsonify({"error": f"File not found: {archive_path}"}), 404

    from rag.email_json import parse_email_json

    try:
        chunks, result = parse_email_json(archive_path)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    # Apply workstream override and persist
    for chunk in chunks:
        if workstream:
            chunk["doc_meta"]["workstream"] = workstream
        add_chunks(
            [{"text": chunk["text"], "source": chunk["source"],
              "chunk_index": chunk["chunk_index"]}],
            doc_meta=chunk["doc_meta"],
            persist_dir=CHROMA_DIR,
        )

    return jsonify({
        "processed":       result.processed,
        "skipped_folder":  result.skipped_folder,
        "skipped_sender":  result.skipped_sender,
        "skipped_empty":   result.skipped_empty,
        "total_skipped":   result.skipped_folder + result.skipped_sender + result.skipped_empty,
        "chunks_ingested": result.total_chunks,
        "total_chunks":    doc_count(CHROMA_DIR),
    })


@app.route("/api/ingest/attachments", methods=["POST"])
def ingest_attachments():
    """
    Ingest all files in the email attachments folder into ChromaDB.
    Runs in-process (safe — same ChromaDB connection as Flask).
    Returns counts of ingested, skipped, and errors.
    """
    from ingest_folder import _collect_files, _ingest_file, _open_db, _is_done, _record

    attachments_dir = Path(r"C:\ParamountRAG\outlook\attachments\email")
    tracker_db = str(_HERE / "ingest_tracker.db")

    if not attachments_dir.exists():
        return jsonify({"error": f"Attachments folder not found: {attachments_dir}"}), 404

    all_files = _collect_files(attachments_dir)
    con = _open_db(tracker_db)

    ingested = skipped = errors = 0
    error_list = []

    for fpath in all_files:
        try:
            source_label = fpath.relative_to(attachments_dir).as_posix()
        except ValueError:
            source_label = fpath.name

        if _is_done(con, source_label):
            skipped += 1
            continue

        try:
            n = _ingest_file(fpath, source_label, CHROMA_DIR)
            if n == 0:
                _record(con, source_label, "empty")
                skipped += 1
            else:
                _record(con, source_label, "ok", chunks=n)
                ingested += 1
        except Exception as exc:
            _record(con, source_label, "error", error=str(exc))
            error_list.append({"file": source_label, "error": str(exc)})
            errors += 1

    con.close()
    return jsonify({
        "ingested": ingested,
        "skipped": skipped,
        "errors": errors,
        "error_detail": error_list,
        "total_chunks": doc_count(CHROMA_DIR),
    })


# ── Dashboard tracking panels ─────────────────────────────────────────────

from dashboard_db import (
    get_doc_requests, add_doc_request, patch_doc_request,
    get_deliverables, add_deliverable, patch_deliverable,
    get_phases,
)


@app.route("/api/phases")
def api_phases():
    return jsonify({"phases": get_phases()})


@app.route("/api/doc_requests", methods=["GET"])
def api_doc_requests_get():
    phase = request.args.get("phase", "")
    return jsonify({"items": get_doc_requests(phase=phase)})


@app.route("/api/doc_requests", methods=["POST"])
def api_doc_requests_post():
    data = request.get_json(silent=True) or {}
    if not data.get("object_name") or not data.get("date_requested"):
        return jsonify({"error": "object_name and date_requested are required"}), 400
    row_id = add_doc_request(data)
    return jsonify({"id": row_id}), 201


@app.route("/api/doc_requests/<int:row_id>", methods=["PATCH"])
def api_doc_requests_patch(row_id):
    data = request.get_json(silent=True) or {}
    patch_doc_request(row_id, data)
    return jsonify({"ok": True})


@app.route("/api/deliverables", methods=["GET"])
def api_deliverables_get():
    phase = request.args.get("phase", "")
    return jsonify({"items": get_deliverables(phase=phase)})


@app.route("/api/deliverables", methods=["POST"])
def api_deliverables_post():
    data = request.get_json(silent=True) or {}
    if not data.get("deliverable_name"):
        return jsonify({"error": "deliverable_name is required"}), 400
    row_id = add_deliverable(data)
    return jsonify({"id": row_id}), 201


@app.route("/api/deliverables/<int:row_id>", methods=["PATCH"])
def api_deliverables_patch(row_id):
    data = request.get_json(silent=True) or {}
    patch_deliverable(row_id, data)
    return jsonify({"ok": True})


# ── Startup ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  RAG System running at http://localhost:{port}")
    print(f"  ChromaDB path: {CHROMA_DIR}\n")
    if not _AZURE_CONFIGURED:
        print("  [M365] Azure credentials not set — Graph features disabled.")
        print("  Set AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID in .env\n")
    # Pre-load ChromaDB + embedding model in the main thread before Flask starts
    # serving. Prevents ONNX/HNSW crash when first load happens inside a request thread.
    print("  Pre-loading ChromaDB...")
    _preload_count = doc_count(CHROMA_DIR)
    print(f"  Ready — {_preload_count:,} chunks indexed.\n")
    app.run(debug=True, port=port, threaded=True, use_reloader=False)
