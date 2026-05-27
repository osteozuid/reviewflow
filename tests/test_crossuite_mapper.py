"""Unit tests for crossuite_mapper -- no API calls, no secrets needed."""

import pytest

from crossuite_mapper import (
    _extract_email,
    build_alias_map,
    build_patient_map,
    map_events_to_rows,
)


# -- Helpers ------------------------------------------------------------------


def _patient(pid, email, forename="Jan", surname="Test", nested=True):
    p = {"patient_id": pid}
    if nested:
        p["contact"] = {
            "contactInfo": {"email": email},
            "forename": forename,
            "surname": surname,
        }
    else:
        p["email"] = email
        p["forename"] = forename
        p["surname"] = surname
    return p


def _event(eid, pid, date="2026-05-20", colleague_id="a1", deleted=False, etype="APPOINTMENT"):
    return {
        "event_id": eid,
        "event_type": etype,
        "event_date": date,
        "deleted": deleted,
        "patients": [{"patient_id": pid}],
        "colleague_alias_ids": [colleague_id],
    }


# -- build_patient_map --------------------------------------------------------


def test_patient_map_indexes_by_id():
    pm = build_patient_map([_patient("p1", "a@x.be"), _patient("p2", "b@x.be")])
    assert "p1" in pm and "p2" in pm


def test_patient_map_skips_non_dicts():
    pm = build_patient_map(["bad", None, {"patient_id": "p1"}])
    assert list(pm) == ["p1"]


# -- build_alias_map ----------------------------------------------------------


def test_alias_map_uses_full_name():
    am = build_alias_map([{"client_alias_id": "a1", "full_name": "Dr. Smith"}])
    assert am["a1"] == "Dr. Smith"


def test_alias_map_combines_forename_surname():
    am = build_alias_map([{"client_alias_id": "a1", "forename": "Jan", "surname": "Peeters"}])
    assert am["a1"] == "Jan Peeters"


def test_alias_map_falls_back_to_alias_id():
    am = build_alias_map([{"client_alias_id": "a99"}])
    assert am["a99"] == "alias-a99"


# -- _extract_email -----------------------------------------------------------


def test_extract_email_nested_contactInfo():
    p = {"contact": {"contactInfo": {"email": "test@example.be"}}}
    assert _extract_email(p) == "test@example.be"


def test_extract_email_flat():
    assert _extract_email({"email": "flat@example.be"}) == "flat@example.be"


def test_extract_email_lowercases():
    assert _extract_email({"email": "TEST@EXAMPLE.BE"}) == "test@example.be"


def test_extract_email_missing():
    assert _extract_email({}) == ""


def test_extract_email_empty_string():
    p = {"contact": {"contactInfo": {"email": ""}}}
    assert _extract_email(p) == ""


# -- map_events_to_rows -------------------------------------------------------


def test_map_basic_row():
    rows, stats = map_events_to_rows(
        [_event("e1", "p1")],
        build_patient_map([_patient("p1", "jan@test.be")]),
        {"a1": "Dr. Test"},
    )
    assert stats["rows_ok"] == 1
    row = rows[0]
    assert row["email"] == "jan@test.be"
    assert row["bestand"] == "crossuite_api"
    assert row["afspraak_type"] == "APPOINTMENT"
    assert row["agenda"] == "Dr. Test"
    assert row["datum_consult"] == "2026-05-20"


def test_map_skips_deleted():
    rows, stats = map_events_to_rows(
        [_event("e1", "p1", deleted=True)],
        build_patient_map([_patient("p1", "x@test.be")]),
        {},
    )
    assert stats["rows_ok"] == 0
    assert stats["skipped_deleted"] == 1


def test_map_skips_non_appointment():
    rows, stats = map_events_to_rows(
        [_event("e1", "p1", etype="ABSENCE")],
        build_patient_map([_patient("p1", "x@test.be")]),
        {},
    )
    assert stats["rows_ok"] == 0
    assert stats["skipped_not_appointment"] == 1


def test_map_skips_no_patients():
    ev = _event("e1", "p1")
    ev["patients"] = []
    rows, stats = map_events_to_rows([ev], {}, {})
    assert stats["skipped_no_patients"] == 1


def test_map_skips_no_email():
    rows, stats = map_events_to_rows(
        [_event("e1", "p1")],
        build_patient_map([_patient("p1", "")]),
        {},
    )
    assert stats["skipped_no_email"] >= 1
    assert stats["rows_ok"] == 0


def test_map_unique_emails_counted():
    rows, stats = map_events_to_rows(
        [_event("e1", "p1"), _event("e2", "p1")],
        build_patient_map([_patient("p1", "same@test.be")]),
        {},
    )
    assert stats["rows_ok"] == 2
    assert stats["unique_emails"] == 1


def test_map_naam_from_surname_voornaam():
    rows, _ = map_events_to_rows(
        [_event("e1", "p1")],
        build_patient_map([_patient("p1", "x@test.be", forename="Jan", surname="Peeters")]),
        {},
    )
    assert rows[0]["voornaam"] == "Jan"
    assert rows[0]["achternaam"] == "Peeters"
    assert rows[0]["naam"] == "Peeters Jan"


def test_map_flat_patient_email():
    rows, stats = map_events_to_rows(
        [_event("e1", "p1")],
        build_patient_map([_patient("p1", "flat@test.be", nested=False)]),
        {},
    )
    assert stats["rows_ok"] == 1
    assert rows[0]["email"] == "flat@test.be"
