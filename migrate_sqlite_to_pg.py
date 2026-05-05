"""
Migrate existing SQLite data to PostgreSQL for ReviewFlow SaaS Sprint 1.

Usage:
    python migrate_sqlite_to_pg.py

Required env vars:
    DATABASE_URL        — PostgreSQL connection string
    SUPERADMIN_EMAIL    — e-mail for superadmin account
    SUPERADMIN_PASSWORD — password for superadmin account

Optional:
    SQLITE_PATH         — path to SQLite DB (default: data/review_requests.db)
    OSTEOZUID_OWNER_EMAIL    — e-mail for osteozuid owner (default: same as SUPERADMIN_EMAIL)
    OSTEOZUID_OWNER_PASSWORD — password for osteozuid owner (default: same as SUPERADMIN_PASSWORD)
    TEST_TENANT_INVITE_EMAIL — e-mail to receive test-tenant invite (default: SUPERADMIN_EMAIL)
"""

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Must be set before importing db (which reads DATABASE_URL at module level)
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    print("ERROR: DATABASE_URL niet ingesteld")
    sys.exit(1)

import db
from auth import hash_password

SQLITE_PATH = Path(os.environ.get('SQLITE_PATH', 'data/review_requests.db'))

SUPERADMIN_EMAIL    = os.environ.get('SUPERADMIN_EMAIL', '').strip()
SUPERADMIN_PASSWORD = os.environ.get('SUPERADMIN_PASSWORD', '').strip()

OWNER_EMAIL    = os.environ.get('OSTEOZUID_OWNER_EMAIL', SUPERADMIN_EMAIL).strip()
OWNER_PASSWORD = os.environ.get('OSTEOZUID_OWNER_PASSWORD', SUPERADMIN_PASSWORD).strip()

TEST_INVITE_EMAIL = os.environ.get('TEST_TENANT_INVITE_EMAIL', SUPERADMIN_EMAIL).strip()

APP_BASE_URL = os.environ.get('APP_BASE_URL', 'http://localhost:5000').rstrip('/')


def get_sqlite_conn():
    if not SQLITE_PATH.exists():
        print(f"WAARSCHUWING: SQLite DB niet gevonden op {SQLITE_PATH}")
        return None
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def migrate():
    if not SUPERADMIN_EMAIL or not SUPERADMIN_PASSWORD:
        print("ERROR: SUPERADMIN_EMAIL en SUPERADMIN_PASSWORD zijn verplicht")
        sys.exit(1)

    print("=" * 60)
    print("ReviewFlow SaaS — Migratie SQLite → PostgreSQL")
    print("=" * 60)

    # ── 1. Initialiseer schema ─────────────────────────────────────────────
    print("\n[1/8] PostgreSQL schema aanmaken...")
    db.init_db()
    print("      ✓ Schema klaar")

    # ── 2. Superadmin aanmaken ─────────────────────────────────────────────
    print(f"\n[2/8] Superadmin aanmaken ({SUPERADMIN_EMAIL})...")
    existing_admin = db.get_user_by_email(SUPERADMIN_EMAIL)
    if existing_admin:
        print(f"      ⚠ Superadmin bestaat al (id={existing_admin['id']}), overgeslagen")
        superadmin_id = existing_admin['id']
    else:
        superadmin_id = db.create_user(
            email=SUPERADMIN_EMAIL,
            password_hash=hash_password(SUPERADMIN_PASSWORD),
            role='superadmin',
            full_name='Superadmin',
        )
        print(f"      ✓ Superadmin aangemaakt (id={superadmin_id})")

    # ── 3. Tenant "osteozuid" aanmaken ────────────────────────────────────
    print("\n[3/8] Tenant 'osteozuid' aanmaken...")
    tenant = db.get_tenant_by_slug('osteozuid')
    if tenant:
        print(f"      ⚠ Tenant 'osteozuid' bestaat al (id={tenant['id']}), overgeslagen")
        tenant_id = tenant['id']
    else:
        tenant_id = db.create_tenant('osteozuid', 'Osteozuid')
        print(f"      ✓ Tenant aangemaakt (id={tenant_id})")

    # ── 4. Owner voor osteozuid aanmaken ──────────────────────────────────
    print(f"\n[4/8] Owner aanmaken ({OWNER_EMAIL})...")
    if OWNER_EMAIL == SUPERADMIN_EMAIL:
        print("      ⚠ Owner e-mail = superadmin e-mail; superadmin heeft geen tenant nodig")
        print("      → Aparte owner wordt aangemaakt")

    owner_email_to_use = OWNER_EMAIL if OWNER_EMAIL != SUPERADMIN_EMAIL else f"owner+{OWNER_EMAIL}"
    existing_owner = db.get_user_by_email(owner_email_to_use)
    if existing_owner:
        print(f"      ⚠ Owner bestaat al (id={existing_owner['id']}), overgeslagen")
    else:
        owner_id = db.create_user(
            email=OWNER_EMAIL,
            password_hash=hash_password(OWNER_PASSWORD),
            role='owner',
            tenant_id=tenant_id,
            full_name='Osteozuid Owner',
        )
        print(f"      ✓ Owner aangemaakt (id={owner_id})")

    # ── 5. Data uit SQLite migreren ───────────────────────────────────────
    sqlite_conn = get_sqlite_conn()
    if not sqlite_conn:
        print("\n[5/8] ⚠ SQLite DB niet gevonden — data-migratie overgeslagen")
    else:
        print("\n[5/8] Data migreren uit SQLite...")

        # App settings → tenant_settings
        print("      → app_settings...")
        settings_rows = sqlite_conn.execute("SELECT sleutel, waarde FROM app_settings").fetchall()
        skip_keys = {'app_password'}  # niet migreren
        count = 0
        for row in settings_rows:
            if row['sleutel'] not in skip_keys:
                db.save_tenant_setting(tenant_id, row['sleutel'], row['waarde'])
                count += 1
        print(f"         {count} instellingen gemigreerd")

        # review_log
        print("      → review_log...")
        rows = sqlite_conn.execute(
            "SELECT email, naam, geboortedatum, sent_at, status, bestand, import_batch "
            "FROM review_log"
        ).fetchall()
        migrated = 0
        with db.get_connection() as conn:
            for row in rows:
                try:
                    db._q(conn,
                        """INSERT INTO review_log
                           (tenant_id, email, naam, geboortedatum, sent_at,
                            status, bestand, import_batch)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (tenant_id, row['email'], row['naam'],
                         row['geboortedatum'], row['sent_at'],
                         row['status'] or 'sent', row['bestand'], row['import_batch']))
                    migrated += 1
                except Exception:
                    pass  # skip duplicates
        print(f"         {migrated}/{len(rows)} review_log rijen gemigreerd")

        # import_log
        print("      → import_log...")
        rows = sqlite_conn.execute("SELECT * FROM import_log").fetchall()
        migrated = 0
        with db.get_connection() as conn:
            for row in rows:
                try:
                    db._q(conn,
                        """INSERT INTO import_log
                           (tenant_id, bestand, rijen_gelezen, rijen_ok,
                            unieke_patienten, kandidaten, gemaild,
                            overgeslagen, modus, import_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (tenant_id, row['bestand'], row['rijen_gelezen'],
                         row['rijen_ok'], row['unieke_patienten'],
                         row['kandidaten'], row['gemaild'],
                         row['overgeslagen'], row['modus'], row['import_at']))
                    migrated += 1
                except Exception:
                    pass
        print(f"         {migrated}/{len(rows)} import_log rijen gemigreerd")

        # reviewed_names
        print("      → reviewed_names...")
        rows = sqlite_conn.execute("SELECT naam, bron, created_at FROM reviewed_names").fetchall()
        migrated = 0
        with db.get_connection() as conn:
            for row in rows:
                try:
                    db._q(conn,
                        """INSERT INTO reviewed_names (tenant_id, naam, bron, created_at)
                           VALUES (%s, %s, %s, %s)""",
                        (tenant_id, row['naam'], row['bron'] or 'manual', row['created_at']))
                    migrated += 1
                except Exception:
                    pass
        print(f"         {migrated}/{len(rows)} reviewed_names gemigreerd")

        # blocked
        print("      → blocked...")
        rows = sqlite_conn.execute(
            "SELECT email, naam, geboortedatum, reden, created_at FROM blocked"
        ).fetchall()
        migrated = 0
        with db.get_connection() as conn:
            for row in rows:
                try:
                    db._q(conn,
                        """INSERT INTO blocked
                           (tenant_id, email, naam, geboortedatum, reden, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (tenant_id, row['email'], row['naam'],
                         row['geboortedatum'], row['reden'], row['created_at']))
                    migrated += 1
                except Exception:
                    pass
        print(f"         {migrated}/{len(rows)} blocked rijen gemigreerd")

        # email_templates
        print("      → email_templates...")
        rows = sqlite_conn.execute("SELECT * FROM email_templates").fetchall()
        migrated = 0
        with db.get_connection() as conn:
            # First clear the seeded templates since we'll import from SQLite
            db._q(conn, "DELETE FROM email_templates WHERE tenant_id = %s", (tenant_id,))
            for row in rows:
                try:
                    db._q(conn,
                        """INSERT INTO email_templates
                           (tenant_id, naam, onderwerp, body_html, is_actief,
                            aangemaakt_op, gewijzigd_op)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (tenant_id, row['naam'], row['onderwerp'],
                         row['body_html'], bool(row['is_actief']),
                         row['aangemaakt_op'], row['gewijzigd_op']))
                    migrated += 1
                except Exception as e:
                    print(f"         ⚠ template fout: {e}")
        print(f"         {migrated}/{len(rows)} templates gemigreerd")

        # contacts
        print("      → contacts...")
        rows = sqlite_conn.execute(
            "SELECT naam, email, eerste_mail, laatste_mail, aantal_mails FROM contacts"
        ).fetchall()
        migrated = 0
        with db.get_connection() as conn:
            for row in rows:
                try:
                    db._q(conn,
                        """INSERT INTO contacts
                           (tenant_id, naam, email, eerste_mail, laatste_mail, aantal_mails)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (tenant_id, row['naam'], row['email'],
                         row['eerste_mail'], row['laatste_mail'], row['aantal_mails']))
                    migrated += 1
                except Exception:
                    pass
        print(f"         {migrated}/{len(rows)} contacten gemigreerd")

        # review_snapshots
        print("      → review_snapshots...")
        rows = sqlite_conn.execute("SELECT date, total FROM review_snapshots").fetchall()
        migrated = 0
        with db.get_connection() as conn:
            for row in rows:
                try:
                    db._q(conn,
                        """INSERT INTO review_snapshots (tenant_id, date, total)
                           VALUES (%s, %s, %s)
                           ON CONFLICT (tenant_id, date) DO UPDATE SET total = EXCLUDED.total""",
                        (tenant_id, row['date'], row['total']))
                    migrated += 1
                except Exception:
                    pass
        print(f"         {migrated}/{len(rows)} snapshots gemigreerd")

        # schedule_config
        print("      → schedule_config...")
        cfg_row = sqlite_conn.execute(
            "SELECT * FROM schedule_config WHERE id = 1"
        ).fetchone()
        if cfg_row:
            db.save_schedule_config(tenant_id, {
                'modus':        cfg_row['modus'],
                'dag_van_week': cfg_row['dag_van_week'],
                'tijdstip':     cfg_row['tijdstip'],
                'actief':       bool(cfg_row['actief']),
            })
            print("         ✓ schedule_config gemigreerd")

        sqlite_conn.close()

    # ── 6. Test-tenant aanmaken ───────────────────────────────────────────
    print("\n[6/8] Testtenant 'testpraktijk' aanmaken...")
    test_tenant = db.get_tenant_by_slug('testpraktijk')
    if test_tenant:
        print(f"      ⚠ Testtenant bestaat al (id={test_tenant['id']}), overgeslagen")
        test_tenant_id = test_tenant['id']
    else:
        test_tenant_id = db.create_tenant('testpraktijk', 'Testpraktijk')
        print(f"      ✓ Testtenant aangemaakt (id={test_tenant_id})")

    # ── 7. Invite voor testtenant ─────────────────────────────────────────
    print(f"\n[7/8] Invite token aanmaken voor testtenant ({TEST_INVITE_EMAIL})...")
    token      = db.create_invite_token(
        email=TEST_INVITE_EMAIL,
        tenant_id=test_tenant_id,
        role='owner',
        created_by=superadmin_id,
    )
    invite_url = f'{APP_BASE_URL}/invite/{token}'
    print(f"      ✓ Invite token aangemaakt")
    print(f"      Invite URL: {invite_url}")

    # ── 8. Overzicht ──────────────────────────────────────────────────────
    print("\n[8/8] Migratie voltooid!")
    print("\n" + "=" * 60)
    print("SAMENVATTING")
    print("=" * 60)
    print(f"  Superadmin:     {SUPERADMIN_EMAIL}")
    print(f"  Tenant:         osteozuid (id={tenant_id})")
    print(f"  Owner:          {OWNER_EMAIL}")
    print(f"  Test-tenant:    testpraktijk (id={test_tenant_id})")
    print(f"  Test invite:    {invite_url}")
    print("=" * 60)
    print("\nVolgende stap: open de invite URL in een browser om het owner-account te bevestigen.")


if __name__ == '__main__':
    migrate()
