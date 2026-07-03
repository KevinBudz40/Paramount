"""
MSAL OAuth2 authentication for Microsoft Graph API.

App registration requirements (Azure AD):
  1. Register a new app at https://portal.azure.com → App registrations
  2. Platform: Web — Redirect URI: http://localhost:5000/auth/callback
  3. Supported account types: "Accounts in this organizational directory only"
     (Oracle's tenant — single-tenant is correct for corporate M365)
  4. Client secret: create one under Certificates & secrets
  5. API permissions (Delegated):
       Microsoft Graph → User.Read              (sign-in identity)
       Microsoft Graph → Mail.Read              (Exchange mailbox)
       Microsoft Graph → Calendars.Read         (Outlook Calendar)
       Microsoft Graph → Sites.Read.All         (SharePoint — REQUIRES admin consent)
       Microsoft Graph → Files.Read.All         (OneDrive/SharePoint files)
       Microsoft Graph → offline_access         (refresh tokens)

  SharePoint note: Sites.Read.All is a HIGH-PRIVILEGE scope. Oracle's IT/Azure
  admin must click "Grant admin consent for Oracle" in the API permissions blade
  before any user in the tenant can authenticate with this scope. If admin consent
  is unavailable, remove Sites.Read.All and use Files.Read.All only — SharePoint
  ingestion will still work for files the signed-in user can access via /me/drives,
  but site discovery (/sites?search=*) will be blocked.

Environment variables required:
  AZURE_CLIENT_ID     — Application (client) ID from app registration overview
  AZURE_CLIENT_SECRET — Client secret value (not the secret ID)
  AZURE_TENANT_ID     — Directory (tenant) ID, e.g. oracle's tenant GUID
  AZURE_REDIRECT_URI  — Optional; defaults to http://localhost:5000/auth/callback
"""
import os
import secrets
from pathlib import Path
from typing import Optional

import msal

# ---------------------------------------------------------------------------
# Scopes — all delegated (act as the signed-in user)
# ---------------------------------------------------------------------------
SCOPES = [
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Calendars.Read",
    # Sites.Read.All enables /sites?search=* discovery AND /sites/{id} lookup.
    # Remove this scope if Oracle IT hasn't granted admin consent; Files.Read.All
    # still allows SharePoint file access via /me/drives and /drives/{id}.
    "https://graph.microsoft.com/Sites.Read.All",
    "https://graph.microsoft.com/Files.Read.All",
    "offline_access",
]

# Fallback scope set when Sites.Read.All is blocked by admin policy
SCOPES_NO_SITES = [
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Calendars.Read",
    "https://graph.microsoft.com/Files.Read.All",
    "offline_access",
]

_CACHE_FILE = Path("token_cache.json")
_DEFAULT_REDIRECT = "http://localhost:5000/auth/callback"


# ---------------------------------------------------------------------------
# MSAL client helpers
# ---------------------------------------------------------------------------

def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if _CACHE_FILE.exists():
        cache.deserialize(_CACHE_FILE.read_text(encoding="utf-8"))
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        _CACHE_FILE.write_text(cache.serialize(), encoding="utf-8")


def _build_msal_app(cache: msal.SerializableTokenCache) -> msal.ConfidentialClientApplication:
    """Build a confidential-client MSAL app (client_id + client_secret)."""
    client_id = os.environ.get("AZURE_CLIENT_ID")
    client_secret = os.environ.get("AZURE_CLIENT_SECRET")
    tenant_id = os.environ.get("AZURE_TENANT_ID")

    if not all([client_id, client_secret, tenant_id]):
        raise RuntimeError(
            "Missing Azure credentials. Set AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, "
            "and AZURE_TENANT_ID in your .env file."
        )

    return msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        # Single-tenant authority: only Oracle M365 accounts can authenticate.
        # Use "https://login.microsoftonline.com/common" if you want to allow
        # any Microsoft account (not recommended for corporate deployments).
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=cache,
    )


# ---------------------------------------------------------------------------
# Public auth API
# ---------------------------------------------------------------------------

def get_auth_url(redirect_uri: str | None = None, state: str | None = None) -> str:
    """
    Return the Microsoft login URL to redirect the user to.
    `state` is echoed back in the callback for CSRF verification.
    """
    redirect_uri = redirect_uri or _DEFAULT_REDIRECT
    state = state or secrets.token_urlsafe(16)
    cache = _load_cache()
    app = _build_msal_app(cache)
    return app.get_authorization_request_url(
        scopes=SCOPES,
        redirect_uri=redirect_uri,
        state=state,
        # Force account picker so users aren't silently re-authenticated
        # with the wrong M365 identity.
        prompt="select_account",
    )


def exchange_code(code: str, redirect_uri: str | None = None) -> dict:
    """
    Exchange an authorization code for tokens.
    Persists the token cache to disk (includes refresh token for silent renewal).
    """
    redirect_uri = redirect_uri or _DEFAULT_REDIRECT
    cache = _load_cache()
    app = _build_msal_app(cache)
    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )
    _save_cache(cache)

    if "error" in result:
        raise ValueError(
            f"Token exchange failed: {result.get('error_description', result['error'])}"
        )
    return result


def get_token() -> str:
    """
    Return a valid access token for Microsoft Graph, refreshing silently if needed.
    Raises RuntimeError if no cached session exists.
    """
    cache = _load_cache()
    app = _build_msal_app(cache)
    accounts = app.get_accounts()
    if not accounts:
        raise RuntimeError("Not authenticated. Visit /auth/login first.")

    result = app.acquire_token_silent(scopes=SCOPES, account=accounts[0])
    _save_cache(cache)

    if result and "access_token" in result:
        return result["access_token"]

    # Silent renewal can fail if the refresh token is expired (>90 days) or
    # if admin revoked consent. Surface as a clear error so the UI can prompt
    # re-login rather than silently returning empty results.
    error = result.get("error_description") if result else "No token result"
    raise RuntimeError(f"Silent token renewal failed: {error}. Re-authenticate at /auth/login.")


def get_account() -> Optional[dict]:
    """Return the cached MSAL account dict, or None if not authenticated."""
    cache = _load_cache()
    try:
        app = _build_msal_app(cache)
        accounts = app.get_accounts()
        return accounts[0] if accounts else None
    except RuntimeError:
        return None


def logout() -> None:
    """Delete the local token cache. Does NOT revoke the token server-side."""
    if _CACHE_FILE.exists():
        _CACHE_FILE.unlink()


def is_authenticated() -> bool:
    return get_account() is not None
