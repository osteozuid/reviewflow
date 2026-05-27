#!/usr/bin/env python3
"""
READ-ONLY Crossuite POC. Do not use for production.
Does not send emails. Does not write to Crossuite.
First run should be local only.
Do not commit before reviewing masked output.
"""

import argparse
import os
import sys
from dotenv import load_dotenv
import requests
from requests.auth import HTTPBasicAuth

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIMEOUT = 20
DATE_FROM = "2026-05-18"
DATE_TO   = "2026-05-24"

EXPECTED_EVENTS        = 73
EXPECTED_WITH_EMAIL    = 71
EXPECTED_UNIQUE_EMAILS = 68

REQUIRED_VARS = [
    "CROSSUITE_CLIENT_ID",
    "CROSSUITE_CLIENT_SECRET",
    "CROSSUITE_USERNAME",
    "CROSSUITE_PASSWORD",
]

# Set after load_dotenv() in main()
API_URL  = ""
SESSION  = requests.Session()

# ---------------------------------------------------------------------------
# Safety helpers
# ---------------------------------------------------------------------------

def mask_email(email: str) -> str:
    """j*** -> j***@d***.be"""
    if not email or "@" not in email:
        return "(geen)"
    local, domain = email.split("@", 1)
    parts = domain.rsplit(".", 1)
    m_local  = (local[0]   + "***") if local   else "***"
    m_domain = (parts[0][0] + "***" + "." + parts[1]) if len(parts) == 2 else "***"
    return f"{m_local}@{m_domain}"


def mask_name(forename: str = "", surname: str = "") -> str:
    """Jan Peeters -> J*** P***"""
    def _m(s: str) -> str:
        s = (s or "").strip()
        return (s[0] + "***") if s else "***"
    return f"{_m(forename)} {_m(surname)}"


def mask_id(id_val) -> str:
    """51195085 -> 5119****"""
    s = str(id_val) if id_val else "?"
    return (s[:4] + "****") if len(s) > 4 else s


def safe_keys(obj) -> list:
    """Return only the key names of a dict, never its values."""
    return list(obj.keys()) if isinstance(obj, dict) else []


def extract_list(data, *keys) -> list:
    """Accept {"key": [...]} or direct list; tries keys in order."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            if k in data and isinstance(data[k], list):
                return data[k]
    return []


# ---------------------------------------------------------------------------
# HTTP wrapper
# ---------------------------------------------------------------------------

def _headers(alias_id: str = None) -> dict:
    h = {"Accept-Language": "nl-BE"}
    if alias_id:
        h["x-active-alias"] = alias_id
    return h


def request_get(path: str, params=None, alias_id: str = None) -> requests.Response:
    url = f"{API_URL}{path}"
    try:
        resp = SESSION.get(url, params=params, headers=_headers(alias_id), timeout=TIMEOUT)
    except requests.exceptions.RequestException as exc:
        print(f"  [ERROR] Verbinding mislukt: {exc}")
        sys.exit(1)

    if resp.status_code in (401, 403):
        print(f"  [ERROR] HTTP {resp.status_code} -- auth, alias of rechten mogelijk onjuist.")
        try:
            print(f"  Error keys: {safe_keys(resp.json())}")
        except Exception:
            pass
        sys.exit(1)

    if not resp.ok:
        print(f"  [ERROR] HTTP {resp.status_code}")
        try:
            err = resp.json()
            print(f"  Error keys: {safe_keys(err)}")
            if err.get("description"):
                print(f"  description: {err['description']}")
            if err.get("code"):
                print(f"  code: {err['code']}")
        except Exception:
            pass
        sys.exit(1)

    return resp


# ---------------------------------------------------------------------------
# Step 1 -- Validate env
# ---------------------------------------------------------------------------

def step_validate_env() -> str:
    print("\n[1] Omgevingsvariabelen controleren...")
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        print(f"  [FOUT] Ontbrekende variabelen: {missing}")
        sys.exit(1)
    print("  Alle verplichte variabelen aanwezig.")

    alias_id = os.getenv("CROSSUITE_ACTIVE_ALIAS_ID", "").strip() or None
    if alias_id:
        print("  CROSSUITE_ACTIVE_ALIAS_ID: aanwezig (uit .env, wordt als override gebruikt).")
    else:
        print("  CROSSUITE_ACTIVE_ALIAS_ID: niet ingesteld -- wordt bepaald via /clients/info.")
    return alias_id


# ---------------------------------------------------------------------------
# Step 2 -- Obtain token
# ---------------------------------------------------------------------------

def step_get_token(auth_url: str, client_id: str, client_secret: str,
                   username: str, password: str) -> str:
    print("\n[2] Access token ophalen...")
    try:
        resp = requests.post(
            f"{auth_url}/token",
            data={"grant_type": "password", "username": username, "password": password},
            auth=HTTPBasicAuth(client_id, client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        print(f"  [ERROR] Auth-verbinding mislukt: {exc}")
        sys.exit(1)

    if resp.status_code in (401, 403):
        print(f"  [ERROR] HTTP {resp.status_code} -- client_id/secret of username/password onjuist.")
        sys.exit(1)
    if not resp.ok:
        print(f"  [ERROR] HTTP {resp.status_code}")
        sys.exit(1)

    body = resp.json()
    access_token = body.get("access_token")
    if not access_token:
        print(f"  [ERROR] Geen access_token in response. Keys: {safe_keys(body)}")
        sys.exit(1)

    print("  Token ontvangen: ja")
    if body.get("expires_in"):
        print(f"  expires_in: {body['expires_in']}s")

    token_type = body.get("token_type", "Bearer")
    return f"{token_type} {access_token}"


# ---------------------------------------------------------------------------
# Step 3 -- /clients/info  (bootstrap: alias-ID ophalen zonder X-Active-Alias)
# ---------------------------------------------------------------------------

def extract_active_alias_id(data: dict) -> tuple:
    """
    Zoek client_alias_id in /clients/info response.
    Retourneert (alias_id_or_None, candidates_list).
    """
    if not isinstance(data, dict):
        return None, []

    inner = data.get("data") or {}
    if not isinstance(inner, dict):
        inner = {}

    # Prioriteit 1-6: directe velden
    for src in (inner, data):
        for key in (
            "active_client_alias_id",
            "client_alias_id", "clientAliasId",
            "activeClientAliasId",
        ):
            val = src.get(key) or (src.get("settings") or {}).get(key) if isinstance(src, dict) else None
            if val:
                return str(val), [str(val)]

    # Prioriteit 7-9: lijsten
    candidates = []
    for src in (inner, data):
        if not isinstance(src, dict):
            continue
        for list_key in ("client_aliases", "clientAliases", "aliases"):
            items = src.get(list_key) or []
            if not isinstance(items, list):
                continue
            for a in items:
                if not isinstance(a, dict):
                    continue
                aid = (
                    a.get("client_alias_id") or a.get("clientAliasId")
                    or a.get("alias_id") or a.get("aliasId")
                    or a.get("id")
                )
                if aid and str(aid) not in candidates:
                    candidates.append(str(aid))

    if len(candidates) == 1:
        return candidates[0], candidates
    return None, candidates


def step_get_clients_info() -> str:
    """
    GET /clients/info -- alleen Authorization header, geen X-Active-Alias.
    Retourneert active alias ID (of sys.exit bij fout/ambiguïteit).
    """
    print("\n[3] GET /clients/info (bootstrap, geen X-Active-Alias)...")
    try:
        resp = SESSION.get(f"{API_URL}/clients/info", timeout=TIMEOUT)
    except requests.exceptions.RequestException as exc:
        print(f"  [ERROR] Verbinding mislukt: {exc}")
        sys.exit(1)

    if resp.status_code in (401, 403):
        print(f"  [ERROR] HTTP {resp.status_code} -- token ongeldig of verlopen.")
        sys.exit(1)
    if not resp.ok:
        print(f"  [ERROR] HTTP {resp.status_code}")
        try:
            err = resp.json()
            print(f"  Error keys: {safe_keys(err)}")
            if err.get("description"):
                print(f"  description: {err['description']}")
            if err.get("code"):
                print(f"  code: {err['code']}")
        except Exception:
            pass
        sys.exit(1)

    print(f"  Status: {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        print("  [ERROR] Response niet JSON.")
        sys.exit(1)

    print(f"  Top-level keys: {safe_keys(data)}")

    # settings.active_client_alias_id aanwezig?
    inner = data.get("data") or {}
    settings = (inner.get("settings") if isinstance(inner, dict) else None) or data.get("settings") or {}
    has_active_setting = bool(settings.get("active_client_alias_id")) if isinstance(settings, dict) else False
    print(f"  settings.active_client_alias_id aanwezig: {'ja' if has_active_setting else 'nee'}")

    alias_id, candidates = extract_active_alias_id(data)
    print(f"  Alias candidates: {candidates}")

    if alias_id:
        print(f"  Active alias: {alias_id}  (uit /clients/info)")
        return alias_id

    if len(candidates) > 1:
        print(f"  [WARN] Meerdere candidates -- stel CROSSUITE_ACTIVE_ALIAS_ID in .env in op één van: {candidates}")
        sys.exit(1)

    print("  [FOUT] Geen alias ID gevonden in /clients/info response.")
    print("  Stel CROSSUITE_ACTIVE_ALIAS_ID handmatig in .env in.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Step 4 -- /client-aliases
# ---------------------------------------------------------------------------

def step_get_client_aliases(alias_id: str) -> dict:
    print("\n[4] GET /client-aliases...")
    resp = request_get("/client-aliases", alias_id=alias_id)
    try:
        data = resp.json()
    except Exception:
        print("  (Response niet JSON)")
        return {}

    aliases = extract_list(data, "client_aliases", "clientAliases", "aliases", "clients", "data")
    print(f"  Aantal aliases: {len(aliases)}")
    if aliases:
        print(f"  Eerste alias keys: {safe_keys(aliases[0]) if isinstance(aliases[0], dict) else '?'}")

    alias_map: dict[str, str] = {}
    for a in aliases:
        if not isinstance(a, dict):
            continue
        aid = (
            a.get("client_alias_id") or a.get("clientAliasId")
            or a.get("alias_id") or a.get("aliasId")
            or a.get("id")
        )
        fn  = a.get("forename")  or a.get("firstName")  or a.get("first_name") or ""
        sn  = a.get("surname")   or a.get("lastName")   or a.get("last_name")  or a.get("name") or ""
        if aid:
            label = mask_name(fn, sn) if (fn or sn) else f"alias-{aid}"
            alias_map[str(aid)] = label

    print(f"  Alias-map (gemaskeerd): {alias_map}")
    return alias_map


# ---------------------------------------------------------------------------
# Step 5 -- /diary/events
# ---------------------------------------------------------------------------

def step_get_events(alias_id: str, colleague_ids: list = None, debug: bool = False) -> list:
    ids = colleague_ids or [alias_id]
    print(f"\n[5] GET /diary/events ({DATE_FROM} -> {DATE_TO}), colleague filter: {ids}...")
    params = [
        ("date_from",       DATE_FROM),
        ("date_to",         DATE_TO),
        ("allocation_type", "COLLEAGUE"),
        ("event_types",     "APPOINTMENT"),
        ("limit",           500),
        ("offset",          0),
        ("order_by",        "event.event_date"),
        ("direction",       "asc"),
        ("history",         "false"),
    ]
    for cid in ids:
        params.append(("colleague_aliases", cid))

    if debug:
        import urllib.parse
        print(f"  DEBUG query: {urllib.parse.urlencode(params, doseq=True)}")
    resp = request_get("/diary/events", params=params, alias_id=alias_id)
    try:
        data = resp.json()
    except Exception:
        print("  (Response niet JSON)")
        return []

    events = extract_list(data, "events", "data")
    print(f"  Events ontvangen: {len(events)}")
    if events and isinstance(events[0], dict):
        print(f"  Eerste event keys: {safe_keys(events[0])}")
    return events


# ---------------------------------------------------------------------------
# Step 6 -- Verwerk events
# ---------------------------------------------------------------------------

def step_process_events(events: list) -> tuple[set, set]:
    print("\n[6] Events verwerken...")
    patient_ids       = set()
    colleague_aliases = set()
    author_aliases    = set()

    deleted_true  = 0
    deleted_false = 0
    deleted_miss  = 0
    event_types   = {}
    alloc_types   = {}
    with_pts      = 0
    without_pts   = 0
    candidates    = 0  # mailbare afspraken

    for ev in events:
        if not isinstance(ev, dict):
            continue

        # deleted verdeling
        d = ev.get("deleted")
        if d is True:
            deleted_true += 1
        elif d is False:
            deleted_false += 1
        else:
            deleted_miss += 1

        # event_type verdeling
        et = ev.get("event_type") or ev.get("eventType") or "onbekend"
        event_types[et] = event_types.get(et, 0) + 1

        # allocation_type verdeling
        at = ev.get("allocation_type") or ev.get("allocationType") or "onbekend"
        alloc_types[at] = alloc_types.get(at, 0) + 1

        # patients
        pts = ev.get("patients") or []
        if pts:
            with_pts += 1
            for p in pts:
                pid = None
                if isinstance(p, dict):
                    pid = p.get("patientId") or p.get("id") or p.get("patient_id")
                elif isinstance(p, (str, int)):
                    pid = str(p)
                if pid:
                    patient_ids.add(str(pid))
        else:
            without_pts += 1

        # candidate: APPOINTMENT + niet deleted + patients aanwezig
        is_appt    = (et == "APPOINTMENT")
        not_del    = (d is not True)
        has_pts    = bool(pts)
        if is_appt and not_del and has_pts:
            candidates += 1

        # colleague_alias_ids
        cal_list = ev.get("colleague_alias_ids") or ev.get("colleagueAliasIds") or []
        if isinstance(cal_list, list) and cal_list:
            for cal in cal_list:
                if cal:
                    colleague_aliases.add(str(cal))
        else:
            cal_single = ev.get("colleagueAliasId") or ev.get("colleague_alias_id")
            if cal_single:
                colleague_aliases.add(str(cal_single))

        # author_client_alias_id
        aal = ev.get("author_client_alias_id") or ev.get("authorClientAliasId")
        if aal:
            author_aliases.add(str(aal))

    total = deleted_true + deleted_false + deleted_miss
    print(f"  Totaal events:                {total}")
    print(f"  deleted=true:                 {deleted_true}")
    print(f"  deleted=false:                {deleted_false}")
    print(f"  deleted veld afwezig:         {deleted_miss}")
    print(f"  event_type verdeling:         {event_types}")
    print(f"  allocation_type verdeling:    {alloc_types}")
    print(f"  Met patients[]:               {with_pts}")
    print(f"  Zonder patients[]:            {without_pts}")
    print(f"  Unieke patient_ids:           {len(patient_ids)}")
    print(f"  Colleague alias IDs:          {colleague_aliases}")
    print(f"  Author alias IDs:             {author_aliases}")
    print(f"\n  candidate_events_for_review_request: {candidates}")
    print(f"  (APPOINTMENT + niet deleted + patients[] aanwezig)")

    EXPECT_EVENTS  = 73
    EXPECT_PATIENTS = 68
    tol = 5
    print(f"\n  --- Vergelijking verwachte Excel-export ---")
    s1 = "PASS" if abs(candidates - EXPECT_EVENTS) <= tol else "WARN"
    s2 = "PASS" if abs(len(patient_ids) - EXPECT_PATIENTS) <= tol else "WARN"
    print(f"  {s1}  Candidate afspraken: {candidates}  (verwacht ~{EXPECT_EVENTS})")
    print(f"  {s2}  Unieke patient_ids:  {len(patient_ids)}  (verwacht ~{EXPECT_PATIENTS})")

    return patient_ids, colleague_aliases | author_aliases


# ---------------------------------------------------------------------------
# Step 7 -- /patients
# ---------------------------------------------------------------------------

def _chunk(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def step_get_patients(patient_ids: set, alias_id: str) -> dict:
    print(f"\n[7] GET /patients ({len(patient_ids)} IDs)...")
    if not patient_ids:
        print("  Geen patient_ids -- stap overgeslagen.")
        return {}

    patient_map: dict[str, str | None] = {}  # patient_id -> email or None

    for chunk in _chunk(sorted(patient_ids), 50):
        params = [("patient_ids", pid) for pid in chunk]
        params += [("limit", 100), ("offset", 0)]
        try:
            resp = SESSION.get(
                f"{API_URL}/patients",
                params=params,
                headers=_headers(alias_id),
                timeout=TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:
            print(f"  [ERROR] Verbinding mislukt: {exc}")
            break

        if resp.status_code in (401, 403):
            print(f"  [ERROR] HTTP {resp.status_code} bij /patients -- auth/alias/rechten.")
            break
        if resp.status_code == 404:
            print("  [WARN] /patients geeft 404 -- pad mogelijk anders (bijv. /diary/patients).")
            break
        if not resp.ok:
            print(f"  [ERROR] HTTP {resp.status_code} bij /patients")
            break

        try:
            data = resp.json()
        except Exception:
            print("  (Response niet JSON)")
            break

        patients = extract_list(data, "patients", "data")
        for p in patients:
            if not isinstance(p, dict):
                continue
            pid = p.get("patientId") or p.get("id") or p.get("patient_id")

            # email: alle bekende paden, nooit voluit printen
            email = None
            contact = p.get("contact") or {}
            if isinstance(contact, dict):
                for ci_key in ("contactInfo", "contact_info"):
                    ci = contact.get(ci_key) or {}
                    if isinstance(ci, dict):
                        email = ci.get("email") or ci.get("email2")
                        if email:
                            break
            if not email:
                email = p.get("email") or p.get("email2")

            if pid:
                patient_map[str(pid)] = email or None

    with_email    = sum(1 for e in patient_map.values() if e)
    without_email = sum(1 for e in patient_map.values() if not e)
    unique_emails = len({e for e in patient_map.values() if e})
    print(f"  Patiënten opgehaald:  {len(patient_map)}")
    print(f"  Met e-mail:           {with_email}")
    print(f"  Zonder e-mail:        {without_email}")
    print(f"  Unieke e-mails:       {unique_emails}")
    return patient_map


# ---------------------------------------------------------------------------
# Step 8 -- Gemaskeerde voorbeeldrijen
# ---------------------------------------------------------------------------

def step_sample_rows(events: list, patient_map: dict, alias_map: dict):
    print("\n[8] 5 gemaskeerde voorbeeldrijen...")
    shown = 0
    for ev in events:
        if not isinstance(ev, dict) or shown >= 5:
            break
        patients = ev.get("patients") or []
        if not patients:
            continue

        # snake_case primary, camelCase fallback
        event_id   = ev.get("event_id")   or ev.get("eventId")   or ev.get("id")   or "?"
        event_date = ev.get("event_date") or ev.get("eventDate") or ev.get("date") or "?"
        time_from  = ev.get("time_from")  or ev.get("timeFrom")  or ""
        time_to    = ev.get("time_to")    or ev.get("timeTo")    or ""
        event_time = (
            f"{time_from}-{time_to}" if time_from
            else (ev.get("eventTime") or ev.get("event_time") or ev.get("time") or "?")
        )
        # colleague_alias_ids list primary, singular fallback
        cal_list  = ev.get("colleague_alias_ids") or ev.get("colleagueAliasIds") or []
        cal       = (cal_list[0] if isinstance(cal_list, list) and cal_list
                     else (ev.get("colleagueAliasId") or ev.get("colleague_alias_id") or "?"))
        therapist = alias_map.get(str(cal), str(cal))

        for p in patients:
            if shown >= 5:
                break
            pid = fn = sn = None
            if isinstance(p, dict):
                pid = p.get("patientId") or p.get("id") or p.get("patient_id")
                fn  = p.get("forename")  or p.get("firstName") or ""
                sn  = p.get("surname")   or p.get("lastName")  or ""
            else:
                pid = str(p)

            email   = patient_map.get(str(pid)) if pid else None
            print(
                f"  [{shown+1}] datum={event_date}  tijd={event_time}  "
                f"event_id={mask_id(event_id)}  patient_id={mask_id(pid)}  "
                f"email={mask_email(email) if email else '(geen)'}  "
                f"naam={mask_name(fn or '', sn or '')}  therapeut={therapist}"
            )
            shown += 1


# ---------------------------------------------------------------------------
# Step 9 -- Eindrapport + vergelijking
# ---------------------------------------------------------------------------

def step_final_report(events: list, patient_map: dict, alias_map: dict):
    print("\n" + "=" * 62)
    print("EINDRAPPORT")
    print("=" * 62)

    total    = len(events)
    with_pid = sum(1 for ev in events if isinstance(ev, dict) and ev.get("patients"))
    unique_pids = {
        str(p.get("patientId") or p.get("id") or p.get("patient_id"))
        for ev in events if isinstance(ev, dict)
        for p in (ev.get("patients") or [])
        if isinstance(p, dict)
    } | {
        str(p) for ev in events if isinstance(ev, dict)
        for p in (ev.get("patients") or [])
        if not isinstance(p, dict)
    }

    # candidate = APPOINTMENT + niet deleted + patients[] aanwezig
    candidates = sum(
        1 for ev in events
        if isinstance(ev, dict)
        and (ev.get("event_type") or ev.get("eventType")) == "APPOINTMENT"
        and ev.get("deleted") is not True
        and ev.get("patients")
    )

    with_email    = sum(1 for e in patient_map.values() if e)
    without_email = sum(1 for e in patient_map.values() if not e)
    unique_emails = len({e for e in patient_map.values() if e})

    print(f"  Totaal events (incl. lege/blokkerende): {total}")
    print(f"  Candidate review events:                {candidates}")
    print(f"  Events met patient_id:                  {with_pid}")
    print(f"  Unieke patient_ids:                     {len(unique_pids)}")
    print(f"  Patienten met e-mail:                   {with_email}")
    print(f"  Patienten zonder e-mail:                {without_email}")
    print(f"  Unieke e-mails:                         {unique_emails}")

    if alias_map:
        print(f"\n  Therapeuten (gemaskeerd):")
        for aid, name in alias_map.items():
            print(f"    alias_id={aid}: {name}")

    print(f"\n--- Vergelijking met verwachte Excel-export ---")

    def check(label: str, actual: int, expected: int, tol: int = 5):
        status = "PASS" if abs(actual - expected) <= tol else "WARN"
        print(f"  {status}  {label}: {actual}  (verwacht ~{expected})")

    check("Candidate afspraken", candidates,    EXPECTED_EVENTS)
    check("Met e-mail",          with_email,    EXPECTED_WITH_EMAIL)
    check("Unieke e-mails",      unique_emails, EXPECTED_UNIQUE_EMAILS)
    print("=" * 62)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

STAGES = ("auth", "aliases", "events", "full")


def main():
    parser = argparse.ArgumentParser(
        description="READ-ONLY Crossuite POC -- geen schrijf-acties."
    )
    parser.add_argument(
        "--stage",
        choices=STAGES,
        default="auth",
        help=(
            "auth      : token + /clients/info + alias detectie  (default)\n"
            "aliases   : auth + /client-aliases met X-Active-Alias\n"
            "events    : aliases + /diary/events  (geen patients)\n"
            "full      : events + /patients + eindrapport"
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Toon extra diagnostics zoals de opgebouwde query string.",
    )
    args = parser.parse_args()
    stage = args.stage
    debug = args.debug

    load_dotenv()

    global API_URL
    auth_url = os.getenv("CROSSUITE_AUTH_URL", "https://auth.crossuite.app")
    API_URL  = os.getenv("CROSSUITE_API_URL",  "https://service.crossuite.app")

    print("=" * 62)
    print(f"Crossuite Read-Only POC  --  stage: {stage.upper()}")
    print(f"Auth:  {auth_url}")
    print(f"API:   {API_URL}")
    if stage in ("events", "full"):
        print(f"Range: {DATE_FROM} -> {DATE_TO}")
    print("=" * 62)

    # ── Altijd: env + token + clients/info + alias bootstrap ───────────────
    env_alias_id = step_validate_env()

    client_id     = os.getenv("CROSSUITE_CLIENT_ID")
    client_secret = os.getenv("CROSSUITE_CLIENT_SECRET")
    username      = os.getenv("CROSSUITE_USERNAME")
    password      = os.getenv("CROSSUITE_PASSWORD")

    auth_header = step_get_token(auth_url, client_id, client_secret, username, password)
    SESSION.headers.update({"Authorization": auth_header})
    del auth_header

    # Alias prioriteit: .env override -> anders /clients/info
    if env_alias_id:
        print(f"\n[3] Active alias: uit .env (override).")
        alias_id = env_alias_id
    else:
        alias_id = step_get_clients_info()

    if stage == "auth":
        print("\n[STOP] Stage 'auth' voltooid.")
        return

    # ── aliases ─────────────────────────────────────────────────────────────
    alias_map = step_get_client_aliases(alias_id)

    if stage == "aliases":
        print("\n[STOP] Stage 'aliases' voltooid.")
        return

    # ── events ──────────────────────────────────────────────────────────────
    colleague_ids = list(alias_map.keys()) if alias_map else [alias_id]
    events = step_get_events(alias_id, colleague_ids=colleague_ids, debug=debug)
    patient_ids, _ = step_process_events(events)

    if stage == "events":
        print("\n[STOP] Stage 'events' voltooid -- geen patients opgehaald.")
        return

    # ── full ─────────────────────────────────────────────────────────────────
    patient_map = step_get_patients(patient_ids, alias_id)
    step_sample_rows(events, patient_map, alias_map)
    step_final_report(events, patient_map, alias_map)


if __name__ == "__main__":
    main()
