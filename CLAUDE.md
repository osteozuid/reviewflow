# ReviewFlow — context voor Claude

## Wat is dit?
Flask webapp voor Osteozuid: stuurt automatisch review-verzoeken naar patiënten via e-mail.
Gebouwd om later door te verkopen aan andere osteopatenpraktijken (multi-tenant in gedachten houden).

## Server
- VPS Hetzner: `root@178.105.54.50`, app op `/opt/reviewflow`
- Live URL: https://reviews.osteozuid.be
- Deploy: `ssh root@178.105.54.50 "cd /opt/reviewflow; git pull; systemctl restart reviewflow"`
- Git remote: https://github.com/osteozuid/reviewflow.git (branch main)

## Codestructuur
- `app.py` — Flask routes: dashboard, upload, run, schema, logs, instellingen, templates, contacten, google-reviews
- `db.py` — SQLite + tabellen: review_log, import_log, email_templates, contacts, reviewed_names, schedule_config, app_settings
- `mailer.py` — SMTP via One.com, ondersteunt HTML templates met {{voornaam}}, {{google_link}}, {{logo}}
- `csv_import.py` — CSV/Excel inlezen, normaliseren
- `dedup.py` — deduplicatie + fuzzy name matching voor reviewed_names
- `google_reviews.py` — Google Places API: fetch_reviews(), fetch_reviewer_names(), fetch_place_summary()
- `static/style.css` — licht thema (wit/lichtgrijs, oranje accent #f28c00)
- `templates/` — base, dashboard, upload, run, schedule, logs, settings, login, templates_list, template_editor, contacts, google_reviews

## App-instellingen (opgeslagen in SQLite app_settings tabel)
SMTP: smtp_host, smtp_port, smtp_user, smtp_password, from_email, from_name, admin_email
Google: google_review_link, google_places_api_key, google_place_id
Branding: logo_url (URL naar logo, gebruikt in e-mailsjablonen via {{logo}})
App: app_password (gehashed via werkzeug), send_geblokkeerd (testmodus toggle)

## E-mail template variabelen
- `{{voornaam}}` — voornaam patiënt
- `{{google_link}}` — Google Review link (als HTML-knop)
- `{{logo}}` — logo van de praktijk (als <img> tag)

## Beveiliging
- Login verplicht voor alle pagina's
- Wachtwoord: Google-stijl wijzigen (huidig + nieuw 2x), gehashed opgeslagen
- Standaard wachtwoord was reviewflow2024 (gebruiker heeft eigen wachtwoord ingesteld)
- Herstel: SSH naar server → reset APP_PASSWORD in /opt/reviewflow/.env

## Workflow gebruiker
1. CSV uploaden met patiënten (naam, email)
2. Template kiezen of aanmaken
3. Mails versturen (dry-run of echt)
4. Logs bekijken

## Lokale code
Pad: `g:\Mijn Drive\osteozuid\reviewflow\`
Na wijzigingen: git add + commit + push, dan deploy-commando op server uitvoeren.
