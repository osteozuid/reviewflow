import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import (Flask, render_template, request, redirect,
                   url_for, flash, jsonify, send_file)
from werkzeug.utils import secure_filename
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'reviewflow-change-in-prod')
app.jinja_env.globals['enumerate'] = enumerate

INPUT_DIR = ROOT / 'input'
OUTPUT_DIR = ROOT / 'output'
INPUT_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ALLOWED = {'.csv', '.xlsx', '.xls'}

# ─── Run state (in-memory) ────────────────────────────────────────────────────
_run = {'active': False, 'lines': [], 'done': False, 'modus': None, 'counts': {}}
_lock = threading.Lock()


def _log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    with _lock:
        _run['lines'].append(f'[{ts}]  {msg}')


def do_run(modus, test_email=None):
    from db import (init_db, get_already_sent, get_blocked, get_reviewed_names,
                    log_sent, log_import, export_review_log)
    from csv_import import load_all_csv
    from dedup import deduplicate, matches_reviewed
    from mailer import get_smtp_config, send_review_request

    with _lock:
        _run.update({'active': True, 'lines': [], 'done': False,
                     'modus': modus, 'counts': {}})
    try:
        init_db()
        _log('Bestanden laden...')
        try:
            all_rows, load_stats = load_all_csv(INPUT_DIR)
        except FileNotFoundError as e:
            _log(f'Geen bestanden gevonden — upload eerst een CSV of Excel.')
            return

        candidates, skip_stats = deduplicate(all_rows)
        already_sent = get_already_sent()
        blocked = get_blocked()
        reviewed = get_reviewed_names()

        to_mail, skipped = [], []
        for c in candidates:
            if c['email'] in already_sent:
                skipped.append({**c, 'reden': 'Al gemaild'})
            elif c['email'] in blocked:
                skipped.append({**c, 'reden': 'Geblokkeerd'})
            else:
                m = matches_reviewed(c['naam'], reviewed)
                if m:
                    skipped.append({**c, 'reden': f'Al review ({m})'})
                else:
                    to_mail.append(c)

        total_gelezen = sum(s['rijen_gelezen'] for s in load_stats)
        _log(f'{total_gelezen} rijen gelezen uit {len(load_stats)} bestand(en)')
        _log(f'{len(to_mail)} te mailen  ·  {len(skipped)} overgeslagen')

        verzonden, gefaald = [], []

        if modus == 'dry':
            for p in to_mail:
                _log(f'[DRY]  {p["naam"]}  <{p["email"]}>')
            _log('─' * 40)
            _log('DRY RUN klaar — geen mails verstuurd')

        else:
            cfg = get_smtp_config()
            if modus == 'test':
                _log(f'TEST modus — alle mails → {test_email}')
            _log('─' * 40)

            for patient in to_mail:
                try:
                    target = {**patient, 'email': test_email} if modus == 'test' else patient
                    send_review_request(target, cfg)
                    if modus == 'send':
                        log_sent([patient], bestand=patient['bestand'])
                    verzonden.append(patient)
                    _log(f'✓  {patient["naam"]}  <{patient["email"]}>')
                    time.sleep(20)
                except Exception as e:
                    gefaald.append(patient)
                    _log(f'✗  {patient["naam"]}  —  {e}')

            _log('─' * 40)
            if modus == 'send' and verzonden:
                export_review_log(OUTPUT_DIR / 'verzonden.csv')
                _log('verzonden.csv bijgewerkt')

            all_ov = sum(
                s.get('rijen_leeg', 0) + s.get('rijen_fout_type', 0) +
                s.get('rijen_geen_naam', 0) + s.get('rijen_geen_email', 0) +
                s.get('rijen_ongeldig_email', 0) for s in load_stats
            )
            log_import(
                bestand=', '.join(s['bestand'] for s in load_stats),
                rijen_gelezen=total_gelezen,
                rijen_ok=sum(s['rijen_ok'] for s in load_stats),
                unieke_patienten=len(to_mail) + len(skipped),
                kandidaten=len(to_mail),
                gemaild=len(verzonden),
                overgeslagen=all_ov + len(skip_stats.get('dubbel', [])) + len(skipped),
                modus=modus,
            )

        with _lock:
            _run['counts'] = {
                'kandidaten': len(to_mail),
                'overgeslagen': len(skipped),
                'verzonden': len(verzonden),
                'gefaald': len(gefaald),
            }

    except Exception as e:
        _log(f'FOUT: {e}')
    finally:
        with _lock:
            _run['done'] = True
            _run['active'] = False


# ─── Scheduler ────────────────────────────────────────────────────────────────
DAGEN = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
scheduler = BackgroundScheduler(timezone='Europe/Brussels')
scheduler.start()


def reload_schedule():
    from db import get_schedule_config
    if scheduler.get_job('auto'):
        scheduler.remove_job('auto')
    cfg = get_schedule_config()
    if not cfg or not cfg['actief'] or cfg['modus'] == 'manual':
        return
    h, m = cfg['tijdstip'].split(':')
    if cfg['modus'] == 'weekly':
        trigger = CronTrigger(day_of_week=DAGEN[cfg['dag_van_week']],
                              hour=int(h), minute=int(m))
    else:
        trigger = CronTrigger(hour=int(h), minute=int(m))
    scheduler.add_job(lambda: do_run('send'), trigger, id='auto',
                      name='Auto verzending', replace_existing=True)


# ─── Template filter ──────────────────────────────────────────────────────────
@app.template_filter('fmtdt')
def fmtdt(s):
    if not s:
        return '—'
    try:
        return s[:16].replace('T', ' ')
    except Exception:
        return str(s)


# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route('/')
def dashboard():
    from db import init_db, get_connection, get_schedule_config
    init_db()
    with get_connection() as conn:
        total = conn.execute('SELECT COUNT(*) FROM review_log').fetchone()[0]
        this_month = conn.execute(
            "SELECT COUNT(*) FROM review_log WHERE sent_at >= date('now','start of month')"
        ).fetchone()[0]
        last_run = conn.execute(
            'SELECT * FROM import_log ORDER BY import_at DESC LIMIT 1'
        ).fetchone()
        recent = conn.execute(
            'SELECT naam, email, sent_at FROM review_log ORDER BY sent_at DESC LIMIT 15'
        ).fetchall()
        reviewed_count = conn.execute('SELECT COUNT(*) FROM reviewed_names').fetchone()[0]
        input_count = sum(1 for f in INPUT_DIR.iterdir() if f.suffix.lower() in ALLOWED)

    cfg = get_schedule_config()
    job = scheduler.get_job('auto')
    next_run = job.next_run_time.strftime('%a %d/%m %H:%M') if job else None

    return render_template('dashboard.html',
        total=total, this_month=this_month, last_run=last_run,
        recent=recent, reviewed_count=reviewed_count,
        input_count=input_count, next_run=next_run, schedule=cfg,
        page='dashboard')


@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        files = request.files.getlist('files')
        saved = []
        for f in files:
            if f and f.filename and Path(f.filename).suffix.lower() in ALLOWED:
                name = secure_filename(f.filename)
                f.save(INPUT_DIR / name)
                saved.append(name)
        if saved:
            flash(f'{len(saved)} bestand(en) geüpload', 'success')
        else:
            flash('Geen geldige bestanden (.csv, .xlsx)', 'warning')
        return redirect(url_for('upload'))

    raw = sorted(INPUT_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    files = []
    for f in raw:
        if f.suffix.lower() in ALLOWED:
            st = f.stat()
            files.append({
                'name': f.name,
                'size': f'{st.st_size / 1024:.1f} KB',
                'modified': datetime.fromtimestamp(st.st_mtime).strftime('%d/%m/%Y %H:%M'),
            })
    return render_template('upload.html', files=files, page='upload')


@app.route('/upload/delete/<path:filename>')
def delete_file(filename):
    p = INPUT_DIR / secure_filename(filename)
    if p.exists():
        p.unlink()
        flash(f'{p.name} verwijderd', 'info')
    return redirect(url_for('upload'))


@app.route('/run')
def run_page():
    from db import init_db
    init_db()
    admin = os.getenv('ADMIN_EMAIL', '')
    with _lock:
        run_snapshot = dict(_run)
    return render_template('run.html', run=run_snapshot, admin=admin, page='run')


@app.route('/run/start', methods=['POST'])
def run_start():
    modus = request.form.get('modus', 'dry')
    test_email = request.form.get('test_email') or os.getenv('ADMIN_EMAIL', '')
    with _lock:
        if _run['active']:
            flash('Er loopt al een run', 'warning')
            return redirect(url_for('run_page'))
    threading.Thread(target=do_run, args=(modus, test_email), daemon=True).start()
    return redirect(url_for('run_page'))


@app.route('/api/run/status')
def run_status():
    with _lock:
        return jsonify({
            'active': _run['active'],
            'done': _run['done'],
            'lines': _run['lines'][-200:],
            'counts': _run['counts'],
            'modus': _run['modus'],
        })


@app.route('/schedule', methods=['GET', 'POST'])
def schedule():
    from db import get_schedule_config, save_schedule_config
    if request.method == 'POST':
        save_schedule_config({
            'modus': request.form.get('modus', 'manual'),
            'dag_van_week': int(request.form.get('dag_van_week', 0)),
            'tijdstip': request.form.get('tijdstip', '09:00'),
            'actief': 1 if request.form.get('actief') else 0,
        })
        reload_schedule()
        flash('Schema opgeslagen', 'success')
        return redirect(url_for('schedule'))

    cfg = get_schedule_config()
    job = scheduler.get_job('auto')
    next_run = job.next_run_time.strftime('%A %d/%m/%Y om %H:%M') if job else None
    return render_template('schedule.html', cfg=cfg, next_run=next_run, page='schedule')


@app.route('/logs')
def logs():
    from db import init_db, get_connection
    init_db()
    with get_connection() as conn:
        runs = conn.execute(
            'SELECT * FROM import_log ORDER BY import_at DESC LIMIT 30'
        ).fetchall()
        sent = conn.execute(
            'SELECT naam, email, sent_at, bestand FROM review_log ORDER BY sent_at DESC LIMIT 100'
        ).fetchall()
    return render_template('logs.html', runs=runs, sent=sent, page='logs')


@app.route('/logs/export')
def export_log():
    from db import init_db, export_review_log
    init_db()
    path = OUTPUT_DIR / 'review_log_export.csv'
    export_review_log(path)
    return send_file(str(path), as_attachment=True, download_name='review_log.csv')


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    from db import init_db, get_reviewed_names, add_reviewed_name, get_connection
    init_db()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_reviewed':
            naam = request.form.get('naam', '').strip()
            if naam:
                add_reviewed_name(naam)
                flash(f'"{naam}" toegevoegd', 'success')
        elif action == 'delete_reviewed':
            naam = request.form.get('naam', '')
            with get_connection() as conn:
                conn.execute('DELETE FROM reviewed_names WHERE naam = ?', (naam,))
            flash(f'"{naam}" verwijderd', 'info')
        return redirect(url_for('settings'))

    reviewed = get_reviewed_names()
    env = {k: os.getenv(k, '') for k in [
        'SMTP_HOST', 'SMTP_PORT', 'FROM_EMAIL', 'FROM_NAME',
        'ADMIN_EMAIL', 'GOOGLE_REVIEW_LINK',
    ]}
    return render_template('settings.html', reviewed=reviewed, env=env, page='settings')


if __name__ == '__main__':
    from db import init_db
    init_db()
    reload_schedule()
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
