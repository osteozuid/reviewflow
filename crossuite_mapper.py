"""
Maps Crossuite event + patient data to the ReviewFlow internal import format.
Compatible with csv_import.py row structure.
No API calls. No DB writes. No mail sends. No raw patient data logged.
"""

from __future__ import annotations

from typing import Optional


def build_patient_map(patients: list) -> dict:
    """Index patients by patient_id for O(1) lookup. Returns {str(id): patient_dict}."""
    result = {}
    for p in patients:
        if not isinstance(p, dict):
            continue
        pid = p.get("patient_id") or p.get("patientId") or p.get("id")
        if pid:
            result[str(pid)] = p
    return result


def build_alias_map(aliases: list) -> dict:
    """
    Build {alias_id: display_name} from /client-aliases list.
    Falls back to 'alias-{id}' when no name fields are present.
    """
    result = {}
    for a in aliases:
        if not isinstance(a, dict):
            continue
        aid = a.get("client_alias_id") or a.get("clientAliasId") or a.get("id")
        if not aid:
            continue
        name = (
            a.get("full_name") or a.get("fullName")
            or a.get("display_name") or a.get("displayName")
            or a.get("name")
        )
        if not name:
            fn = a.get("forename") or a.get("first_name") or a.get("firstName") or ""
            sn = a.get("surname") or a.get("last_name") or a.get("lastName") or ""
            name = f"{fn} {sn}".strip() or f"alias-{aid}"
        result[str(aid)] = name
    return result


def _extract_email(patient: dict) -> str:
    """Extract email from nested or flat contact info structures."""
    for path in (
        ("contact", "contactInfo", "email"),
        ("contact", "contact_info", "email"),
        ("contactInfo", "email"),
        ("contact_info", "email"),
        ("email",),
    ):
        obj = patient
        for key in path:
            if not isinstance(obj, dict):
                obj = None
                break
            obj = obj.get(key)
        if obj and isinstance(obj, str) and "@" in obj:
            return obj.strip().lower()
    return ""


def _extract_patient_ids(event: dict) -> list:
    patients = event.get("patients") or []
    ids = []
    for p in patients:
        if isinstance(p, dict):
            pid = p.get("patient_id") or p.get("patientId") or p.get("id")
        else:
            pid = p
        if pid and str(pid) not in ids:
            ids.append(str(pid))
    return ids


def _event_date(event: dict) -> Optional[str]:
    raw = event.get("event_date") or event.get("eventDate") or ""
    return str(raw)[:10] if raw else None


def _colleague_name(event: dict, alias_map: dict) -> str:
    ids = event.get("colleague_alias_ids") or event.get("colleagueAliasIds") or []
    for cid in ids:
        name = alias_map.get(str(cid))
        if name:
            return name
    return ""


def map_events_to_rows(
    events: list,
    patient_map: dict,
    alias_map: dict,
) -> tuple[list, dict]:
    """
    Convert Crossuite events + patient_map to ReviewFlow import rows.

    Filters applied:
      - event_type == APPOINTMENT
      - deleted is not True
      - patients[] present
      - email present in patient record

    Returns:
        (rows, stats)
        rows: list of dicts matching csv_import.py _process_rows output format
        stats: counts for preview/logging (no patient data in stats)
    """
    stats = {
        "events_total": len(events),
        "skipped_not_appointment": 0,
        "skipped_deleted": 0,
        "skipped_no_patients": 0,
        "skipped_no_email": 0,
        "rows_ok": 0,
        "unique_emails": 0,
    }

    rows = []
    seen_emails: set[str] = set()

    for ev in events:
        if not isinstance(ev, dict):
            continue

        event_type = ev.get("event_type") or ev.get("eventType")
        if event_type != "APPOINTMENT":
            stats["skipped_not_appointment"] += 1
            continue

        if ev.get("deleted") is True:
            stats["skipped_deleted"] += 1
            continue

        patient_ids = _extract_patient_ids(ev)
        if not patient_ids:
            stats["skipped_no_patients"] += 1
            continue

        datum_consult = _event_date(ev)
        agenda = _colleague_name(ev, alias_map)

        for pid in patient_ids:
            patient = patient_map.get(pid)
            if not patient:
                continue

            email = _extract_email(patient)
            if not email:
                stats["skipped_no_email"] += 1
                continue

            contact = patient.get("contact") or {}
            voornaam = (
                contact.get("forename") or contact.get("first_name")
                or contact.get("firstName") or patient.get("forename") or ""
            ).strip()
            achternaam = (
                contact.get("surname") or contact.get("last_name")
                or contact.get("lastName") or patient.get("surname") or ""
            ).strip()
            naam = f"{achternaam} {voornaam}".strip() if achternaam else voornaam

            rows.append({
                "naam": naam,
                "voornaam": voornaam,
                "achternaam": achternaam,
                "email": email,
                "geboortedatum": None,
                "datum_consult": datum_consult,
                "telefoon": "",
                "gsm": "",
                "agenda": agenda,
                "afspraak_type": "APPOINTMENT",
                "bestand": "crossuite_api",
            })
            seen_emails.add(email)
            stats["rows_ok"] += 1

    stats["unique_emails"] = len(seen_emails)
    return rows, stats
