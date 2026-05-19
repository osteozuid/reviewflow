import csv
import json
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import psycopg2
import psycopg2.extras
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://localhost/reviewflow')

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id          SERIAL PRIMARY KEY,
    slug        VARCHAR(100) UNIQUE NOT NULL,
    name        VARCHAR(255) NOT NULL,
    created_at  TIMESTAMP DEFAULT NOW(),
    is_active   BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    tenant_id     INTEGER REFERENCES tenants(id) ON DELETE CASCADE,
    email         VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(50) NOT NULL CHECK (role IN ('superadmin', 'owner', 'staff')),
    full_name     VARCHAR(255),
    is_active     BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMP DEFAULT NOW(),
    last_login    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS invite_tokens (
    id          SERIAL PRIMARY KEY,
    token       VARCHAR(255) UNIQUE NOT NULL,
    email       VARCHAR(255) NOT NULL,
    tenant_id   INTEGER REFERENCES tenants(id) ON DELETE CASCADE NOT NULL,
    role        VARCHAR(50) NOT NULL DEFAULT 'owner',
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    expires_at  TIMESTAMP NOT NULL,
    accepted_at TIMESTAMP,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tenant_settings (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER REFERENCES tenants(id) ON DELETE CASCADE NOT NULL,
    sleutel     VARCHAR(255) NOT NULL,
    waarde      TEXT,
    updated_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE(tenant_id, sleutel)
);

CREATE TABLE IF NOT EXISTS review_log (
    id           SERIAL PRIMARY KEY,
    tenant_id    INTEGER REFERENCES tenants(id) ON DELETE CASCADE NOT NULL,
    email        VARCHAR(255) NOT NULL,
    naam         VARCHAR(255),
    geboortedatum DATE,
    sent_at      TIMESTAMP DEFAULT NOW(),
    status       VARCHAR(50) DEFAULT 'sent',
    bestand      VARCHAR(255),
    import_batch VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS import_log (
    id               SERIAL PRIMARY KEY,
    tenant_id        INTEGER REFERENCES tenants(id) ON DELETE CASCADE NOT NULL,
    bestand          VARCHAR(255),
    rijen_gelezen    INTEGER DEFAULT 0,
    rijen_ok         INTEGER DEFAULT 0,
    unieke_patienten INTEGER DEFAULT 0,
    kandidaten       INTEGER DEFAULT 0,
    gemaild          INTEGER DEFAULT 0,
    overgeslagen     INTEGER DEFAULT 0,
    modus            VARCHAR(50),
    import_at        TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS blocked (
    id            SERIAL PRIMARY KEY,
    tenant_id     INTEGER REFERENCES tenants(id) ON DELETE CASCADE NOT NULL,
    email         VARCHAR(255),
    naam          VARCHAR(255),
    geboortedatum DATE,
    reden         TEXT,
    created_at    TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS reviewed_names (
    id         SERIAL PRIMARY KEY,
    tenant_id  INTEGER REFERENCES tenants(id) ON DELETE CASCADE NOT NULL,
    naam       VARCHAR(255) NOT NULL,
    bron       VARCHAR(100) DEFAULT 'manual',
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(tenant_id, naam)
);

CREATE TABLE IF NOT EXISTS schedule_config (
    id           SERIAL PRIMARY KEY,
    tenant_id    INTEGER REFERENCES tenants(id) ON DELETE CASCADE NOT NULL UNIQUE,
    modus        VARCHAR(50) DEFAULT 'manual',
    dag_van_week INTEGER DEFAULT 0,
    tijdstip     VARCHAR(10) DEFAULT '09:00',
    actief       BOOLEAN DEFAULT FALSE,
    updated_at   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS email_templates (
    id           SERIAL PRIMARY KEY,
    tenant_id    INTEGER REFERENCES tenants(id) ON DELETE CASCADE NOT NULL,
    naam         VARCHAR(255) NOT NULL,
    onderwerp    VARCHAR(255) NOT NULL,
    body_html    TEXT NOT NULL,
    is_actief    BOOLEAN DEFAULT FALSE,
    aangemaakt_op TIMESTAMP DEFAULT NOW(),
    gewijzigd_op  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contacts (
    id           SERIAL PRIMARY KEY,
    tenant_id    INTEGER REFERENCES tenants(id) ON DELETE CASCADE NOT NULL,
    naam         VARCHAR(255),
    email        VARCHAR(255) NOT NULL,
    eerste_mail  TIMESTAMP,
    laatste_mail TIMESTAMP,
    aantal_mails INTEGER DEFAULT 1,
    UNIQUE(tenant_id, email)
);

CREATE TABLE IF NOT EXISTS review_snapshots (
    id        SERIAL PRIMARY KEY,
    tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE NOT NULL,
    date      DATE NOT NULL,
    total     INTEGER,
    UNIQUE(tenant_id, date)
);

CREATE TABLE IF NOT EXISTS patients (
    id            SERIAL PRIMARY KEY,
    tenant_id     INTEGER REFERENCES tenants(id) ON DELETE CASCADE NOT NULL,
    email         VARCHAR(255),
    email_hash    VARCHAR(255),
    naam          VARCHAR(255),
    voornaam      VARCHAR(255),
    geboortedatum DATE,
    telefoon      VARCHAR(50),
    gsm           VARCHAR(50),
    agenda        VARCHAR(255),
    created_at    TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS document_acceptances (
    id               SERIAL PRIMARY KEY,
    tenant_id        INTEGER REFERENCES tenants(id) ON DELETE CASCADE NOT NULL,
    user_id          INTEGER REFERENCES users(id) NOT NULL,
    document_type    VARCHAR(100) NOT NULL,
    document_version VARCHAR(50),
    accepted_at      TIMESTAMP DEFAULT NOW(),
    ip_address       VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id         SERIAL PRIMARY KEY,
    tenant_id  INTEGER REFERENCES tenants(id) ON DELETE SET NULL,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action     VARCHAR(100) NOT NULL,
    details    JSONB,
    ip_address VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_review_log_tenant    ON review_log(tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_review_log_tenant_email ON review_log(tenant_id, email);
CREATE INDEX IF NOT EXISTS idx_blocked_tenant        ON blocked(tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_tenant          ON audit_logs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_user            ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_invite_token          ON invite_tokens(token);
"""


@contextmanager
def get_connection():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _q(conn, query, params=()):
    """Execute a query and return a RealDictCursor."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(query, params)
    return cur


def _patch_template_text(old, new, naam):
    """One-time idempotent text replacement across all tenants for a named preset template."""
    with get_connection() as conn:
        _q(conn,
            "UPDATE email_templates "
            "SET body_html = REPLACE(body_html, %s, %s) "
            "WHERE naam = %s AND body_html LIKE %s",
            (old, new, naam, f'%{old}%'))


def _patch_template_subject(old_subject, new_subject, naam):
    """Idempotent subject fix for a named preset template across all tenants."""
    with get_connection() as conn:
        _q(conn,
            "UPDATE email_templates SET onderwerp = %s "
            "WHERE naam = %s AND onderwerp = %s",
            (new_subject, naam, old_subject))


def init_db():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
    _patch_template_text('massagesessie', 'massage sessie', 'Massage - Warm')
    _patch_template_subject(
        'Hoe was uw massage?',
        'Bedankt voor uw bezoek aan {{praktijknaam}}',
        'Massage - Warm',
    )


# ─── Tenants ──────────────────────────────────────────────────────────────────

def create_tenant(slug, name):
    with get_connection() as conn:
        cur = _q(conn,
            "INSERT INTO tenants (slug, name) VALUES (%s, %s) RETURNING id",
            (slug.lower().strip(), name.strip()))
        tenant_id = cur.fetchone()['id']
    seed_preset_templates(tenant_id)
    return tenant_id


def get_tenant(tenant_id):
    with get_connection() as conn:
        cur = _q(conn, "SELECT * FROM tenants WHERE id = %s", (tenant_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def get_tenant_by_slug(slug):
    with get_connection() as conn:
        cur = _q(conn, "SELECT * FROM tenants WHERE slug = %s", (slug.lower().strip(),))
        row = cur.fetchone()
    return dict(row) if row else None


def get_all_tenants():
    with get_connection() as conn:
        cur = _q(conn,
            """SELECT t.*, COUNT(u.id) as user_count
               FROM tenants t
               LEFT JOIN users u ON u.tenant_id = t.id AND u.is_active = TRUE
               GROUP BY t.id
               ORDER BY t.created_at DESC""")
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def set_tenant_active(tenant_id, active):
    with get_connection() as conn:
        _q(conn, "UPDATE tenants SET is_active = %s WHERE id = %s", (active, tenant_id))


# ─── Users ────────────────────────────────────────────────────────────────────

def create_user(email, password_hash, role, tenant_id=None, full_name=None):
    with get_connection() as conn:
        cur = _q(conn,
            """INSERT INTO users (email, password_hash, role, tenant_id, full_name)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (email.lower().strip(), password_hash, role, tenant_id, full_name))
        user_id = cur.fetchone()['id']
    return user_id


def get_user_by_email(email):
    with get_connection() as conn:
        cur = _q(conn,
            "SELECT * FROM users WHERE email = %s AND is_active = TRUE",
            (email.lower().strip(),))
        row = cur.fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id):
    with get_connection() as conn:
        cur = _q(conn,
            "SELECT * FROM users WHERE id = %s AND is_active = TRUE", (user_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def update_user_last_login(user_id):
    with get_connection() as conn:
        _q(conn, "UPDATE users SET last_login = NOW() WHERE id = %s", (user_id,))


def update_user_password(user_id, password_hash):
    with get_connection() as conn:
        _q(conn, "UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id))


def get_tenant_users(tenant_id):
    with get_connection() as conn:
        cur = _q(conn,
            """SELECT id, email, full_name, role, created_at, last_login
               FROM users WHERE tenant_id = %s AND is_active = TRUE ORDER BY created_at""",
            (tenant_id,))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


# ─── Invite tokens ────────────────────────────────────────────────────────────

def create_invite_token(email, tenant_id, role='owner', created_by=None, expires_days=7):
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(days=expires_days)
    with get_connection() as conn:
        _q(conn,
            """INSERT INTO invite_tokens (token, email, tenant_id, role, created_by, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (token, email.lower().strip(), tenant_id, role, created_by, expires_at))
    return token


def get_invite_token(token):
    with get_connection() as conn:
        cur = _q(conn, "SELECT * FROM invite_tokens WHERE token = %s", (token,))
        row = cur.fetchone()
    return dict(row) if row else None


def accept_invite_token(token):
    with get_connection() as conn:
        _q(conn,
            """UPDATE invite_tokens SET accepted_at = NOW()
               WHERE token = %s AND accepted_at IS NULL""",
            (token,))


def get_pending_invites(tenant_id):
    with get_connection() as conn:
        cur = _q(conn,
            """SELECT * FROM invite_tokens
               WHERE tenant_id = %s AND accepted_at IS NULL AND expires_at > NOW()
               ORDER BY created_at DESC""",
            (tenant_id,))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


# ─── Audit logs ───────────────────────────────────────────────────────────────

def log_audit(action, user_id=None, tenant_id=None, details=None, ip=None):
    details_json = json.dumps(details) if details else None
    try:
        with get_connection() as conn:
            _q(conn,
                """INSERT INTO audit_logs (action, user_id, tenant_id, details, ip_address)
                   VALUES (%s, %s, %s, %s::jsonb, %s)""",
                (action, user_id, tenant_id, details_json, ip))
    except Exception:
        pass  # Audit logging must never crash the main flow


# ─── Tenant settings ──────────────────────────────────────────────────────────

def get_tenant_setting(tenant_id, sleutel, default=''):
    with get_connection() as conn:
        cur = _q(conn,
            "SELECT waarde FROM tenant_settings WHERE tenant_id = %s AND sleutel = %s",
            (tenant_id, sleutel))
        row = cur.fetchone()
    if row and row['waarde'] is not None:
        return row['waarde']
    return default


def save_tenant_setting(tenant_id, sleutel, waarde):
    with get_connection() as conn:
        _q(conn,
            """INSERT INTO tenant_settings (tenant_id, sleutel, waarde, updated_at)
               VALUES (%s, %s, %s, NOW())
               ON CONFLICT (tenant_id, sleutel) DO UPDATE
               SET waarde = EXCLUDED.waarde, updated_at = NOW()""",
            (tenant_id, sleutel, waarde))


def save_tenant_settings(tenant_id, settings_dict):
    for sleutel, waarde in settings_dict.items():
        save_tenant_setting(tenant_id, sleutel, waarde)


def get_all_tenant_settings(tenant_id):
    with get_connection() as conn:
        cur = _q(conn,
            "SELECT sleutel, waarde FROM tenant_settings WHERE tenant_id = %s",
            (tenant_id,))
        rows = cur.fetchall()
    return {r['sleutel']: r['waarde'] for r in rows}


# ─── Review log ───────────────────────────────────────────────────────────────

def get_already_sent(tenant_id):
    with get_connection() as conn:
        cur = _q(conn,
            "SELECT email FROM review_log WHERE tenant_id = %s", (tenant_id,))
        rows = cur.fetchall()
    return {r['email'].lower() for r in rows}


def log_sent(tenant_id, candidates, bestand, batch_id=None):
    now = datetime.now()
    with get_connection() as conn:
        for c in candidates:
            try:
                _q(conn,
                    """INSERT INTO review_log
                       (tenant_id, email, naam, geboortedatum, sent_at, status, bestand, import_batch)
                       VALUES (%s, %s, %s, %s, %s, 'sent', %s, %s)""",
                    (tenant_id, c['email'], c['naam'],
                     c.get('geboortedatum'), now, bestand, batch_id))
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
    for c in candidates:
        upsert_contact(tenant_id, c.get('naam', ''), c['email'], now)


def log_import(tenant_id, bestand, rijen_gelezen, rijen_ok, unieke_patienten,
               kandidaten, gemaild, overgeslagen, modus):
    with get_connection() as conn:
        _q(conn,
            """INSERT INTO import_log
               (tenant_id, bestand, rijen_gelezen, rijen_ok, unieke_patienten,
                kandidaten, gemaild, overgeslagen, modus)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (tenant_id, bestand, rijen_gelezen, rijen_ok, unieke_patienten,
             kandidaten, gemaild, overgeslagen, modus))


def export_review_log(tenant_id, output_path):
    with get_connection() as conn:
        cur = _q(conn,
            """SELECT email, naam, geboortedatum, sent_at, status, bestand
               FROM review_log WHERE tenant_id = %s ORDER BY sent_at""",
            (tenant_id,))
        rows = cur.fetchall()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(['email', 'naam', 'geboortedatum', 'sent_at', 'status', 'bestand'])
        for row in rows:
            writer.writerow([row['email'], row['naam'], row['geboortedatum'],
                             row['sent_at'], row['status'], row['bestand']])
    return len(rows)


def get_sent_logs(tenant_id, limit=100):
    with get_connection() as conn:
        cur = _q(conn,
            """SELECT naam, email, sent_at, bestand FROM review_log
               WHERE tenant_id = %s ORDER BY sent_at DESC LIMIT %s""",
            (tenant_id, limit))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_import_logs(tenant_id, limit=30):
    with get_connection() as conn:
        cur = _q(conn,
            """SELECT * FROM import_log WHERE tenant_id = %s
               ORDER BY import_at DESC LIMIT %s""",
            (tenant_id, limit))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


# ─── Blocked list ─────────────────────────────────────────────────────────────

def get_blocked(tenant_id):
    with get_connection() as conn:
        cur = _q(conn,
            """SELECT email FROM blocked
               WHERE tenant_id = %s AND email IS NOT NULL AND email != ''""",
            (tenant_id,))
        rows = cur.fetchall()
    return {r['email'].lower() for r in rows}


def get_blocked_names(tenant_id):
    with get_connection() as conn:
        cur = _q(conn,
            """SELECT naam FROM blocked
               WHERE tenant_id = %s AND naam IS NOT NULL AND naam != ''""",
            (tenant_id,))
        rows = cur.fetchall()
    return [r['naam'] for r in rows]


def get_blocked_list(tenant_id):
    with get_connection() as conn:
        cur = _q(conn,
            """SELECT id, naam, email, reden, created_at FROM blocked
               WHERE tenant_id = %s ORDER BY created_at DESC""",
            (tenant_id,))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def add_blocked_person(tenant_id, naam, email='', reden=''):
    with get_connection() as conn:
        _q(conn,
            "INSERT INTO blocked (tenant_id, naam, email, reden) VALUES (%s, %s, %s, %s)",
            (tenant_id, naam.strip(), email.strip() or None, reden.strip() or None))


def delete_blocked_person(tenant_id, blocked_id):
    with get_connection() as conn:
        _q(conn,
            "DELETE FROM blocked WHERE id = %s AND tenant_id = %s",
            (blocked_id, tenant_id))


# ─── Reviewed names ───────────────────────────────────────────────────────────

def get_reviewed_names(tenant_id):
    with get_connection() as conn:
        cur = _q(conn,
            "SELECT naam FROM reviewed_names WHERE tenant_id = %s ORDER BY naam",
            (tenant_id,))
        rows = cur.fetchall()
    return [r['naam'] for r in rows]


def get_reviewed_names_full(tenant_id):
    with get_connection() as conn:
        cur = _q(conn,
            """SELECT naam, bron, created_at FROM reviewed_names
               WHERE tenant_id = %s ORDER BY naam""",
            (tenant_id,))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def add_reviewed_name(tenant_id, naam, bron='manual'):
    with get_connection() as conn:
        try:
            _q(conn,
                "INSERT INTO reviewed_names (tenant_id, naam, bron) VALUES (%s, %s, %s)",
                (tenant_id, naam.strip(), bron))
            return True
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            return False


def delete_reviewed_name(tenant_id, naam):
    with get_connection() as conn:
        _q(conn,
            "DELETE FROM reviewed_names WHERE tenant_id = %s AND naam = %s",
            (tenant_id, naam))


# ─── Schedule config ──────────────────────────────────────────────────────────

def get_schedule_config(tenant_id):
    with get_connection() as conn:
        cur = _q(conn,
            "SELECT * FROM schedule_config WHERE tenant_id = %s", (tenant_id,))
        row = cur.fetchone()
    if row:
        return dict(row)
    return {'modus': 'manual', 'dag_van_week': 0, 'tijdstip': '09:00', 'actief': False}


def save_schedule_config(tenant_id, cfg):
    with get_connection() as conn:
        _q(conn,
            """INSERT INTO schedule_config
               (tenant_id, modus, dag_van_week, tijdstip, actief, updated_at)
               VALUES (%s, %s, %s, %s, %s, NOW())
               ON CONFLICT (tenant_id) DO UPDATE
               SET modus = EXCLUDED.modus,
                   dag_van_week = EXCLUDED.dag_van_week,
                   tijdstip = EXCLUDED.tijdstip,
                   actief = EXCLUDED.actief,
                   updated_at = NOW()""",
            (tenant_id, cfg['modus'], cfg['dag_van_week'],
             cfg['tijdstip'], cfg['actief']))


# ─── Email templates ──────────────────────────────────────────────────────────

def get_all_templates(tenant_id):
    with get_connection() as conn:
        cur = _q(conn,
            """SELECT * FROM email_templates WHERE tenant_id = %s
               ORDER BY aangemaakt_op DESC""",
            (tenant_id,))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_active_template(tenant_id):
    with get_connection() as conn:
        cur = _q(conn,
            """SELECT * FROM email_templates
               WHERE tenant_id = %s AND is_actief = TRUE LIMIT 1""",
            (tenant_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def get_template(tenant_id, template_id):
    with get_connection() as conn:
        cur = _q(conn,
            "SELECT * FROM email_templates WHERE id = %s AND tenant_id = %s",
            (template_id, tenant_id))
        row = cur.fetchone()
    return dict(row) if row else None


def save_template(tenant_id, naam, onderwerp, body_html, template_id=None):
    with get_connection() as conn:
        if template_id:
            _q(conn,
                """UPDATE email_templates
                   SET naam=%s, onderwerp=%s, body_html=%s, gewijzigd_op=NOW()
                   WHERE id=%s AND tenant_id=%s""",
                (naam, onderwerp, body_html, template_id, tenant_id))
        else:
            _q(conn,
                """INSERT INTO email_templates (tenant_id, naam, onderwerp, body_html)
                   VALUES (%s, %s, %s, %s)""",
                (tenant_id, naam, onderwerp, body_html))


def delete_template(tenant_id, template_id):
    tpl = get_template(tenant_id, template_id)
    if not tpl:
        return False, 'Template niet gevonden'
    if tpl['is_actief']:
        return False, 'Kan actief template niet verwijderen'
    with get_connection() as conn:
        _q(conn,
            "DELETE FROM email_templates WHERE id=%s AND tenant_id=%s",
            (template_id, tenant_id))
    return True, 'Verwijderd'


def set_active_template(tenant_id, template_id):
    with get_connection() as conn:
        _q(conn,
            "UPDATE email_templates SET is_actief = FALSE WHERE tenant_id = %s",
            (tenant_id,))
        _q(conn,
            "UPDATE email_templates SET is_actief = TRUE WHERE id = %s AND tenant_id = %s",
            (template_id, tenant_id))


_CELL = "max-width:600px;margin:0 auto;padding:32px 24px;"
_FONT = "font-family:Calibri,Candara,'Segoe UI',Arial,sans-serif;"
_P    = "font-size:15px;line-height:1.7;color:#1a1a1a;margin:0 0 16px 0;"
_BTN  = ("background:#f28c00;color:#ffffff;text-decoration:none;"
         "padding:12px 28px;border-radius:4px;font-size:14px;"
         "font-weight:600;display:inline-block;")

def _tpl(body_rows):
    return (
        f'<!DOCTYPE html><html lang="nl"><head><meta charset="UTF-8"></head>'
        f'<body style="margin:0;padding:0;background:#ffffff;{_FONT}">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td style="{_CELL}">{{{{logo}}}}{body_rows}</td></tr>'
        f'</table></body></html>'
    )


DEFAULT_EMAIL_TEMPLATES = [
    {
        # Exact gebaseerd op productie-template "Origineel (Osteozuid)" (template id=4)
        # "Osteozuid" vervangen door {{praktijknaam}}
        'naam': 'Praktijk - Standaard',
        'onderwerp': 'Uw ervaring bij {{praktijknaam}}',
        'is_actief': True,
        'body_html': (
            '<p>Dag {{voornaam}},</p>'
            '<p>Bedankt voor uw bezoek aan {{praktijknaam}}.</p>'
            '<p>We proberen elke patiënt zo goed mogelijk te begeleiden. '
            'Als u enkele minuten tijd heeft, zouden we het enorm waarderen '
            'als u uw ervaring wilt delen via Google — dat helpt andere mensen '
            'om een praktijk te vinden die bij hen past.</p>'
            '<p><a href="{{google_link}}" '
            'style="color:#1a73e8;text-decoration:underline;font-weight:bold;">'
            'Deel uw ervaring via Google</a></p>'
            '<p>Wilt u liever rechtstreeks iets aan ons doorgeven? '
            'Antwoord dan gerust op deze mail.</p>'
            '<p>Vriendelijke groeten,<br>{{praktijknaam}}</p>'
        ),
    },
    {
        'naam': 'Professioneel',
        'onderwerp': 'Bedankt voor uw bezoek — deel uw ervaring',
        'is_actief': False,
        'body_html': _tpl(
            f'<p style="{_P}">Geachte {{{{voornaam}}}},</p>'
            f'<p style="{_P}">Bedankt voor uw bezoek aan onze praktijk. '
            'Wij hopen u op een professionele en zorgzame manier te hebben geholpen.</p>'
            f'<p style="{_P}">Indien u tevreden bent, stellen wij het zeer op prijs '
            'als u even de tijd neemt om een Google-recensie achter te laten. '
            'Dit helpt ons en andere zorgzoekenden enorm.</p>'
            f'<p style="margin:24px 0;"><a href="{{{{google_link}}}}" style="{_BTN}">'
            'Schrijf een recensie</a></p>'
            f'<p style="font-size:13px;line-height:1.6;color:#555555;margin:0;">'
            'Met vriendelijke groeten,<br><strong>{{{{praktijknaam}}}}</strong></p>'
        ),
    },
    {
        'naam': 'Kort & Krachtig',
        'onderwerp': '1 minuut voor uw mening?',
        'is_actief': False,
        'body_html': _tpl(
            f'<p style="{_P}">Dag {{{{voornaam}}}},</p>'
            f'<p style="{_P}">Had u een goede ervaring? Deel het in 1 minuut via Google:</p>'
            f'<p style="margin:0 0 24px 0;"><a href="{{{{google_link}}}}" style="{_BTN}">'
            'Review schrijven</a></p>'
            '<p style="font-size:13px;line-height:1.6;color:#888888;margin:0;">'
            'Vragen? Antwoord gewoon op deze mail.</p>'
        ),
    },
    {
        # Tekst aangeleverd door gebruiker
        'naam': 'Massage - Warm',
        'onderwerp': 'Bedankt voor uw bezoek aan {{praktijknaam}}',
        'is_actief': False,
        'body_html': (
            '{{logo}}'
            '<p>Dag {{voornaam}},</p>'
            '<p>Bedankt voor uw bezoek aan {{praktijknaam}}.</p>'
            '<p>We hopen dat u nog wat nageniet van uw massage sessie.</p>'
            '<p>Als u een momentje heeft, zouden we het erg waarderen '
            'als u uw ervaring deelt via Google.</p>'
            '<p><a href="{{google_link}}" '
            'style="color:#1a73e8;text-decoration:underline;font-weight:bold;">'
            'Deel uw ervaring via Google</a></p>'
            '<p>Tot de volgende keer,<br>{{praktijknaam}}</p>'
        ),
    },
]


def seed_preset_templates(tenant_id):
    """Add default templates to a tenant — only inserts missing ones (by name)."""
    with get_connection() as conn:
        cur = _q(conn,
            "SELECT naam FROM email_templates WHERE tenant_id = %s",
            (tenant_id,))
        existing_names = {row['naam'] for row in cur.fetchall()}

        has_active = False
        if existing_names:
            cur2 = _q(conn,
                "SELECT COUNT(*) as cnt FROM email_templates "
                "WHERE tenant_id = %s AND is_actief = TRUE",
                (tenant_id,))
            has_active = cur2.fetchone()['cnt'] > 0

        for tpl in DEFAULT_EMAIL_TEMPLATES:
            if tpl['naam'] in existing_names:
                continue
            is_actief = tpl['is_actief'] and not has_active
            if is_actief:
                has_active = True
            _q(conn,
                """INSERT INTO email_templates
                   (tenant_id, naam, onderwerp, body_html, is_actief)
                   VALUES (%s, %s, %s, %s, %s)""",
                (tenant_id, tpl['naam'], tpl['onderwerp'],
                 tpl['body_html'], is_actief))


# ─── Contacts ─────────────────────────────────────────────────────────────────

def get_all_contacts(tenant_id):
    with get_connection() as conn:
        cur = _q(conn,
            """SELECT * FROM contacts WHERE tenant_id = %s ORDER BY laatste_mail DESC""",
            (tenant_id,))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def upsert_contact(tenant_id, naam, email, sent_at):
    if not email:
        return
    email = email.lower().strip()
    with get_connection() as conn:
        cur = _q(conn,
            "SELECT id, aantal_mails FROM contacts WHERE tenant_id = %s AND email = %s",
            (tenant_id, email))
        existing = cur.fetchone()
        if existing:
            _q(conn,
                """UPDATE contacts SET naam=%s, laatste_mail=%s, aantal_mails=%s
                   WHERE tenant_id=%s AND email=%s""",
                (naam, sent_at, existing['aantal_mails'] + 1, tenant_id, email))
        else:
            _q(conn,
                """INSERT INTO contacts
                   (tenant_id, naam, email, eerste_mail, laatste_mail, aantal_mails)
                   VALUES (%s, %s, %s, %s, %s, 1)""",
                (tenant_id, naam, email, sent_at, sent_at))


def sync_contacts_from_log(tenant_id):
    with get_connection() as conn:
        cur = _q(conn,
            """SELECT naam, email,
                      MIN(sent_at) as eerste, MAX(sent_at) as laatste, COUNT(*) as aantal
               FROM review_log
               WHERE tenant_id = %s AND status = 'sent'
               GROUP BY email, naam""",
            (tenant_id,))
        rows = cur.fetchall()
        for row in rows:
            try:
                _q(conn,
                    """INSERT INTO contacts
                       (tenant_id, naam, email, eerste_mail, laatste_mail, aantal_mails)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (tenant_id, email) DO NOTHING""",
                    (tenant_id, row['naam'], row['email'].lower(),
                     row['eerste'], row['laatste'], row['aantal']))
            except Exception:
                pass


# ─── Review snapshots ─────────────────────────────────────────────────────────

def record_review_snapshot(tenant_id, total):
    from datetime import date
    with get_connection() as conn:
        _q(conn,
            """INSERT INTO review_snapshots (tenant_id, date, total)
               VALUES (%s, %s, %s)
               ON CONFLICT (tenant_id, date) DO UPDATE SET total = EXCLUDED.total""",
            (tenant_id, date.today(), total))


def get_review_snapshots(tenant_id):
    with get_connection() as conn:
        cur = _q(conn,
            """SELECT date, total FROM review_snapshots
               WHERE tenant_id = %s ORDER BY date""",
            (tenant_id,))
        rows = cur.fetchall()
    return [{'date': str(r['date']), 'total': r['total']} for r in rows]


def get_review_growth(tenant_id, baseline=None):
    snapshots = get_review_snapshots(tenant_id)
    if not snapshots:
        return None
    latest = snapshots[-1]
    if baseline:
        first_total = int(baseline)
        first_date = 'startwaarde'
    else:
        first_total = snapshots[0]['total']
        first_date = snapshots[0]['date']
    return {
        'first_date': first_date,
        'first_total': first_total,
        'latest_total': latest['total'],
        'growth': latest['total'] - first_total,
    }


# ─── Dashboard time-series helpers ───────────────────────────────────────────

VALID_PERIODS = {'7d', '30d', 'year', 'all'}


def _period_cutoff_sql(period):
    """Returns SQL fragment for WHERE clause (no params needed — values are literals)."""
    if period == '7d':
        return "sent_at >= NOW() - interval '7 days'"
    if period == '30d':
        return "sent_at >= NOW() - interval '30 days'"
    if period == 'year':
        return "sent_at >= date_trunc('year', NOW())"
    return None  # 'all' — no filter


def get_sent_series(tenant_id, period='30d'):
    """Returns [{date, count}] grouped by day for the selected period. Always tenant-scoped."""
    cutoff = _period_cutoff_sql(period)
    where = f"tenant_id = %s{f' AND {cutoff}' if cutoff else ''}"
    with get_connection() as conn:
        cur = _q(conn,
            f"""SELECT DATE(sent_at) AS date, COUNT(*) AS count
                FROM review_log
                WHERE {where}
                GROUP BY DATE(sent_at)
                ORDER BY date""",
            (tenant_id,))
        rows = cur.fetchall()
    return [{'date': str(r['date']), 'count': r['count']} for r in rows]


def get_sent_count_for_period(tenant_id, period='30d'):
    """Total sent emails in the current period. Always tenant-scoped."""
    cutoff = _period_cutoff_sql(period)
    where = f"tenant_id = %s{f' AND {cutoff}' if cutoff else ''}"
    with get_connection() as conn:
        cur = _q(conn,
            f"SELECT COUNT(*) AS cnt FROM review_log WHERE {where}",
            (tenant_id,))
        return cur.fetchone()['cnt']


def get_sent_count_prev_period(tenant_id, period='30d'):
    """Total sent emails in the previous equal-length period. Returns None for 'all'."""
    if period == '7d':
        sql = """SELECT COUNT(*) AS cnt FROM review_log WHERE tenant_id = %s
                 AND sent_at >= NOW() - interval '14 days'
                 AND sent_at <  NOW() - interval '7 days'"""
    elif period == '30d':
        sql = """SELECT COUNT(*) AS cnt FROM review_log WHERE tenant_id = %s
                 AND sent_at >= NOW() - interval '60 days'
                 AND sent_at <  NOW() - interval '30 days'"""
    elif period == 'year':
        sql = """SELECT COUNT(*) AS cnt FROM review_log WHERE tenant_id = %s
                 AND sent_at >= date_trunc('year', NOW() - interval '1 year')
                 AND sent_at <  date_trunc('year', NOW())"""
    else:
        return None
    with get_connection() as conn:
        cur = _q(conn, sql, (tenant_id,))
        return cur.fetchone()['cnt']


def get_review_snapshots_for_period(tenant_id, period='30d'):
    """Returns review_snapshots filtered by period. Always tenant-scoped."""
    if period == '7d':
        date_filter = "AND date >= CURRENT_DATE - interval '7 days'"
    elif period == '30d':
        date_filter = "AND date >= CURRENT_DATE - interval '30 days'"
    elif period == 'year':
        date_filter = "AND date >= date_trunc('year', CURRENT_DATE)"
    else:
        date_filter = ''
    with get_connection() as conn:
        cur = _q(conn,
            f"""SELECT date, total FROM review_snapshots
                WHERE tenant_id = %s {date_filter}
                ORDER BY date""",
            (tenant_id,))
        rows = cur.fetchall()
    return [{'date': str(r['date']), 'total': r['total']} for r in rows]


# ─── Dashboard helpers ────────────────────────────────────────────────────────

def get_dashboard_stats(tenant_id):
    with get_connection() as conn:
        cur = _q(conn,
            "SELECT COUNT(*) as total FROM review_log WHERE tenant_id = %s",
            (tenant_id,))
        total = cur.fetchone()['total']

        cur = _q(conn,
            """SELECT COUNT(*) as cnt FROM review_log
               WHERE tenant_id = %s AND sent_at >= date_trunc('month', NOW())""",
            (tenant_id,))
        this_month = cur.fetchone()['cnt']

        cur = _q(conn,
            "SELECT * FROM import_log WHERE tenant_id = %s ORDER BY import_at DESC LIMIT 1",
            (tenant_id,))
        last_run = cur.fetchone()

        cur = _q(conn,
            """SELECT naam, email, sent_at FROM review_log
               WHERE tenant_id = %s ORDER BY sent_at DESC LIMIT 15""",
            (tenant_id,))
        recent = cur.fetchall()

        cur = _q(conn,
            "SELECT COUNT(*) as cnt FROM reviewed_names WHERE tenant_id = %s",
            (tenant_id,))
        reviewed_count = cur.fetchone()['cnt']

        cur = _q(conn,
            "SELECT DATE(MIN(sent_at)) as d FROM review_log WHERE tenant_id = %s",
            (tenant_id,))
        first_sent = cur.fetchone()

    return {
        'total': total,
        'this_month': this_month,
        'last_run': dict(last_run) if last_run else None,
        'recent': [dict(r) for r in recent],
        'reviewed_count': reviewed_count,
        'first_sent_date': str(first_sent['d']) if first_sent and first_sent['d'] else None,
    }
