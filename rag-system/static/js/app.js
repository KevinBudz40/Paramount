/* RAG System — Paramount metadata + M365 edition */

// ── M365 / Graph ──────────────────────────────────────

async function checkM365Status() {
  try {
    const { configured, authenticated, username } = await fetch("/auth/status").then(r => r.json());
    if (!configured) {
      document.getElementById("m365-panel").style.opacity = "0.45";
      document.getElementById("m365-connect-btn").disabled = true;
      document.getElementById("m365-connect-btn").title = "Set AZURE_* vars in .env to enable";
      return;
    }
    if (authenticated) {
      showM365Connected(username);
    } else {
      document.getElementById("m365-disconnected").hidden = false;
      document.getElementById("m365-connected").hidden = true;
    }
  } catch (_) {}
}

function showM365Connected(username) {
  document.getElementById("m365-disconnected").hidden = true;
  document.getElementById("m365-connected").hidden = false;
  document.getElementById("m365-username").textContent = username || "Connected";
}

function connectM365() {
  window.location.href = "/auth/login";
}

async function disconnectM365() {
  await fetch("/auth/logout", { method: "POST" });
  document.getElementById("m365-disconnected").hidden = false;
  document.getElementById("m365-connected").hidden = true;
  setM365Status("", "");
}

function setM365Status(msg, type = "info") {
  const el = document.getElementById("m365-status");
  el.textContent = msg;
  el.className = `m365-status-msg ${type}`;
}

async function syncEmail() {
  const folder = document.getElementById("email-folder").value;
  const top    = parseInt(document.getElementById("email-top").value);
  setM365Status("Syncing email…", "info");
  try {
    const r = await fetch("/api/graph/mail", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder, top }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error);
    setM365Status(`✓ ${data.ingested_messages} emails → ${data.chunks} chunks`, "ok");
    loadSources();
  } catch (e) {
    setM365Status(`Email sync failed: ${e.message}`, "err");
  }
}

async function syncCalendar() {
  const days_back    = parseInt(document.getElementById("cal-back").value);
  const days_forward = parseInt(document.getElementById("cal-forward").value);
  setM365Status("Syncing calendar…", "info");
  try {
    const r = await fetch("/api/graph/calendar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ days_back, days_forward }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error);
    setM365Status(`✓ ${data.ingested_events} events → ${data.chunks} chunks`, "ok");
    loadSources();
  } catch (e) {
    setM365Status(`Calendar sync failed: ${e.message}`, "err");
  }
}

async function syncSharePoint() {
  const site_url = document.getElementById("sp-site-url").value.trim();
  if (!site_url) { setM365Status("Enter a SharePoint site URL first.", "err"); return; }
  setM365Status("Syncing SharePoint site…", "info");
  try {
    const r = await fetch("/api/graph/sharepoint", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ site_url }),
    });
    const data = await r.json();
    if (!r.ok) {
      // Surface SharePoint 403 with actionable message
      if (r.status === 403) {
        setM365Status(
          "Access denied (403). Sites.Read.All requires admin consent — " +
          "ask your Oracle IT admin, or use 'My Drives' instead.", "err"
        );
      } else {
        throw new Error(data.error);
      }
      return;
    }
    const errNote = data.errors?.length ? ` (${data.errors.length} skipped)` : "";
    setM365Status(`✓ ${data.ingested_files} files → ${data.chunks} chunks${errNote}`, "ok");
    loadSources();
  } catch (e) {
    setM365Status(`SharePoint sync failed: ${e.message}`, "err");
  }
}

async function syncMyDrives() {
  setM365Status("Syncing My Drives (no admin consent needed)…", "info");
  try {
    const r = await fetch("/api/graph/sharepoint", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ use_my_drives: true }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error);
    const errNote = data.errors?.length ? ` (${data.errors.length} skipped)` : "";
    setM365Status(`✓ ${data.ingested_files} files → ${data.chunks} chunks${errNote}`, "ok");
    loadSources();
  } catch (e) {
    setM365Status(`My Drives sync failed: ${e.message}`, "err");
  }
}

async function searchSites() {
  const q = document.getElementById("sp-search-q").value.trim() || "*";
  setM365Status("Searching sites…", "info");
  const resultsList = document.getElementById("sp-site-results");
  resultsList.hidden = true;
  try {
    const r = await fetch(`/api/graph/sharepoint/sites?q=${encodeURIComponent(q)}`);
    const data = await r.json();
    if (!r.ok) {
      if (r.status === 403) {
        setM365Status("Site search requires Sites.Read.All (admin consent). Enter the site URL directly instead.", "err");
      } else {
        throw new Error(data.error);
      }
      return;
    }
    if (!data.sites.length) { setM365Status("No sites found.", "info"); return; }
    setM365Status(`${data.sites.length} site(s) found — click to set URL.`, "ok");
    resultsList.innerHTML = data.sites.map(s => `
      <li class="sp-site-item" onclick="selectSite('${s.url.replace(/'/g, "\\'")}')">
        <span class="sp-site-name">${s.name || s.id}</span>
        <span class="sp-site-url">${s.url}</span>
      </li>`).join("");
    resultsList.hidden = false;
  } catch (e) {
    setM365Status(`Search failed: ${e.message}`, "err");
  }
}

function selectSite(webUrl) {
  // Convert full web URL to Graph hostname:path fragment
  // e.g. https://oracle.sharepoint.com/sites/Alpha → oracle.sharepoint.com:/sites/Alpha
  try {
    const u = new URL(webUrl);
    document.getElementById("sp-site-url").value = `${u.hostname}:${u.pathname}`;
  } catch (_) {
    document.getElementById("sp-site-url").value = webUrl;
  }
  document.getElementById("sp-site-results").hidden = true;
  setM365Status("Site URL set. Click 'Sync Site' to ingest.", "info");
}

// ── DOM refs ─────────────────────────────────────────
const messagesEl   = document.getElementById("messages");
const form         = document.getElementById("query-form");
const input        = document.getElementById("question-input");
const sendBtn      = document.getElementById("send-btn");
const dropZone     = document.getElementById("drop-zone");
const fileInput    = document.getElementById("file-input");
const uploadStatus = document.getElementById("upload-status");
const sourceList   = document.getElementById("source-list");
const chunkCount   = document.getElementById("chunk-count");
const stagedArea   = document.getElementById("staged-area");
const stagedList   = document.getElementById("staged-list");
const ingestBtn    = document.getElementById("ingest-btn");
const metaDetails  = document.getElementById("meta-details");
const metaFilled   = document.getElementById("meta-filled");

// Filter bar
const filterToggle      = document.getElementById("filter-toggle");
const filterRow         = document.getElementById("filter-row");
const activeFilterCount = document.getElementById("active-filter-count");
const clearFiltersBtn   = document.getElementById("clear-filters");

// ── Schema (populated from /api/schema) ──────────────
let SCHEMA = { workstreams: [], doc_types: [], priorities: [], sentiments: [] };

async function loadSchema() {
  try {
    SCHEMA = await fetch("/api/schema").then(r => r.json());
    populateSelects();
  } catch (_) {}
}

function populateSelects() {
  const pairs = [
    ["m-workstream",  SCHEMA.workstreams, "f-workstream"],
    ["m-doc_type",    SCHEMA.doc_types,   "f-doc_type"],
    ["m-priority",    SCHEMA.priorities,  "f-priority"],
    ["m-sentiment",   SCHEMA.sentiments,  "f-sentiment"],
  ];
  pairs.forEach(([metaId, opts, filterId]) => {
    const mSel = document.getElementById(metaId);
    const fSel = filterId ? document.getElementById(filterId) : null;
    opts.forEach(o => {
      if (mSel) mSel.add(new Option(o, o));
      if (fSel) fSel.add(new Option(o, o));
    });
  });
}

// ── Staged-file queue ─────────────────────────────────
// Each entry: { file: File, relativePath: string }
let stagedFiles = [];

function stageFiles(files) {
  files.forEach(f => {
    const rel = f.webkitRelativePath || f.name;
    if (!stagedFiles.find(s => s.relativePath === rel)) {
      stagedFiles.push({ file: f, relativePath: rel });
    }
  });
  renderStaged();
}

function renderStaged() {
  if (!stagedFiles.length) {
    stagedArea.hidden = true;
    return;
  }
  stagedArea.hidden = false;
  stagedList.innerHTML = stagedFiles.map((s, i) => `
    <li class="staged-item">
      <span title="${s.relativePath}">${s.relativePath}</span>
      <button class="rm" onclick="removeStaged(${i})" title="Remove">×</button>
    </li>`).join("");
  updateMetaCount();
}

window.removeStaged = function(i) {
  stagedFiles.splice(i, 1);
  renderStaged();
};

// ── Metadata form helpers ─────────────────────────────
const LIST_META_FIELDS = ["recipients", "mentioned_people", "keywords", "action_items"];

function collectMetadata() {
  const metaForm = document.getElementById("meta-form");
  const meta = {};
  metaForm.querySelectorAll("input, select").forEach(el => {
    const name = el.name;
    if (!name) return;
    if (el.type === "checkbox") {
      meta[name] = el.checked;
    } else if (LIST_META_FIELDS.includes(name)) {
      meta[name] = el.value.split(",").map(s => s.trim()).filter(Boolean);
    } else {
      meta[name] = el.value;
    }
  });
  return meta;
}

function updateMetaCount() {
  const meta = collectMetadata();
  let count = 0;
  Object.entries(meta).forEach(([k, v]) => {
    if (v === "" || v === false || (Array.isArray(v) && !v.length)) return;
    count++;
  });
  metaFilled.textContent = `${count} field${count !== 1 ? "s" : ""}`;
  metaFilled.style.display = count ? "" : "none";
}

document.getElementById("meta-form").addEventListener("input", updateMetaCount);
document.getElementById("meta-form").addEventListener("change", updateMetaCount);

// ── Upload & ingest ───────────────────────────────────
async function ingestStaged() {
  if (!stagedFiles.length) return;
  ingestBtn.disabled = true;

  const meta = collectMetadata();
  const zips = stagedFiles.filter(s => s.file.name.toLowerCase().endsWith(".zip"));
  const others = stagedFiles.filter(s => !s.file.name.toLowerCase().endsWith(".zip"));
  const resultLines = [];

  // ── Non-zip files: single batch request ──────────────────────
  if (others.length) {
    uploadStatus.innerHTML = '<div class="upload-item">Ingesting…</div>';
    const fd = new FormData();
    others.forEach(s => fd.append("files", s.file));
    fd.append("metadata", JSON.stringify(meta));
    fd.append("paths", JSON.stringify(others.map(s => s.relativePath)));
    try {
      const r = await fetch("/api/upload", { method: "POST", body: fd });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || `Server error ${r.status}`);
      data.results.forEach(res => {
        if (res.error) {
          resultLines.push(`<div class="upload-item"><span class="err">✗</span> ${res.file}: ${res.error}</div>`);
        } else {
          resultLines.push(`<div class="upload-item"><span class="ok">✓</span> ${res.file} <span style="color:var(--text-dim)">(${res.chunks} chunks)</span></div>`);
        }
      });
    } catch (e) {
      resultLines.push(`<div class="upload-item"><span class="err">✗</span> Upload failed: ${e.message}</div>`);
    }
  }

  // ── Zip files: SSE progress stream, one at a time ─────────────
  for (const zipEntry of zips) {
    const zipFile = zipEntry.file;
    const progressId = `zip-progress-${Date.now()}`;
    resultLines.push(`<div id="${progressId}" class="zip-progress">
      <div class="upload-item"><span style="color:var(--accent)">⏳</span> ${zipFile.name}</div>
      <div class="zip-progress-bar-wrap"><div class="zip-progress-bar" id="${progressId}-bar"></div></div>
      <div class="zip-progress-label" id="${progressId}-label">Uploading…</div>
      <div class="zip-progress-counts" id="${progressId}-counts"></div>
    </div>`);
    uploadStatus.innerHTML = resultLines.join("");

    const bar    = () => document.getElementById(`${progressId}-bar`);
    const label  = () => document.getElementById(`${progressId}-label`);
    const counts = () => document.getElementById(`${progressId}-counts`);
    const icon   = () => document.querySelector(`#${progressId} .upload-item span`);

    const fd = new FormData();
    fd.append("file", zipFile);
    fd.append("metadata", JSON.stringify(meta));

    try {
      const r = await fetch("/api/ingest/zip", { method: "POST", body: fd });
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        throw new Error(data.error || `Server error ${r.status}`);
      }

      // Parse SSE from the response body stream
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      let ingested = 0, skipped = 0, total = 0;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop(); // keep incomplete last line

        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5);
          if (payload === "[DONE]") break;
          let evt;
          try { evt = JSON.parse(payload); } catch { continue; }

          if (evt.type === "start") {
            total = evt.total;
            label().textContent = `Extracting ${total} entries…`;
          } else if (evt.type === "file" || evt.type === "resume") {
            ingested++;
            const pct = Math.round((evt.i / total) * 100);
            bar().style.width = pct + "%";
            label().textContent = evt.type === "resume" ? `↩ ${evt.name}` : evt.name;
            counts().textContent = `${evt.i}/${total} · ${ingested} ingested`;
          } else if (evt.type === "skip") {
            skipped++;
            const pct = Math.round((evt.i / total) * 100);
            bar().style.width = pct + "%";
            counts().textContent = `${evt.i}/${total} · ${ingested} ingested · ${skipped} skipped`;
          } else if (evt.type === "done") {
            bar().style.width = "100%";
            const skipNote = evt.skipped ? ` · ${evt.skipped} skipped` : "";
            label().textContent = `✓ ${evt.ingested}/${evt.found} files ingested${skipNote}`;
            counts().textContent = `${evt.total_chunks} chunks total in knowledge base`;
            icon().textContent = "✓";
            icon().style.color = "var(--success)";
            loadSources();
          } else if (evt.type === "error") {
            label().textContent = `✗ ${evt.error}`;
            label().style.color = "var(--danger)";
            icon().textContent = "✗";
            icon().style.color = "var(--danger)";
          }
        }
      }
    } catch (e) {
      if (label()) { label().textContent = `✗ ${e.message}`; label().style.color = "var(--danger)"; }
      if (icon())  { icon().textContent = "✗"; icon().style.color = "var(--danger)"; }
    }
  }

  stagedFiles = [];
  renderStaged();
  ingestBtn.disabled = false;
}

ingestBtn.addEventListener("click", ingestStaged);

// Drag-and-drop / browse
dropZone.addEventListener("dragover",  e => { e.preventDefault(); dropZone.classList.add("over"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("over"));
dropZone.addEventListener("drop", e => {
  e.preventDefault();
  dropZone.classList.remove("over");
  stageFiles([...e.dataTransfer.files]);
});
dropZone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => { stageFiles([...fileInput.files]); fileInput.value = ""; });
document.getElementById("folder-input").addEventListener("change", function() {
  stageFiles([...this.files]);
  this.value = "";
});

// ── Sources sidebar ───────────────────────────────────
async function loadSources() {
  try {
    const { sources, total_chunks } = await fetch("/api/sources").then(r => r.json());
    chunkCount.textContent = `${total_chunks} chunks`;
    renderSources(sources);
    // Populate source filter dropdown
    const sel = document.getElementById("f-source");
    const prev = sel.value;
    sel.innerHTML = '<option value="">All sources</option>' +
      (sources || []).map(s => `<option value="${s.source}">${s.source}</option>`).join("");
    if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
  } catch (_) {}
}

function renderSources(sources) {
  if (!sources || !sources.length) {
    sourceList.innerHTML = '<li class="empty">No documents indexed yet.</li>';
    return;
  }
  sourceList.innerHTML = sources.map(s => {
    const chips = [];
    if (s.workstream) chips.push(`<span class="meta-chip ws">${s.workstream}</span>`);
    if (s.doc_type)   chips.push(`<span class="meta-chip dt">${s.doc_type}</span>`);
    if (s.priority)   chips.push(`<span class="meta-chip pri">${s.priority}</span>`);
    if (s.risk_flag)  chips.push(`<span class="meta-chip risk">⚠ Risk</span>`);
    const chipHtml = chips.length ? `<div class="source-meta-chips">${chips.join("")}</div>` : "";
    const safeName = s.source.replace(/'/g, "\\'");
    return `
      <li class="source-item">
        <div class="source-item-row">
          <span class="source-item-name" title="${s.source}">${s.source}</span>
          <button onclick="removeSource('${safeName}')" title="Remove">
            <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
        ${s.author ? `<div style="font-size:.72rem;color:var(--text-dim)">${s.author}${s.date ? " · " + s.date : ""}</div>` : ""}
        ${chipHtml}
      </li>`;
  }).join("");
}

window.removeSource = async function(name) {
  await fetch(`/api/sources/${encodeURIComponent(name)}`, { method: "DELETE" });
  loadSources();
};

// ── Filters ───────────────────────────────────────────
let filtersVisible = false;

filterToggle.addEventListener("click", () => {
  filtersVisible = !filtersVisible;
  filterRow.hidden = !filtersVisible;
  filterToggle.classList.toggle("active", filtersVisible);
});

function getActiveFilters() {
  const f = {};
  const ws  = document.getElementById("f-workstream").value;
  const dt  = document.getElementById("f-doc_type").value;
  const pri = document.getElementById("f-priority").value;
  const sen = document.getElementById("f-sentiment").value;
  const risk = document.getElementById("f-risk_flag").checked;
  const res  = document.getElementById("f-is_resolved").checked;
  if (ws)   f.workstream  = ws;
  if (dt)   f.doc_type    = dt;
  if (pri)  f.priority    = pri;
  if (sen)  f.sentiment   = sen;
  if (risk) f.risk_flag   = true;
  if (res)  f.is_resolved = true;
  const src = document.getElementById("f-source").value;
  if (src)  f.source = src;
  return f;
}

function updateFilterCount() {
  const count = Object.keys(getActiveFilters()).length;
  activeFilterCount.textContent = count;
  activeFilterCount.hidden = count === 0;
  filterToggle.classList.toggle("active", count > 0 || filtersVisible);
}

filterRow.addEventListener("change", updateFilterCount);

clearFiltersBtn.addEventListener("click", () => {
  document.getElementById("f-workstream").value = "";
  document.getElementById("f-doc_type").value = "";
  document.getElementById("f-priority").value = "";
  document.getElementById("f-sentiment").value = "";
  document.getElementById("f-risk_flag").checked = false;
  document.getElementById("f-is_resolved").checked = false;
  updateFilterCount();
});

// ── Export / Clear ───────────────────────────────────
function exportChat(fmt) {
  const msgs = document.querySelectorAll("#messages .msg");
  if (!msgs.length) { alert("No conversation to export."); return; }

  const lines = [];
  const ts = new Date().toISOString().slice(0, 16).replace("T", " ");
  lines.push(fmt === "md" ? `# Paramount RAG Export — ${ts}\n` : `Paramount RAG Export — ${ts}\n`);

  msgs.forEach(msg => {
    const isUser = msg.classList.contains("user");
    const bubble = msg.querySelector(".bubble");
    const text   = bubble ? bubble.textContent.trim() : "";
    const chips  = [...msg.querySelectorAll(".source-chip")].map(c => c.textContent.trim());

    if (fmt === "md") {
      lines.push(isUser ? `## Q: ${text}\n` : `### Answer\n${text}\n`);
      if (!isUser && chips.length) lines.push(`**Sources:** ${chips.join(" · ")}\n`);
    } else {
      lines.push(isUser ? `Q: ${text}\n` : `A: ${text}\n`);
      if (!isUser && chips.length) lines.push(`Sources: ${chips.join(" | ")}\n`);
    }
    lines.push("");
  });

  const blob = new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `rag-export-${ts.replace(/[: ]/g, "-")}.${fmt}`;
  a.click();
  URL.revokeObjectURL(url);
}

function clearChat() {
  if (!confirm("Clear the conversation?")) return;
  const el = document.getElementById("messages");
  el.innerHTML = '<div class="welcome"><h2>Ask anything about your documents</h2><p>Upload files or sync from M365, then ask questions here.</p></div>';
  _history.length = 0;
}

// ── Conversation history (last 3 turns for multi-turn context) ───────────
const _history = [];  // [{role, content}]
const _MAX_HISTORY = 3;

function _pushHistory(role, content) {
  _history.push({ role, content });
  // Keep last _MAX_HISTORY complete exchanges (each = 2 entries)
  while (_history.length > _MAX_HISTORY * 2) _history.shift();
}

// ── Chat ──────────────────────────────────────────────
function appendMessage(role, text = "") {
  document.querySelector(".welcome")?.remove();
  const msg = document.createElement("div");
  msg.className = `msg ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  msg.appendChild(bubble);
  if (role === "assistant") {
    bubble.classList.add("cursor");
    msg._bubble = bubble;
  }
  messagesEl.appendChild(msg);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return msg;
}

function renderSourceChips(msgEl, sources) {
  if (!sources.length) return;
  const bar = document.createElement("div");
  bar.className = "sources-bar";
  bar.innerHTML = sources.map(s => {
    const tags = [];
    if (s.workstream) tags.push(`<span class="s-tag ws">${s.workstream}</span>`);
    if (s.doc_type)   tags.push(`<span class="s-tag">${s.doc_type}</span>`);
    if (s.priority && (s.priority === "High" || s.priority === "Critical"))
      tags.push(`<span class="s-tag pri-${s.priority}">${s.priority}</span>`);
    if (s.risk_flag)  tags.push(`<span class="s-tag risk">⚠ Risk</span>`);
    const authorDate = [s.author, s.date].filter(Boolean).join(" · ");
    return `
      <div class="src-chip${s.risk_flag ? " has-risk" : ""}">
        <span class="s-name">${s.source}</span>
        <span class="s-score">${s.score}</span>
        ${authorDate ? `<span class="s-tag">${authorDate}</span>` : ""}
        ${tags.join("")}
      </div>`;
  }).join("");
  msgEl.insertBefore(bar, msgEl.firstChild);
}

async function ask(question) {
  appendMessage("user", question);
  sendBtn.disabled = true;
  input.disabled = true;

  const assistantMsg = appendMessage("assistant");
  const bubble = assistantMsg._bubble;
  let buffer = "";

  try {
    const r = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        n_results: 10,
        filters: getActiveFilters(),
        history: [..._history],  // send current history before this turn
      }),
    });
    if (!r.ok) throw new Error(await r.text());

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let partial = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      partial += decoder.decode(value, { stream: true });
      const lines = partial.split("\n\n");
      partial = lines.pop();
      for (const line of lines) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5);
        if (payload === "[DONE]") break;
        if (payload.startsWith("sources:")) {
          renderSourceChips(assistantMsg, JSON.parse(payload.slice(8)));
        } else if (payload.startsWith("t:")) {
          buffer += JSON.parse(payload.slice(2));
          bubble.textContent = buffer;
          messagesEl.scrollTop = messagesEl.scrollHeight;
        } else if (payload.startsWith("error:")) {
          bubble.textContent = `⚠ ${JSON.parse(payload.slice(6))}`;
          bubble.style.color = "var(--danger)";
        }
      }
    }
    // Record this exchange in history for next turn
    if (buffer) {
      _pushHistory("user", question);
      _pushHistory("assistant", buffer);
    }
  } catch (e) {
    bubble.textContent = `Error: ${e.message}`;
  } finally {
    bubble.classList.remove("cursor");
    sendBtn.disabled = false;
    input.disabled = false;
    input.focus();
  }
}

form.addEventListener("submit", e => {
  e.preventDefault();
  const q = input.value.trim();
  if (!q || sendBtn.disabled) return;
  input.value = "";
  ask(q);
});

input.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.dispatchEvent(new Event("submit")); }
});

// ── Email archive ingestion ───────────────────────────

async function ingestEmailArchive() {
  const btn = document.getElementById("email-archive-btn");
  const statusEl = document.getElementById("email-archive-status");
  const workstream = document.getElementById("archive-ws").value;

  btn.disabled = true;
  statusEl.className = "m365-status-msg info";
  statusEl.textContent = "Parsing and ingesting emails…";

  try {
    const r = await fetch("/api/ingest/email-json", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workstream }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error);

    statusEl.className = "m365-status-msg ok";
    statusEl.textContent =
      `✓ ${data.processed} emails → ${data.chunks_ingested} chunks  ` +
      `(skipped: ${data.skipped_folder} folder, ${data.skipped_sender} sender, ${data.skipped_empty} empty)`;
    loadSources();
  } catch (e) {
    statusEl.className = "m365-status-msg err";
    statusEl.textContent = `Failed: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

// Populate archive workstream select once schema loads
const _origLoadSchema = loadSchema;
// Patch: after schema loads, also populate archive-ws
async function loadSchema() {
  try {
    SCHEMA = await fetch("/api/schema").then(r => r.json());
    populateSelects();
    // Populate archive workstream dropdown
    const archiveWs = document.getElementById("archive-ws");
    if (archiveWs) {
      SCHEMA.workstreams.forEach(ws => archiveWs.add(new Option(ws, ws)));
    }
  } catch (_) {}
}

// ── Scripts panel ─────────────────────────────────────

async function runScript(scriptId) {
  const logEl = document.getElementById("scripts-log");
  const btnIds = ["btn-ingest-todo", "btn-sync-incremental", "btn-sync-full", "btn-sync-tactiq"];

  // Disable all buttons, show log
  btnIds.forEach(id => { const b = document.getElementById(id); if (b) b.disabled = true; });
  logEl.hidden = false;
  logEl.textContent = "";

  try {
    const resp = await fetch(`/api/scripts/${scriptId}`, { method: "POST" });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5);
        if (payload === "[DONE]") { loadSources(); break; }
        try {
          logEl.textContent += JSON.parse(payload) + "\n";
          logEl.scrollTop = logEl.scrollHeight;
        } catch (_) {}
      }
    }
  } catch (e) {
    logEl.textContent += `\nError: ${e.message}`;
  } finally {
    btnIds.forEach(id => { const b = document.getElementById(id); if (b) b.disabled = false; });
  }
}

// ── Init ──────────────────────────────────────────────
checkM365Status();
loadSchema();
loadSources();
