# Manueel testplan — Sprint 1

Voer de onderstaande stappen uit na deployment op de testomgeving
(of lokaal via `python app.py` met een PostgreSQL-database).

---

## Voorbereiding

1. Kopieer `.env.example` naar `.env` en vul alle waarden in:
   ```
   DATABASE_URL=postgresql://localhost/reviewflow
   SECRET_KEY=<genereer met: python -c "import secrets; print(secrets.token_hex(32))">
   APP_NAME=ReviewFlow
   APP_BASE_URL=http://localhost:5000
   SYSTEM_SMTP_HOST=...
   SYSTEM_SMTP_USER=...
   SYSTEM_SMTP_PASSWORD=...
   SYSTEM_FROM_EMAIL=...
   SUPERADMIN_EMAIL=primalimport@gmail.com
   SUPERADMIN_PASSWORD=<kies sterk wachtwoord>
   ```

2. Voer de migratie uit:
   ```
   python migrate_sqlite_to_pg.py
   ```
   Verwacht: schema aangemaakt, osteozuid-data gemigreerd, invite URL geprint.

3. Start de app:
   ```
   python app.py
   ```

---

## Test 1 — Login / Logout

- [ ] Ga naar `http://localhost:5000`
- [ ] Verwacht: redirect naar `/login`
- [ ] Log in met verkeerd wachtwoord → foutmelding "Ongeldig e-mailadres of wachtwoord"
- [ ] Log in met superadmin e-mail + wachtwoord → land op dashboard
- [ ] Klik "Uitloggen" → redirect naar `/login`
- [ ] Bezoek `/` → redirect naar `/login` (sessie is weg)

---

## Test 2 — Superadmin tenant aanmaken

- [ ] Log in als superadmin
- [ ] Klik "Tenants" in sidebar (oranje link, alleen zichtbaar voor superadmin)
- [ ] Verwacht: pagina `/admin/tenants` toont bestaande tenants (osteozuid, testpraktijk)
- [ ] Vul formulier in: Naam="Fysiozuid", Slug="fysiozuid", Uitnodigingsmail=jouw e-mailadres
- [ ] Klik "Tenant aanmaken + uitnodiging versturen"
- [ ] Verwacht: flash-bericht "Tenant aangemaakt en uitnodiging verstuurd" OF invite-URL in waarschuwing
- [ ] Verificeer: tenant "fysiozuid" verschijnt in de lijst

---

## Test 3 — Invite token werkt

- [ ] Gebruik de invite-URL uit stap 2 (of de URL die `migrate_sqlite_to_pg.py` heeft geprint voor testpraktijk)
- [ ] Open de URL in een browser
- [ ] Verwacht: formulier "Welkom bij [praktijknaam]" met naam- en wachtwoordveld
- [ ] Vul in: Volledige naam, wachtwoord (min. 8 tekens), herhaal wachtwoord
- [ ] Klik "Account aanmaken"
- [ ] Verwacht: direct ingelogd, land op dashboard van die tenant
- [ ] Probeer dezelfde invite-URL opnieuw → "Deze uitnodiging is al gebruikt"

---

## Test 4 — Owner ziet alleen eigen tenant

- [ ] Log in als owner van osteozuid
- [ ] Ga naar Uitsluitingen, Contacten, Logs, Templates → enkel Osteozuid-data zichtbaar
- [ ] Log uit
- [ ] Log in als owner van testpraktijk (na invite acceptatie)
- [ ] Dezelfde pagina's → lege lijsten of testpraktijk-data; geen Osteozuid-data

---

## Test 5 — Tenant A ziet geen data van tenant B

- [ ] Log in als owner osteozuid
- [ ] Ga naar Logs → enkel Osteozuid review_log en import_log
- [ ] Ga naar Contacten → enkel Osteozuid-contacten
- [ ] Ga naar Templates → enkel Osteozuid-templates
- [ ] Ga naar Instellingen → enkel Osteozuid SMTP/API-sleutels
- [ ] Herhaal als owner testpraktijk → geen overlap

---

## Test 6 — Tenant logo niet in app sidebar/header

- [ ] Log in als owner osteozuid
- [ ] Ga naar Instellingen → upload een logo
- [ ] Ga terug naar dashboard
- [ ] Inspecteer de sidebar: **het tenant-logo mag NIET zichtbaar zijn**
- [ ] De SVG van ReviewFlow en de naam "ReviewFlow" moeten aanwezig zijn
- [ ] Tenant-logo URL mag niet voorkomen in de `<nav>` HTML

---

## Test 7 — Tenant logo in e-mail template preview

- [ ] Log in als owner osteozuid (met logo ingesteld via Instellingen)
- [ ] Ga naar E-mail Templates → klik "Bewerken" op een template
- [ ] In de template editor: klik "Testmail versturen" naar jouw e-mailadres
- [ ] Ontvang de e-mail → **het logo moet zichtbaar zijn in de mail**
- [ ] Controleer ook: `{{logo}}` wordt vervangen door `<img src="...">`

---

## Test 8 — APP_BASE_URL bepaalt de invite-link

- [ ] In `.env`: zet `APP_BASE_URL=http://localhost:5000`
- [ ] Maak een nieuwe tenant aan als superadmin
- [ ] De invite-URL in de flash/mail begint met `http://localhost:5000/invite/`
- [ ] Wijzig `.env`: `APP_BASE_URL=https://reviewflow.osteozuid.be`
- [ ] Herstart app, maak nieuwe tenant aan
- [ ] De invite-URL begint nu met `https://reviewflow.osteozuid.be/invite/`

---

## Test 9 — Upload/Run pagina werkt tenant-aware

- [ ] Log in als owner osteozuid
- [ ] Ga naar Upload → upload een CSV-bestand (MyOrganizer-formaat)
- [ ] Verwacht: bestand verschijnt in lijst
- [ ] Ga naar Uitvoeren → selecteer "Dry run" → klik uitvoeren
- [ ] Verwacht: live log verschijnt, enkel osteozuid-patiënten verwerkt
- [ ] Log in als owner testpraktijk → Uitvoeren → lege run (geen bestanden)
- [ ] Ga naar Logs → testpraktijk ziet enkel eigen import_log-rijen

---

## Geautomatiseerde tests uitvoeren

```bash
# Zorg dat een PostgreSQL-testdatabase beschikbaar is
export TEST_DATABASE_URL=postgresql://localhost/reviewflow_test

cd "g:\Mijn Drive\osteozuid\reviewflow"
pytest tests/ -v
```

Verwacht: alle 23 tests slagen (of skip als testdatabase niet beschikbaar).
