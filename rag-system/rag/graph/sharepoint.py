"""
Ingest SharePoint Online documents via Microsoft Graph API.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHY SHAREPOINT AUTH IS COMPLEX
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SharePoint sits at the intersection of three permission systems:

1. Microsoft Graph scopes (what this module uses)
   ─ Sites.Read.All  — read any site's metadata and enumerate document
                       libraries. HIGH-PRIVILEGE: requires Azure AD admin
                       consent. Without it, /sites?search=* is blocked.
   ─ Files.Read.All  — read all files the signed-in user can access,
                       regardless of which site they live on. Often allowed
                       without admin consent. Enables /me/drives and
                       /drives/{id}/… paths, but NOT /sites/{id} enumeration.
   ─ Sites.Selected  — most locked-down option: an Azure/SharePoint admin
                       runs a one-time Graph API call (or PowerShell) to
                       grant the app access to specific site IDs only. Useful
                       for production workloads where you don't want Sites.Read.All.

2. SharePoint permission levels (classic SharePoint model)
   ─ The Graph API enforces these on top of AAD scopes. A token with
     Sites.Read.All still cannot read a site the user has no SharePoint
     permission to. Delegated tokens act as the signed-in user — so the
     user must have at least "Read" on the site/library.

3. Conditional Access / DLP policies
   ─ Oracle may have tenant policies that block token issuance for high-
     privilege scopes from unmanaged devices. If authentication succeeds
     but calls to /sites fail with 403, check the tenant's CA policies.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GRAPH API SURFACE USED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Site resolution:
  GET /sites/{hostname}:{server-relative-path}
      e.g. /sites/oracle.sharepoint.com:/sites/ProjectAlpha
      Returns site metadata including the site's stable ID.

Drive (document library) enumeration:
  GET /sites/{site-id}/drives
      A SharePoint "drive" == a document library (Documents, Shared Documents…).
      System libraries (Site Assets, Site Pages, Form Templates) are filtered out.

File tree traversal:
  GET /drives/{drive-id}/items/{item-id}/children
      Recursive — folders are descended, files are yielded if they match
      the supported extension list and are under the size cap.
      Pagination via @odata.nextLink is handled by client.get_paginated().

File download:
  GET /drives/{drive-id}/items/{item-id}/content
      Returns raw bytes. Content is written to a temp file, then passed
      to rag.chunker.chunk_document() for text extraction.

Site search (requires Sites.Read.All):
  GET /sites?search={query}
      Returns sites matching the search term. Used for the discovery UI.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SETUP FOR ORACLE M365
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Option A — Admin grants Sites.Read.All consent (recommended):
  1. In Azure portal → App registrations → your app → API permissions
  2. Add: Microsoft Graph → Delegated → Sites.Read.All
  3. Click "Grant admin consent for Oracle" (requires Global/Cloud App Admin)
  4. Users can then authenticate and ingest any SharePoint site they
     have SharePoint-level read access to.

Option B — Sites.Selected (no broad site read, most secure):
  1. Register the app WITHOUT Sites.Read.All
  2. A SharePoint Online admin runs one of:
       a. PowerShell:
            Grant-PnPAzureADAppSitePermission \
              -AppId <AZURE_CLIENT_ID> \
              -DisplayName "RAG System" \
              -Site https://oracle.sharepoint.com/sites/ProjectAlpha \
              -Permissions Read
       b. Graph API call (as SharePoint admin):
            POST /sites/{site-id}/permissions
            Body: { "roles": ["read"], "grantedToIdentities": [{
                     "application": { "id": "<CLIENT_ID>", "displayName": "RAG" }
                   }] }
  3. The app can then access those specific sites. Site discovery
     (search) is unavailable — users must type site URLs directly.

Option C — Files.Read.All only (no admin consent needed in many tenants):
  Remove Sites.Read.All from SCOPES in auth.py.
  SharePoint files still accessible via /me/drives (personal drive) and
  following shared links. /sites/{id} enumeration will return 403;
  remove the site_url ingestion path and use _ingest_via_me_drives() instead.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import tempfile
from pathlib import Path
from typing import Iterator

from rag.chunker import chunk_document
from rag.graph.client import get, get_bytes, get_paginated

# Extensions this system can extract text from
_INGESTIBLE = {".pdf", ".docx", ".doc", ".txt", ".md", ".csv", ".xlsx", ".xls", ".pptx"}

# System document libraries to skip — these contain SharePoint scaffolding,
# not user documents, and bloat the index with noise.
_SKIP_DRIVE_NAMES = {
    "Site Assets", "Site Pages", "Style Library",
    "Form Templates", "Preservation Hold Library",
    "_catalogs",
}

# Skip files larger than 20 MB — content extraction becomes slow and the
# chunks are rarely useful for Q&A without additional pre-processing.
_MAX_FILE_BYTES = 20 * 1024 * 1024


# ── Site / drive resolution ────────────────────────────────────────────────

def resolve_site(site_url: str) -> dict:
    """
    Resolve a SharePoint site by URL fragment or full Graph path.

    Accepted formats:
      "oracle.sharepoint.com:/sites/ProjectAlpha"   → hostname:server-relative-path
      "oracle.sharepoint.com:/teams/OpsTeam"        → team site
      "root"                                         → tenant root site

    Returns the Graph site object (id, displayName, webUrl, …).
    Raises requests.HTTPError 403 if Sites.Read.All is not consented.
    """
    return get(f"/sites/{site_url}")


def list_drives(site_id: str) -> list[dict]:
    """
    Return document libraries in a site, filtering out SharePoint system libraries.
    Each drive dict has: id, name, driveType, webUrl, quota.
    """
    drives = get_paginated(f"/sites/{site_id}/drives")
    return [d for d in drives if d.get("name") not in _SKIP_DRIVE_NAMES]


def search_sites(query: str = "*") -> list[dict]:
    """
    Search SharePoint sites visible to the user.
    Requires Sites.Read.All — raises 403 if scope not consented.
    """
    data = get("/sites", params={"search": query})
    return [
        {
            "id":   s["id"],
            "name": s.get("displayName", ""),
            "url":  s.get("webUrl", ""),
        }
        for s in data.get("value", [])
    ]


# ── File tree traversal ────────────────────────────────────────────────────

_DRIVE_ITEM_SELECT = ",".join([
    "id", "name", "file", "folder", "size",
    "lastModifiedDateTime", "createdBy", "webUrl",
    "parentReference",
])


def _iter_drive_files(drive_id: str, item_id: str = "root") -> Iterator[dict]:
    """
    Depth-first recursive walk of a drive folder, yielding only ingestible files.
    Pagination at every level is handled by get_paginated().
    """
    items = get_paginated(
        f"/drives/{drive_id}/items/{item_id}/children",
        params={"$select": _DRIVE_ITEM_SELECT, "$top": 200},
    )
    for item in items:
        if "folder" in item:
            yield from _iter_drive_files(drive_id, item["id"])
        elif "file" in item:
            ext = Path(item.get("name", "")).suffix.lower()
            size = item.get("size", 0)
            if ext in _INGESTIBLE and size <= _MAX_FILE_BYTES and size > 0:
                item["_drive_id"] = drive_id
                yield item


# ── Content extraction ─────────────────────────────────────────────────────

def _extract_chunks(content: bytes, item: dict, site_name: str, drive_name: str) -> list[dict]:
    """
    Write file bytes to a temp file, run chunk_document(), attach Paramount
    metadata, then delete the temp file.
    """
    name = item["name"]
    suffix = Path(name).suffix.lower()
    date = (item.get("lastModifiedDateTime") or "")[:10]
    author_obj = (item.get("createdBy") or {}).get("user") or {}
    author = author_obj.get("displayName") or author_obj.get("email") or ""
    source = f"sharepoint:{site_name}/{drive_name}/{name}"

    doc_meta = {
        "doc_id":   item["id"],
        "doc_type": "Report",
        "source":   source,
        "date":     date,
        "author":   author,
        "topic":    name,
    }

    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(content)
        tmp.close()
        raw_chunks = chunk_document(Path(tmp.name))
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    return [
        {
            "text":        c["text"],
            "chunk_index": c["chunk_index"],
            "source":      source,
            "doc_meta":    doc_meta,
        }
        for c in raw_chunks
    ]


# ── Public ingestion API ───────────────────────────────────────────────────

def ingest_site(
    site_url: str,
    drive_names: list[str] | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Ingest all supported documents from a SharePoint site.

    Args:
        site_url:    SharePoint site URL fragment, e.g.
                     "oracle.sharepoint.com:/sites/ProjectAlpha"
        drive_names: Whitelist of document library names (None = all).

    Returns:
        (chunks, errors) — chunks ready for add_chunks(); errors = skipped files.

    Raises:
        requests.HTTPError 403: Sites.Read.All not consented or user lacks
                                SharePoint access to the site.
        requests.HTTPError 404: Site URL not found or mis-spelled.
    """
    site = resolve_site(site_url)
    site_id = site["id"]
    site_name = site.get("displayName") or site_url

    drives = list_drives(site_id)
    if drive_names:
        drives = [d for d in drives if d.get("name") in drive_names]

    all_chunks: list[dict] = []
    errors: list[str] = []

    for drive in drives:
        drive_id = drive["id"]
        drive_name = drive.get("name", "Documents")

        for item in _iter_drive_files(drive_id):
            try:
                content = get_bytes(f"/drives/{drive_id}/items/{item['id']}/content")
                chunks = _extract_chunks(content, item, site_name, drive_name)
                all_chunks.extend(chunks)
            except Exception as exc:
                errors.append(f"{item.get('name', item['id'])}: {exc}")

    return all_chunks, errors


def ingest_my_drives() -> tuple[list[dict], list[str]]:
    """
    Fallback ingestion path that does NOT require Sites.Read.All.

    Accesses SharePoint files via /me/drives, which returns drives the signed-in
    user has direct access to (including SharePoint document libraries synced to
    their OneDrive). Useful when admin consent for Sites.Read.All is unavailable.
    """
    drives = get_paginated("/me/drives")
    all_chunks: list[dict] = []
    errors: list[str] = []

    for drive in drives:
        drive_id = drive["id"]
        drive_name = drive.get("name", drive_id)
        site_name = drive.get("description") or drive_name

        for item in _iter_drive_files(drive_id):
            try:
                content = get_bytes(f"/drives/{drive_id}/items/{item['id']}/content")
                chunks = _extract_chunks(content, item, site_name, drive_name)
                all_chunks.extend(chunks)
            except Exception as exc:
                errors.append(f"{item.get('name', item['id'])}: {exc}")

    return all_chunks, errors
