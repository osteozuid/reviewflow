import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime

DB_PATH = Path('data/review_requests.db')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS patients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL UNIQUE,
    naam        TEXT,
    voornaam    TEXT,
    geboortedatum TEXT,
    telefoon    TEXT,
    gsm         TEXT,
    agenda      TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS review_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL,
    naam        TEXT,
    geboortedatum TEXT,
    sent_at     TEXT,
    status      TEXT,
    bestand     TEXT,
    import_batch TEXT
);

CREATE TABLE IF NOT EXISTS blocked (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT,
    naam        TEXT,
    geboortedatum TEXT,
    reden       TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS import_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bestand         TEXT,
    rijen_gelezen   INTEGER,
    rijen_ok        INTEGER,
    unieke_patienten INTEGER,
    kandidaten      INTEGER,
    gemaild         INTEGER,
    overgeslagen    INTEGER,
    modus           TEXT,
    import_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reviewed_names (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    naam        TEXT NOT NULL,
    bron        TEXT DEFAULT 'manual',
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS schedule_config (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    modus       TEXT DEFAULT 'manual',
    dag_van_week INTEGER DEFAULT 0,
    tijdstip    TEXT DEFAULT '09:00',
    actief      INTEGER DEFAULT 0,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS email_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    naam TEXT NOT NULL,
    onderwerp TEXT NOT NULL,
    body_html TEXT NOT NULL,
    is_actief INTEGER DEFAULT 0,
    aangemaakt_op TEXT DEFAULT (datetime('now')),
    gewijzigd_op TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    naam TEXT,
    email TEXT NOT NULL UNIQUE,
    eerste_mail TEXT,
    laatste_mail TEXT,
    aantal_mails INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS app_settings (
    sleutel TEXT PRIMARY KEY,
    waarde TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS review_snapshots (
    date TEXT PRIMARY KEY,
    total INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_review_log_email ON review_log(email);
CREATE INDEX IF NOT EXISTS idx_blocked_email ON blocked(email);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reviewed_names_naam ON reviewed_names(naam);
'''


@contextmanager
def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_connection() as conn:
        conn.executescript(SCHEMA)
    seed_preset_templates()


def record_review_snapshot(total):
    from datetime import date
    with get_connection() as conn:
        conn.execute('INSERT OR REPLACE INTO review_snapshots (date, total) VALUES (?, ?)',
                     (date.today().isoformat(), total))

def get_review_snapshots():
    with get_connection() as conn:
        rows = conn.execute('SELECT date, total FROM review_snapshots ORDER BY date').fetchall()
    return [{'date': r['date'], 'total': r['total']} for r in rows]

def get_review_growth(baseline=None):
    with get_connection() as conn:
        rows = conn.execute('SELECT date, total FROM review_snapshots ORDER BY date').fetchall()
    if not rows:
        return None
    latest = rows[-1]
    if baseline:
        first_total = int(baseline)
        first_date = 'startwaarde'
    else:
        first_total = rows[0]['total']
        first_date = rows[0]['date']
    return {'first_date': first_date, 'first_total': first_total,
            'latest_total': latest['total'], 'growth': latest['total'] - first_total}


def get_already_sent():
    with get_connection() as conn:
        rows = conn.execute('SELECT email FROM review_log').fetchall()
    return {row['email'].lower() for row in rows}


def get_blocked():
    with get_connection() as conn:
        rows = conn.execute(
            'SELECT email FROM blocked WHERE email IS NOT NULL AND email != ""'
        ).fetchall()
    return {row['email'].lower() for row in rows}


def get_blocked_names():
    with get_connection() as conn:
        rows = conn.execute(
            'SELECT naam FROM blocked WHERE naam IS NOT NULL AND naam != ""'
        ).fetchall()
    return [row['naam'] for row in rows]


def get_blocked_list():
    with get_connection() as conn:
        rows = conn.execute(
            'SELECT id, naam, email, reden, created_at FROM blocked ORDER BY created_at DESC'
        ).fetchall()
    return [dict(r) for r in rows]


def add_blocked_person(naam, email='', reden=''):
    with get_connection() as conn:
        conn.execute(
            'INSERT INTO blocked (naam, email, reden) VALUES (?, ?, ?)',
            (naam.strip(), email.strip() or None, reden.strip() or None),
        )


def delete_blocked_person(blocked_id):
    with get_connection() as conn:
        conn.execute('DELETE FROM blocked WHERE id = ?', (blocked_id,))


def log_sent(candidates, bestand, batch_id=None):
    now = datetime.now().isoformat(timespec='seconds')
    with get_connection() as conn:
        for c in candidates:
            try:
                conn.execute(
                    '''INSERT INTO review_log
                       (email, naam, geboortedatum, sent_at, status, bestand, import_batch)
                       VALUES (?, ?, ?, ?, 'sent', ?, ?)''',
                    (c['email'], c['naam'], c['geboortedatum'], now, bestand, batch_id),
                )
            except sqlite3.IntegrityError:
                pass
    for c in candidates:
        upsert_contact(c.get('naam', ''), c['email'], now)


def log_import(bestand, rijen_gelezen, rijen_ok, unieke_patienten,
               kandidaten, gemaild, overgeslagen, modus):
    now = datetime.now().isoformat(timespec='seconds')
    with get_connection() as conn:
        conn.execute(
            '''INSERT INTO import_log
               (bestand, rijen_gelezen, rijen_ok, unieke_patienten,
                kandidaten, gemaild, overgeslagen, modus, import_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (bestand, rijen_gelezen, rijen_ok, unieke_patienten,
             kandidaten, gemaild, overgeslagen, modus, now),
        )


def add_reviewed_name(naam, bron='manual'):
    with get_connection() as conn:
        try:
            conn.execute(
                'INSERT INTO reviewed_names (naam, bron) VALUES (?, ?)',
                (naam.strip(), bron),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def delete_reviewed_name(naam):
    with get_connection() as conn:
        conn.execute('DELETE FROM reviewed_names WHERE naam = ?', (naam,))


def get_reviewed_names_full():
    with get_connection() as conn:
        rows = conn.execute(
            'SELECT naam, bron, created_at FROM reviewed_names ORDER BY naam'
        ).fetchall()
    return [dict(r) for r in rows]


def get_reviewed_names():
    with get_connection() as conn:
        rows = conn.execute('SELECT naam FROM reviewed_names ORDER BY naam').fetchall()
    return [row['naam'] for row in rows]


def get_schedule_config():
    with get_connection() as conn:
        row = conn.execute('SELECT * FROM schedule_config WHERE id=1').fetchone()
    if row:
        return dict(row)
    return {'modus': 'manual', 'dag_van_week': 0, 'tijdstip': '09:00', 'actief': 0}


def save_schedule_config(cfg):
    now = datetime.now().isoformat(timespec='seconds')
    with get_connection() as conn:
        conn.execute(
            '''INSERT OR REPLACE INTO schedule_config
               (id, modus, dag_van_week, tijdstip, actief, updated_at)
               VALUES (1, ?, ?, ?, ?, ?)''',
            (cfg['modus'], cfg['dag_van_week'], cfg['tijdstip'], cfg['actief'], now),
        )


def export_review_log(output_path):
    import csv
    with get_connection() as conn:
        rows = conn.execute(
            'SELECT email, naam, geboortedatum, sent_at, status, bestand FROM review_log ORDER BY sent_at'
        ).fetchall()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(['email', 'naam', 'geboortedatum', 'sent_at', 'status', 'bestand'])
        for row in rows:
            writer.writerow(list(row))
    return len(rows)


# ─── Email Templates ──────────────────────────────────────────────────────────

def get_all_templates():
    with get_connection() as conn:
        rows = conn.execute(
            'SELECT * FROM email_templates ORDER BY aangemaakt_op DESC'
        ).fetchall()
    return [dict(r) for r in rows]


def get_active_template():
    with get_connection() as conn:
        row = conn.execute(
            'SELECT * FROM email_templates WHERE is_actief = 1 LIMIT 1'
        ).fetchone()
    return dict(row) if row else None


def get_template(id):
    with get_connection() as conn:
        row = conn.execute(
            'SELECT * FROM email_templates WHERE id = ?', (id,)
        ).fetchone()
    return dict(row) if row else None


def save_template(naam, onderwerp, body_html, id=None):
    now = datetime.now().isoformat(timespec='seconds')
    with get_connection() as conn:
        if id:
            conn.execute(
                '''UPDATE email_templates
                   SET naam=?, onderwerp=?, body_html=?, gewijzigd_op=?
                   WHERE id=?''',
                (naam, onderwerp, body_html, now, id),
            )
        else:
            conn.execute(
                '''INSERT INTO email_templates (naam, onderwerp, body_html, aangemaakt_op)
                   VALUES (?, ?, ?, ?)''',
                (naam, onderwerp, body_html, now),
            )


def delete_template(id):
    tpl = get_template(id)
    if not tpl:
        return False, 'Template niet gevonden'
    if tpl['is_actief']:
        return False, 'Kan actief template niet verwijderen'
    with get_connection() as conn:
        conn.execute('DELETE FROM email_templates WHERE id=?', (id,))
    return True, 'Verwijderd'


def set_active_template(id):
    with get_connection() as conn:
        conn.execute('UPDATE email_templates SET is_actief = 0')
        conn.execute('UPDATE email_templates SET is_actief = 1 WHERE id=?', (id,))


def seed_preset_templates():
    with get_connection() as conn:
        count = conn.execute('SELECT COUNT(*) FROM email_templates').fetchone()[0]
    if count > 0:
        return

    template_vriendelijk = '''<!DOCTYPE html>
<html lang="nl">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#ffffff;font-family:Calibri,Candara,'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;">
  <tr><td style="max-width:600px;margin:0 auto;padding:32px 24px;">
    <p style="font-size:15px;line-height:1.7;color:#1a1a1a;margin:0 0 16px 0;">Dag {{voornaam}},</p>
    <p style="font-size:15px;line-height:1.7;color:#1a1a1a;margin:0 0 16px 0;">Hartelijk bedankt voor uw bezoek aan Osteozuid. We hopen dat u zich goed voelt en dat we u goed hebben kunnen helpen.</p>
    <p style="font-size:15px;line-height:1.7;color:#1a1a1a;margin:0 0 16px 0;">Als u een momentje heeft, zouden we het erg waarderen als u uw ervaring wilt delen via Google. Uw review helpt andere mensen om Osteozuid te vinden en een weloverwogen keuze te maken.</p>
    <p style="margin:24px 0;">
      <a href="{{google_link}}" style="background:#f28c00;color:#ffffff;text-decoration:none;padding:12px 28px;border-radius:4px;font-size:14px;font-weight:600;display:inline-block;">Schrijf uw Google review</a>
    </p>
    <p style="font-size:15px;line-height:1.7;color:#1a1a1a;margin:0 0 16px 0;">Heeft u opmerkingen of vragen? Antwoord dan gerust op deze mail — we lezen alles.</p>
    <p style="font-size:15px;line-height:1.7;color:#1a1a1a;margin:0;">Vriendelijke groeten,<br><strong>Osteozuid Groepspraktijk</strong></p>
  </td></tr>
</table>
</body>
</html>'''

    template_professioneel = '''<!DOCTYPE html>
<html lang="nl">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#ffffff;font-family:Calibri,Candara,'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;">
  <tr><td style="max-width:600px;margin:0 auto;padding:32px 24px;">
    <p style="font-size:15px;line-height:1.7;color:#1a1a1a;margin:0 0 16px 0;">Geachte {{voornaam}},</p>
    <p style="font-size:15px;line-height:1.7;color:#1a1a1a;margin:0 0 16px 0;">Bedankt voor uw bezoek aan onze praktijk. Wij streven ernaar elke patiënt de best mogelijke zorg te bieden en hopen dat u tevreden bent over uw behandeling.</p>
    <p style="font-size:15px;line-height:1.7;color:#1a1a1a;margin:0 0 16px 0;">Om anderen te helpen een geschikte zorgverlener te vinden, vragen wij u vriendelijk een korte beoordeling achter te laten via Google Reviews. Dit neemt slechts een minuut in beslag.</p>
    <p style="margin:24px 0;">
      <a href="{{google_link}}" style="background:#f28c00;color:#ffffff;text-decoration:none;padding:12px 28px;border-radius:4px;font-size:14px;font-weight:600;display:inline-block;">Beoordeling achterlaten</a>
    </p>
    <p style="font-size:15px;line-height:1.7;color:#1a1a1a;margin:0 0 16px 0;">Voor vragen of opmerkingen kunt u altijd contact met ons opnemen.</p>
    <p style="font-size:15px;line-height:1.7;color:#1a1a1a;margin:0;">Met vriendelijke groeten,<br><strong>Osteozuid Groepspraktijk</strong></p>
  </td></tr>
</table>
</body>
</html>'''

    template_kort = '''<!DOCTYPE html>
<html lang="nl">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#ffffff;font-family:Calibri,Candara,'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;">
  <tr><td style="max-width:600px;margin:0 auto;padding:32px 24px;">
    <p style="font-size:15px;line-height:1.7;color:#1a1a1a;margin:0 0 16px 0;">Dag {{voornaam}},</p>
    <p style="font-size:15px;line-height:1.7;color:#1a1a1a;margin:0 0 16px 0;">Bedankt voor uw bezoek aan Osteozuid!</p>
    <p style="font-size:15px;line-height:1.7;color:#1a1a1a;margin:0 0 20px 0;">Had u een goede ervaring? Deel het in 1 minuut via Google:</p>
    <p style="margin:0 0 24px 0;">
      <a href="{{google_link}}" style="background:#f28c00;color:#ffffff;text-decoration:none;padding:12px 28px;border-radius:4px;font-size:14px;font-weight:600;display:inline-block;">Review schrijven</a>
    </p>
    <p style="font-size:13px;line-height:1.6;color:#888888;margin:0;">Vragen? Antwoord gewoon op deze mail.<br><strong>Osteozuid Groepspraktijk</strong></p>
  </td></tr>
</table>
</body>
</html>'''

    now = datetime.now().isoformat(timespec='seconds')
    with get_connection() as conn:
        conn.execute(
            '''INSERT INTO email_templates (naam, onderwerp, body_html, is_actief, aangemaakt_op)
               VALUES (?, ?, ?, 1, ?)''',
            ('Vriendelijk', 'Uw ervaring bij Osteozuid', template_vriendelijk, now),
        )
        conn.execute(
            '''INSERT INTO email_templates (naam, onderwerp, body_html, is_actief, aangemaakt_op)
               VALUES (?, ?, ?, 0, ?)''',
            ('Professioneel', 'Bedankt voor uw bezoek — deel uw ervaring', template_professioneel, now),
        )
        conn.execute(
            '''INSERT INTO email_templates (naam, onderwerp, body_html, is_actief, aangemaakt_op)
               VALUES (?, ?, ?, 0, ?)''',
            ('Kort & Krachtig', '1 minuut voor uw mening?', template_kort, now),
        )


# ─── Contacts ─────────────────────────────────────────────────────────────────

def get_all_contacts():
    with get_connection() as conn:
        rows = conn.execute(
            'SELECT * FROM contacts ORDER BY laatste_mail DESC'
        ).fetchall()
    return [dict(r) for r in rows]


def delete_contact(email):
    email = email.lower().strip()
    with get_connection() as conn:
        conn.execute('DELETE FROM contacts WHERE lower(email) = ?', (email,))
        conn.execute('DELETE FROM review_log WHERE lower(email) = ?', (email,))
        conn.commit()


def upsert_contact(naam, email, sent_at):
    if not email:
        return
    email = email.lower().strip()
    with get_connection() as conn:
        existing = conn.execute(
            'SELECT id, aantal_mails, eerste_mail FROM contacts WHERE email = ?', (email,)
        ).fetchone()
        if existing:
            conn.execute(
                '''UPDATE contacts SET naam=?, laatste_mail=?, aantal_mails=?
                   WHERE email=?''',
                (naam, sent_at, existing['aantal_mails'] + 1, email),
            )
        else:
            conn.execute(
                '''INSERT INTO contacts (naam, email, eerste_mail, laatste_mail, aantal_mails)
                   VALUES (?, ?, ?, ?, 1)''',
                (naam, email, sent_at, sent_at),
            )


def sync_contacts_from_log():
    with get_connection() as conn:
        rows = conn.execute(
            '''SELECT naam, email, MIN(sent_at) as eerste, MAX(sent_at) as laatste, COUNT(*) as aantal
               FROM review_log
               WHERE status = 'sent'
               GROUP BY email'''
        ).fetchall()
        for row in rows:
            existing = conn.execute(
                'SELECT id FROM contacts WHERE email = ?', (row['email'].lower(),)
            ).fetchone()
            if not existing:
                conn.execute(
                    '''INSERT OR IGNORE INTO contacts (naam, email, eerste_mail, laatste_mail, aantal_mails)
                       VALUES (?, ?, ?, ?, ?)''',
                    (row['naam'], row['email'].lower(), row['eerste'], row['laatste'], row['aantal']),
                )


# ─── App Settings ─────────────────────────────────────────────────────────────

def get_app_setting(sleutel, default=''):
    with get_connection() as conn:
        row = conn.execute(
            'SELECT waarde FROM app_settings WHERE sleutel = ?', (sleutel,)
        ).fetchone()
    if row and row['waarde'] is not None:
        return row['waarde']
    return default


def save_app_setting(sleutel, waarde):
    now = datetime.now().isoformat(timespec='seconds')
    with get_connection() as conn:
        conn.execute(
            '''INSERT INTO app_settings (sleutel, waarde, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(sleutel) DO UPDATE SET waarde=excluded.waarde, updated_at=excluded.updated_at''',
            (sleutel, waarde, now),
        )


def save_app_settings(settings_dict):
    for sleutel, waarde in settings_dict.items():
        save_app_setting(sleutel, waarde)


def get_all_app_settings():
    with get_connection() as conn:
        rows = conn.execute('SELECT sleutel, waarde FROM app_settings').fetchall()
    return {row['sleutel']: row['waarde'] for row in rows}
