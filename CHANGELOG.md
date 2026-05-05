# Changelog

## Sprint 1 — SaaS Fundering (2026-05-05)

### Toegevoegd
- **PostgreSQL** vervangt SQLite als database voor de SaaS-versie
- **Multi-tenancy**: alle data-tabellen hebben `tenant_id`; elke query filtert op de ingelogde tenant
- **Gebruikerssysteem**: login met e-mail + wachtwoord (geen globale app_password meer)
- **Rollen**: `superadmin`, `owner`, `staff`
- **Invite-only onboarding**: superadmin maakt tenant aan + stuurt invite-link per e-mail
- **Invite tokens**: verlopen na 7 dagen; één gebruik
- **Superadmin dashboard** (`/admin/tenants`): overzicht van alle tenants, nieuwe tenant aanmaken, extra gebruikers uitnodigen
- **`auth.py`**: nieuwe module met `login_required`, `role_required`, `superadmin_required`, `hash_password`, `verify_password`
- **`migrate_sqlite_to_pg.py`**: migratiescript van bestaande SQLite naar PostgreSQL; maakt tenant `osteozuid`, superadmin, owner, en testtenant `testpraktijk` met invite
- **Audit logging**: login, logout, tenant created, invite sent, invite accepted, superadmin view
- **`document_acceptances` tabel**: schema klaar voor GDPR-flow in Sprint 2
- **`data_retention_days` instelling**: voorbereid via tenant_settings (default Sprint 2)
- **Per-tenant bestandsdirectories**: `input/{tenant_id}/`, `output/{tenant_id}/`, `static/uploads/{tenant_id}/`
- **Per-tenant APScheduler jobs**: job-ID `auto_{tenant_id}`, volledig geïsoleerd
- **Per-tenant run-state**: `_runs` dict gekey'd op tenant_id
- **Systeem-SMTP** (env vars) voor invite-mails, gescheiden van tenant-SMTP
- **`APP_NAME`, `APP_BASE_URL`, `MARKETING_URL`** env vars; invite-links altijd via `APP_BASE_URL`
- **Testsuites** (`tests/conftest.py`, `tests/test_sprint1.py`): 9 test-klassen, 23 tests voor auth, tenant-isolatie, invite flow, branding en upload/run

### Gewijzigd
- `db.py`: volledig herschreven voor PostgreSQL (`psycopg2-binary`); alle functies hebben `tenant_id` parameter
- `app.py`: volledig herschreven; `g.user` / `g.tenant_id` via `before_request`; geen `_update_env_file()` meer
- `mailer.py`: `get_smtp_config(tenant_id)` leest uit `tenant_settings`; aparte `get_system_smtp_config()` voor systeem-mails; geen SMTP naar `.env` schrijven
- `templates/login.html`: e-mailadresveld toegevoegd; geen hardcoded "Osteozuid" meer
- `templates/base.html`: ReviewFlow-branding altijd aanwezig; tenant-logo **nooit** in sidebar/header; gebruikersnaam + rol zichtbaar in sidebar-footer; superadmin-link alleen voor superadmins

### Nieuwe bestanden
- `auth.py`
- `migrate_sqlite_to_pg.py`
- `.env.example`
- `templates/invite.html`
- `templates/admin_tenants.html`
- `tests/__init__.py`
- `tests/conftest.py`
- `tests/test_sprint1.py`

### Verwijderd / niet meer van toepassing
- Globale `app_password` — vervangen door per-user login
- `_update_env_file()` — SMTP-settings worden niet meer naar `.env` geschreven
- Hardcoded `INPUT_DIR`, `OUTPUT_DIR` (globaal) — vervangen door per-tenant directories

### Niet gebouwd in Sprint 1 (volgt in Sprint 2+)
- Stripe / betaling
- Publieke signup
- Crossuite API
- Custom domeinen per tenant
- Volledige GDPR-flow (DPA, privacy, unsubscribe, data-retentiejob)
- AI features
- Marketingwebsite
