import sqlite3
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_review_log_email ON review_log(email);
CREATE INDEX IF NOT EXISTS idx_blocked_email ON blocked(email);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reviewed_names_naam ON reviewed_names(naam);
'''


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.executescript(SCHEMA)


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
