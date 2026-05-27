"""
Crossuite API client -- read-only.
Credentials via environment variables only. No secrets printed or logged.
"""

from __future__ import annotations

import os
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth

USER_AGENT = "Client-ReviewExport/1.0"
TIMEOUT = 20
CHUNK_SIZE = 50  # max patient_ids per /patients request


class CrossuiteError(Exception):
    pass


class CrossuiteAuthError(CrossuiteError):
    pass


class CrossuiteClient:
    def __init__(
        self,
        auth_url: str,
        api_url: str,
        client_id: str,
        client_secret: str,
        username: str,
        password: str,
        active_alias_id: Optional[str] = None,
    ):
        self._auth_url = auth_url.rstrip("/")
        self._api_url = api_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._username = username
        self._password = password
        self._active_alias_id = active_alias_id
        self._session = requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT
        self._session.headers["Accept-Language"] = "nl-BE"

    # -- Auth -----------------------------------------------------------------

    def get_token(self) -> str:
        """Fetch OAuth2 password-grant token. Stores it on the session."""
        try:
            resp = requests.post(
                f"{self._auth_url}/token",
                data={
                    "grant_type": "password",
                    "username": self._username,
                    "password": self._password,
                },
                auth=HTTPBasicAuth(self._client_id, self._client_secret),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": USER_AGENT,
                },
                timeout=TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:
            raise CrossuiteError(f"Auth connection failed: {exc}") from exc

        if resp.status_code in (401, 403):
            raise CrossuiteAuthError(f"Auth HTTP {resp.status_code}: invalid credentials")
        if not resp.ok:
            raise CrossuiteError(f"Auth HTTP {resp.status_code}")

        body = resp.json()
        access_token = body.get("access_token")
        if not access_token:
            raise CrossuiteError("No access_token in auth response")

        token_type = body.get("token_type", "Bearer")
        auth_header = f"{token_type} {access_token}"
        self._session.headers["Authorization"] = auth_header
        return auth_header

    # -- Bootstrap ------------------------------------------------------------

    def get_clients_info(self) -> dict:
        """GET /clients/info -- no X-Active-Alias header, only Authorization."""
        resp = self._get("/clients/info", alias_id=None)
        return resp.json()

    def resolve_active_alias_id(self) -> str:
        """
        Return active alias ID from env override, or discover via /clients/info.
        Caches result on self._active_alias_id.
        """
        if self._active_alias_id:
            return self._active_alias_id

        data = self.get_clients_info()
        alias_id = _extract_active_alias_id(data)
        if not alias_id:
            raise CrossuiteError(
                "Cannot determine active alias ID from /clients/info. "
                "Set CROSSUITE_ACTIVE_ALIAS_ID manually."
            )
        self._active_alias_id = alias_id
        return alias_id

    # -- Data endpoints -------------------------------------------------------

    def get_client_aliases(self) -> list:
        """GET /client-aliases. Returns list of alias dicts."""
        alias_id = self._require_alias()
        resp = self._get("/client-aliases", alias_id=alias_id)
        data = resp.json()
        return _extract_list(data, "client_aliases", "clientAliases", "aliases", "clients", "data")

    def get_events(
        self,
        date_from: str,
        date_to: str,
        colleague_ids: Optional[list] = None,
    ) -> list:
        """
        GET /diary/events for APPOINTMENT events in [date_from, date_to].
        colleague_ids: list of alias IDs (None = use active alias only).
        Uses repeated 'colleague_aliases' params as required by the Crossuite API.
        """
        alias_id = self._require_alias()
        ids = colleague_ids if colleague_ids else [alias_id]

        params = [
            ("date_from", date_from),
            ("date_to", date_to),
            ("allocation_type", "COLLEAGUE"),
            ("event_types", "APPOINTMENT"),
            ("limit", 500),
            ("offset", 0),
            ("order_by", "event.event_date"),
            ("direction", "asc"),
            ("history", "false"),
        ]
        for cid in ids:
            params.append(("colleague_aliases", cid))

        resp = self._get("/diary/events", params=params, alias_id=alias_id)
        data = resp.json()
        return _extract_list(data, "events", "diary_events", "data", "items")

    def get_patients(self, patient_ids: list) -> list:
        """GET /patients in chunks of CHUNK_SIZE. Returns flat list of patient dicts."""
        alias_id = self._require_alias()
        results = []
        for i in range(0, len(patient_ids), CHUNK_SIZE):
            chunk = patient_ids[i : i + CHUNK_SIZE]
            params = [("patient_ids", pid) for pid in chunk]
            resp = self._get("/patients", params=params, alias_id=alias_id)
            data = resp.json()
            results.extend(_extract_list(data, "patients", "data", "items"))
        return results

    # -- Internal helpers -----------------------------------------------------

    def _require_alias(self) -> str:
        if not self._active_alias_id:
            raise CrossuiteError(
                "active_alias_id not set -- call resolve_active_alias_id() first"
            )
        return self._active_alias_id

    def _get(
        self, path: str, params=None, alias_id: Optional[str] = None
    ) -> requests.Response:
        url = f"{self._api_url}{path}"
        extra_headers = {}
        if alias_id:
            extra_headers["x-active-alias"] = alias_id
        try:
            resp = self._session.get(
                url, params=params, headers=extra_headers, timeout=TIMEOUT
            )
        except requests.exceptions.RequestException as exc:
            raise CrossuiteError(f"Request failed for {path}: {exc}") from exc

        if resp.status_code in (401, 403):
            raise CrossuiteAuthError(f"HTTP {resp.status_code} on {path}")
        if not resp.ok:
            _raise_api_error(resp, path)
        return resp


# -- Module-level helpers (also used by mapper / preview) ---------------------


def _extract_active_alias_id(data: dict) -> Optional[str]:
    """Extract active_client_alias_id from /clients/info response."""
    if not isinstance(data, dict):
        return None
    inner = data.get("data") or {}
    if not isinstance(inner, dict):
        inner = {}

    for src in (inner, data):
        if not isinstance(src, dict):
            continue
        settings = src.get("settings") or {}
        if isinstance(settings, dict) and settings.get("active_client_alias_id"):
            return str(settings["active_client_alias_id"])
        for key in (
            "active_client_alias_id",
            "client_alias_id",
            "clientAliasId",
            "activeClientAliasId",
        ):
            if src.get(key):
                return str(src[key])

    # Fall back: single-item aliases list
    for src in (inner, data):
        if not isinstance(src, dict):
            continue
        for list_key in ("client_aliases", "clientAliases", "aliases"):
            items = src.get(list_key) or []
            if isinstance(items, list) and len(items) == 1:
                a = items[0]
                if isinstance(a, dict):
                    for k in ("client_alias_id", "clientAliasId", "alias_id", "aliasId", "id"):
                        if a.get(k):
                            return str(a[k])
    return None


def _extract_list(data, *keys) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            if k in data and isinstance(data[k], list):
                return data[k]
    return []


def _raise_api_error(resp: requests.Response, path: str) -> None:
    msg = f"HTTP {resp.status_code} on {path}"
    try:
        err = resp.json()
        if err.get("description"):
            msg += f" -- {err['description']}"
        elif err.get("code"):
            msg += f" -- {err['code']}"
    except Exception:
        pass
    raise CrossuiteError(msg)


# -- Factory ------------------------------------------------------------------


def from_env() -> CrossuiteClient:
    """Create CrossuiteClient from environment variables."""
    required = [
        "CROSSUITE_AUTH_URL",
        "CROSSUITE_API_URL",
        "CROSSUITE_CLIENT_ID",
        "CROSSUITE_CLIENT_SECRET",
        "CROSSUITE_USERNAME",
        "CROSSUITE_PASSWORD",
    ]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        raise CrossuiteError(f"Missing environment variables: {missing}")

    return CrossuiteClient(
        auth_url=os.environ["CROSSUITE_AUTH_URL"],
        api_url=os.environ["CROSSUITE_API_URL"],
        client_id=os.environ["CROSSUITE_CLIENT_ID"],
        client_secret=os.environ["CROSSUITE_CLIENT_SECRET"],
        username=os.environ["CROSSUITE_USERNAME"],
        password=os.environ["CROSSUITE_PASSWORD"],
        active_alias_id=os.getenv("CROSSUITE_ACTIVE_ALIAS_ID", "").strip() or None,
    )
