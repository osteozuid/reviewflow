# Crossuite API -- Read-Only POC Notes

## Status
Read-only POC geslaagd op 2026-05-27.
Geen productie-integratie. Geen mails verstuurd. Geen Crossuite writes.

---

## Datumrange getest
2026-05-18 t/m 2026-05-24

---

## Auth-flow

```
POST https://auth.crossuite.app/token
  Content-Type: application/x-www-form-urlencoded
  Authorization: Basic base64(client_id:client_secret)
  Body: grant_type=password, username, password
  -> access_token (Bearer)

GET https://service.crossuite.app/clients/info
  Authorization: Bearer <access_token>
  (geen X-Active-Alias header hier)
  -> settings.active_client_alias_id  (= X-Active-Alias voor vervolgcalls)
```

Vervolgcalls gebruiken:
- `Authorization: Bearer <token>`
- `X-Active-Alias: <active_client_alias_id>`

---

## Aliases-flow

```
GET /client-aliases
  X-Active-Alias: <alias_id>
  -> lijst van aliases (client_alias_id, group_id, role, ...)
```

Gevonden: 4 aliases voor de testpraktijk.

---

## Events-flow

```
GET /diary/events
  X-Active-Alias: <alias_id>
  Params:
    date_from=2026-05-18
    date_to=2026-05-24
    allocation_type=COLLEAGUE
    event_types=APPOINTMENT
    limit=500
    offset=0
    order_by=event.event_date
    direction=asc
    history=false
    colleague_aliases=<alias_id>   <- repeated array param, een per alias
    colleague_aliases=<alias_id>
    ...
```

Belangrijk: de filterparameter heet `colleague_aliases` (meervoud, array).
Eerder geprobeerde namen die NIET werkten: `colleague`, `colleague_alias_id`.

Event keys (relevant): `event_id`, `event_date`, `event_type`, `deleted`,
`colleague_alias_ids`, `author_client_alias_id`, `patients[]`

---

## Patients-flow

```
GET /patients
  X-Active-Alias: <alias_id>
  Params (repeated): patient_ids=<id>, patient_ids=<id>, ...
  -> patients lijst met contact.contactInfo.email of contact.contact_info.email
```

Chunks van 50 IDs per request.

---

## Resultaten testweek

| Meting | Waarde |
|--------|--------|
| Totaal events opgehaald | 91 |
| Candidate review events (APPOINTMENT, niet deleted, met patient) | 70 |
| Unieke patient_ids | 68 |
| Patiënten met e-mail | 68 |
| Patiënten zonder e-mail | 0 |
| Unieke e-mails | 68 |
| Aliases (therapeuten) | 4 |

Vergelijking Excel-export (verwacht ~73 afspraken, ~68 unieke e-mails): PASS.

---

## Veiligheidsregels tijdens POC

- Geen mails verstuurd
- Geen database writes
- Geen Crossuite writes (alleen GET requests)
- Geen productie-integratie
- Alle output gemaskeerd (e-mails, namen, IDs)
- .env credentials nooit getoond of gelogd
- Geen verkoopbare Crossuite-integratie claimen zonder expliciete toestemming/afspraken met Crossuite.

---

## Volgende stap

Mapper bouwen van Crossuite event+patient response naar ReviewFlow importstructuur
(voornaam, achternaam, e-mail, datum afspraak, therapeut).
Nog niet implementeren -- eerst architectuurbeslissing over integratiepunt.
Eerste integratie blijft read-only preview/import; geen automatische mailruns vanuit Crossuite zonder manuele bevestiging.
