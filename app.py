import io
import os
import re as _re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_TZ = ZoneInfo('Europe/Brussels')

from functools import wraps

from flask import (Flask, render_template, request, redirect,
                   url_for, flash, jsonify, send_file, Response, session)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'reviewflow-change-in-prod')
app.jinja_env.globals['enumerate'] = enumerate

INPUT_DIR  = ROOT / 'input'
OUTPUT_DIR = ROOT / 'output'
UPLOAD_DIR = ROOT / 'static' / 'uploads'
INPUT_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED = {'.csv', '.xlsx', '.xls'}

# ─── Run state (in-memory) ────────────────────────────────────────────────────
_run = {'active': False, 'lines': [], 'done': False, 'modus': None, 'counts': {}}
_lock = threading.Lock()


def _log(msg):
    ts = datetime.now(_TZ).strftime('%H:%M:%S')
    with _lock:
        _run['lines'].append(f'[{ts}]  {msg}')


def do_run(modus, test_email=None):
    from db import (init_db, get_already_sent, get_blocked, get_blocked_names,
                    get_reviewed_names, log_sent, log_import, export_review_log,
                    get_app_setting, get_active_template)
    from csv_import import load_all_csv
    from dedup import deduplicate, matches_reviewed
    from mailer import get_smtp_config, send_review_request

    with _lock:
        _run.update({'active': True, 'lines': [], 'done': False,
                     'modus': modus, 'counts': {}})
    try:
        init_db()

        # Check if real send is blocked
        if modus == 'send':
            geblokkeerd = get_app_setting('send_geblokkeerd', '0')
            if geblokkeerd == '1':
                _log('GEBLOKKEERD: Versturen is uitgeschakeld in Instellingen (testmodus actief).')
                _log('Zet "Versturen geblokkeerd" uit in Instellingen om echte mails te sturen.')
                return

        _log('Bestanden laden...')
        try:
            all_rows, load_stats = load_all_csv(INPUT_DIR)
        except FileNotFoundError:
            _log('Geen bestanden gevonden — upload eerst een CSV of Excel.')
            return

        candidates, skip_stats = deduplicate(all_rows)
        already_sent = get_already_sent()
        blocked_emails = get_blocked()
        blocked_names = get_blocked_names()
        reviewed = get_reviewed_names()

        to_mail, skipped = [], []
        for c in candidates:
            if c['email'] in already_sent:
                skipped.append({**c, 'reden': 'Al gemaild'})
            elif c['email'] in blocked_emails:
                skipped.append({**c, 'reden': 'Geblokkeerd'})
            elif matches_reviewed(c['naam'], blocked_names):
                skipped.append({**c, 'reden': 'Niet mailen'})
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
            active_template = get_active_template()
            if active_template:
                _log(f'Template: "{active_template["naam"]}"')

            SEND_LIMIT = 30
            if modus == 'send' and len(to_mail) > SEND_LIMIT:
                _log(f'⚠  Limiet: max {SEND_LIMIT} mails per run — '
                     f'{len(to_mail) - SEND_LIMIT} patiënten worden overgeslagen.')
                to_mail = to_mail[:SEND_LIMIT]

            _log('─' * 40)

            for patient in to_mail:
                try:
                    target = {**patient, 'email': test_email} if modus == 'test' else patient
                    send_review_request(target, cfg, template=active_template)
                    if modus == 'send':
                        log_sent([patient], bestand=patient['bestand'])
                    verzonden.append(patient)
                    _log(f'✓  {patient["naam"]}  <{patient["email"]}>')
                    time.sleep(20)
                except Exception as e:
                    gefaald.append(patient)
                    err_str = str(e)
                    if '451' in err_str:
                        friendly = 'Tijdelijke SMTP-limiet. Wacht 10 minuten en probeer opnieuw.'
                    elif '535' in err_str or 'Authentication' in err_str:
                        friendly = 'SMTP login fout. Controleer gebruikersnaam/wachtwoord.'
                    else:
                        friendly = err_str
                    _log(f'✗  {patient["naam"]}  —  {friendly}')

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


# ─── Auth ────────────────────────────────────────────────────────────────────
@app.before_request
def require_login():
    open_endpoints = {'login', 'logout', 'static'}
    if request.endpoint not in open_endpoints and not session.get('logged_in'):
        return redirect(url_for('login', next=request.path))


def _check_password(entered, stored):
    """Verify password — supports both hashed (werkzeug) and plain text (legacy)."""
    if stored.startswith('pbkdf2:') or stored.startswith('scrypt:'):
        return check_password_hash(stored, entered)
    return entered == stored


@app.route('/login', methods=['GET', 'POST'])
def login():
    from db import get_app_setting
    if session.get('logged_in'):
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        password = request.form.get('password', '')
        stored = get_app_setting('app_password') or os.getenv('APP_PASSWORD', 'reviewflow2024')
        if _check_password(password, stored):
            session['logged_in'] = True
            return redirect(request.args.get('next') or url_for('dashboard'))
        flash('Verkeerd wachtwoord', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ─── Template filter ──────────────────────────────────────────────────────────
@app.template_filter('fmtdt')
def fmtdt(s):
    if not s:
        return '—'
    try:
        return s[:16].replace('T', ' ')
    except Exception:
        return str(s)

@app.template_filter('initials')
def initials(s):
    if not s:
        return '?'
    parts = s.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return s[0].upper() if s else '?'


# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route('/')
def dashboard():
    from db import init_db, get_connection, get_schedule_config, sync_contacts_from_log
    init_db()
    sync_contacts_from_log()
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
    from db import get_review_growth, get_app_setting, get_review_snapshots, get_connection
    review_baseline = get_app_setting('review_baseline') or None
    review_growth = get_review_growth(baseline=review_baseline)
    review_snapshots = get_review_snapshots()
    # Prepend a synthetic start point at the baseline value on the date of the first sent mail
    if review_baseline:
        with get_connection() as _conn:
            _first = _conn.execute("SELECT DATE(MIN(sent_at)) as d FROM review_log").fetchone()
        start_date = _first['d'] if _first and _first['d'] else None
        if start_date and (not review_snapshots or review_snapshots[0]['date'] > start_date):
            review_snapshots = [{'date': start_date, 'total': int(review_baseline)}] + review_snapshots

    return render_template('dashboard.html',
        total=total, this_month=this_month, last_run=last_run,
        recent=recent, reviewed_count=reviewed_count,
        input_count=input_count, next_run=next_run, schedule=cfg,
        review_growth=review_growth,
        review_snapshots=review_snapshots,
        review_baseline=review_baseline,
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


@app.route('/upload/logo', methods=['POST'])
def upload_logo():
    from db import save_app_settings, get_app_setting
    if request.form.get('action') == 'delete_logo':
        logo_url = get_app_setting('logo_url', '')
        if logo_url:
            logo_path = ROOT / logo_url.lstrip('/')
            if logo_path.exists():
                logo_path.unlink()
        save_app_settings({'logo_url': ''})
        flash('Logo verwijderd', 'success')
        return redirect(url_for('settings'))
    f = request.files.get('logo')
    if not f or not f.filename:
        flash('Geen bestand geselecteerd', 'warning')
        return redirect(url_for('settings'))
    ext = Path(f.filename).suffix.lower().lstrip('.')
    if ext not in {'png', 'jpg', 'jpeg', 'svg', 'gif', 'webp'}:
        flash('Ongeldig bestandstype — gebruik PNG, JPG of SVG', 'warning')
        return redirect(url_for('settings'))
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    f.save(UPLOAD_DIR / f'logo.{ext}')
    save_app_settings({'logo_url': f'/static/uploads/logo.{ext}'})
    flash('Logo opgeslagen ✓', 'success')
    return redirect(url_for('settings'))


@app.route('/run')
def run_page():
    from db import init_db, get_app_setting
    init_db()
    admin = get_app_setting('admin_email') or os.getenv('ADMIN_EMAIL', '')
    send_geblokkeerd = get_app_setting('send_geblokkeerd', '0') == '1'
    with _lock:
        run_snapshot = dict(_run)
    return render_template('run.html', run=run_snapshot, admin=admin,
                           send_geblokkeerd=send_geblokkeerd, page='run')


@app.route('/run/test-one', methods=['POST'])
def run_test_one():
    from db import (get_already_sent, get_blocked, get_blocked_names,
                    get_reviewed_names, get_app_setting, get_active_template)
    from csv_import import load_all_csv
    from dedup import deduplicate, matches_reviewed
    from mailer import get_smtp_config, _render_template, _send

    try:
        all_rows, _ = load_all_csv(INPUT_DIR)
    except FileNotFoundError:
        flash('Geen bestanden gevonden — upload eerst een CSV of Excel.', 'warning')
        return redirect(url_for('run_page'))
    except Exception as e:
        flash(f'Fout bij laden bestanden: {e}', 'error')
        return redirect(url_for('run_page'))

    candidates, _ = deduplicate(all_rows)
    already_sent   = get_already_sent()
    blocked_emails = get_blocked()
    blocked_names  = get_blocked_names()
    reviewed       = get_reviewed_names()

    candidate = None
    for c in candidates:
        if c['email'] in already_sent:
            continue
        if c['email'] in blocked_emails:
            continue
        if matches_reviewed(c['naam'], blocked_names):
            continue
        if matches_reviewed(c['naam'], reviewed):
            continue
        candidate = c
        break

    if not candidate:
        flash('Geen kandidaat gevonden om testmail mee te maken.', 'warning')
        return redirect(url_for('run_page'))

    try:
        cfg = get_smtp_config()
    except ValueError as e:
        flash(f'SMTP niet geconfigureerd: {e}', 'error')
        return redirect(url_for('run_page'))

    admin_email = cfg.get('admin_email') or ''
    if not admin_email:
        flash('Geen admin e-mailadres ingesteld in Instellingen.', 'warning')
        return redirect(url_for('run_page'))

    active_template = get_active_template()
    voornaam    = candidate.get('voornaam') or candidate['naam'].split()[-1]
    review_link = cfg.get('google_review_link', '') or 'https://maps.google.com/'
    logo_url    = cfg.get('logo_url', '')

    test_banner = (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:16px;">'
        '<tr><td style="background:#fff3cd;border:2px solid #ffc107;padding:12px 16px;'
        'border-radius:4px;font-family:Arial,sans-serif;font-size:13px;color:#856404;">'
        '&#9888; <strong>TESTMAIL</strong> &mdash; deze mail werd niet naar de pati&euml;nt verstuurd. '
        f'Voorbeelddata van: <strong>{candidate["naam"]}</strong>'
        ' &lt;' + candidate['email'] + '&gt;'
        '</td></tr></table>'
    )

    if active_template:
        subject   = f"[TEST] {active_template['onderwerp']}"
        html_body = _render_template(active_template['body_html'], voornaam, review_link, logo_url)
        if '<body' in html_body:
            html_body = _re.sub(r'(<body[^>]*>)', r'\1' + test_banner, html_body, count=1)
        else:
            html_body = test_banner + html_body
    else:
        subject   = '[TEST] Review request — Osteozuid'
        html_body = (test_banner +
                     f'<p style="font-family:Arial,sans-serif;font-size:15px;">'
                     f'Dag {voornaam}, dit is een testmail. Geen actieve template ingesteld.</p>')

    try:
        _send(admin_email, voornaam, review_link, cfg, subject=subject, html_body=html_body)
        flash(
            f'Testmail verstuurd naar {admin_email} met voorbeelddata van '
            f'{candidate["naam"]} <{candidate["email"]}>',
            'success'
        )
    except Exception as e:
        err_str = str(e)
        if '451' in err_str:
            msg = 'Tijdelijke SMTP-limiet. Wacht 10 minuten en probeer opnieuw.'
        elif '535' in err_str or 'Authentication' in err_str:
            msg = 'SMTP login fout. Controleer gebruikersnaam/wachtwoord.'
        else:
            msg = err_str
        flash(f'Fout bij testmail: {msg}', 'error')

    return redirect(url_for('run_page'))


@app.route('/run/preview')
def run_preview():
    from db import (get_already_sent, get_blocked, get_blocked_names,
                    get_reviewed_names, get_app_setting, get_active_template)
    from csv_import import load_all_csv
    from dedup import deduplicate, matches_reviewed
    from mailer import get_smtp_config

    admin            = get_app_setting('admin_email') or ''
    send_geblokkeerd = get_app_setting('send_geblokkeerd', '0') == '1'
    praktijknaam     = get_app_setting('from_name') or 'Osteozuid'
    SEND_LIMIT       = 30
    error            = None
    preview          = None

    try:
        all_rows, load_stats = load_all_csv(INPUT_DIR)
        candidates, _ = deduplicate(all_rows)
        already_sent   = get_already_sent()
        blocked_emails = get_blocked()
        blocked_names  = get_blocked_names()
        reviewed       = get_reviewed_names()

        to_mail, skipped = [], []
        for c in candidates:
            if c['email'] in already_sent:
                skipped.append({**c, 'reden': 'Al gemaild'})
            elif c['email'] in blocked_emails:
                skipped.append({**c, 'reden': 'Geblokkeerd'})
            elif matches_reviewed(c['naam'], blocked_names):
                skipped.append({**c, 'reden': 'Niet mailen'})
            else:
                m = matches_reviewed(c['naam'], reviewed)
                if m:
                    skipped.append({**c, 'reden': f'Al review ({m})'})
                else:
                    to_mail.append(c)

        active_template = get_active_template()
        try:
            smtp_cfg = get_smtp_config()
            smtp_ok  = True
        except Exception:
            smtp_cfg = {}
            smtp_ok  = False

        capped = len(to_mail) > SEND_LIMIT
        preview = {
            'total_gelezen':  sum(s['rijen_gelezen'] for s in load_stats),
            'bestanden':      len(load_stats),
            'to_mail':        to_mail,
            'to_mail_sample': to_mail[:10],
            'skipped':        skipped,
            'capped':         capped,
            'send_limit':     SEND_LIMIT,
            'template':       active_template,
            'smtp_ok':        smtp_ok,
            'smtp_cfg':       smtp_cfg,
        }
    except FileNotFoundError:
        error = 'Geen bestanden gevonden — upload eerst een CSV of Excel.'
    except Exception as e:
        error = str(e)

    return render_template('run_confirm.html',
        preview=preview, error=error, admin=admin,
        send_geblokkeerd=send_geblokkeerd,
        praktijknaam=praktijknaam, page='run')


@app.route('/run/start', methods=['POST'])
def run_start():
    from db import get_app_setting
    modus = request.form.get('modus', 'dry')
    admin_email = get_app_setting('admin_email') or os.getenv('ADMIN_EMAIL', '')
    test_email = request.form.get('test_email') or admin_email

    if modus == 'send' and not request.form.get('confirmed'):
        return redirect(url_for('run_preview'))

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
    from db import (init_db, get_reviewed_names, add_reviewed_name, get_connection,
                    get_all_app_settings, save_app_settings, get_app_setting)
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
        elif action == 'save_settings':
            fields = {
                'smtp_host':          request.form.get('smtp_host', '').strip(),
                'smtp_port':          request.form.get('smtp_port', '587').strip(),
                'smtp_user':          request.form.get('smtp_user', '').strip(),
                'smtp_password':      request.form.get('smtp_password', '').strip(),
                'from_email':         request.form.get('from_email', '').strip(),
                'from_name':          request.form.get('from_name', '').strip(),
                'admin_email':        request.form.get('admin_email', '').strip(),
                'google_review_link': request.form.get('google_review_link', '').strip(),
                'send_geblokkeerd':   '1' if request.form.get('send_geblokkeerd') else '0',
                'myorganizer_client_id':     request.form.get('myorganizer_client_id', '').strip(),
                'myorganizer_client_secret': request.form.get('myorganizer_client_secret', '').strip(),
                'myorganizer_tenant_id':     request.form.get('myorganizer_tenant_id', '').strip(),
                'google_places_api_key':     request.form.get('google_places_api_key', '').strip(),
                'google_place_id':           request.form.get('google_place_id', '').strip(),
                'review_baseline':           request.form.get('review_baseline', '').strip(),
            }
            # logo_url is managed separately via /upload/logo — preserve existing value
            fields['logo_url'] = get_app_setting('logo_url', '') or ''
            # Don't overwrite smtp_password if blank
            if not fields['smtp_password']:
                fields['smtp_password'] = get_app_setting('smtp_password', '')
            # App password change — Google-stijl: huidig + nieuw 2x
            current_pw = request.form.get('app_password_current', '').strip()
            new_pw     = request.form.get('app_password_new', '').strip()
            confirm_pw = request.form.get('app_password_confirm', '').strip()
            if new_pw or current_pw:
                stored = get_app_setting('app_password') or os.getenv('APP_PASSWORD', 'reviewflow2024')
                if not _check_password(current_pw, stored):
                    flash('Huidig wachtwoord klopt niet', 'error')
                elif len(new_pw) < 8:
                    flash('Nieuw wachtwoord moet minstens 8 tekens zijn', 'error')
                elif new_pw != confirm_pw:
                    flash('Nieuwe wachtwoorden komen niet overeen', 'error')
                else:
                    fields['app_password'] = generate_password_hash(new_pw)
                    flash('Wachtwoord gewijzigd ✓', 'success')
            save_app_settings(fields)
            # Also write SMTP settings back to .env file
            _update_env_file(fields)
            flash('Instellingen opgeslagen', 'success')
        return redirect(url_for('settings'))

    reviewed = get_reviewed_names()
    db_settings = get_all_app_settings()
    # Merge: DB values take priority, fall back to .env
    env_defaults = {
        'smtp_host':          os.getenv('SMTP_HOST', 'smtp.gmail.com'),
        'smtp_port':          os.getenv('SMTP_PORT', '587'),
        'smtp_user':          os.getenv('SMTP_USER', ''),
        'smtp_password':      os.getenv('SMTP_PASSWORD', ''),
        'from_email':         os.getenv('FROM_EMAIL', ''),
        'from_name':          os.getenv('FROM_NAME', 'Osteozuid'),
        'admin_email':        os.getenv('ADMIN_EMAIL', ''),
        'google_review_link': os.getenv('GOOGLE_REVIEW_LINK', ''),
        'send_geblokkeerd':   '0',
        'myorganizer_client_id':     '',
        'myorganizer_client_secret': '',
        'myorganizer_tenant_id':     '',
        'google_places_api_key':     '',
        'google_place_id':           os.getenv('GOOGLE_PLACE_ID', ''),
        'logo_url':                  '',
        'review_baseline':           '',
    }
    cfg = {k: db_settings.get(k) or env_defaults.get(k, '') for k in env_defaults}
    return render_template('settings.html', reviewed=reviewed, cfg=cfg, page='settings')


def _update_env_file(fields):
    """Write SMTP-related settings back to .env file."""
    env_path = ROOT / '.env'
    env_map = {
        'SMTP_HOST':          fields.get('smtp_host', ''),
        'SMTP_PORT':          fields.get('smtp_port', '587'),
        'SMTP_USER':          fields.get('smtp_user', ''),
        'SMTP_PASSWORD':      fields.get('smtp_password', ''),
        'FROM_EMAIL':         fields.get('from_email', ''),
        'FROM_NAME':          fields.get('from_name', ''),
        'ADMIN_EMAIL':        fields.get('admin_email', ''),
        'GOOGLE_REVIEW_LINK': fields.get('google_review_link', ''),
    }
    # Read existing .env if present
    existing = {}
    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, _, v = line.partition('=')
                existing[k.strip()] = v.strip()
    existing.update(env_map)
    lines = [f'{k}={v}' for k, v in existing.items()]
    env_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


# ─── Template routes ──────────────────────────────────────────────────────────

@app.route('/templates')
def templates_list():
    from db import init_db, get_all_templates
    init_db()
    templates = get_all_templates()
    return render_template('templates_list.html', templates=templates, page='templates')




@app.route('/templates/new')
def template_new():
    from db import get_app_setting
    return render_template('template_editor.html', template=None, page='templates',
                           logo_url=get_app_setting('logo_url') or '',
                           admin_email=get_app_setting('admin_email') or '')


@app.route('/templates/save', methods=['POST'])
def template_save():
    from db import save_template
    naam      = request.form.get('naam', '').strip()
    onderwerp = request.form.get('onderwerp', '').strip()
    body_html = request.form.get('body_html', '')
    tpl_id    = request.form.get('id') or None
    if not naam or not onderwerp:
        flash('Naam en onderwerp zijn verplicht', 'warning')
        return redirect(url_for('templates_list'))
    save_template(naam, onderwerp, body_html, id=tpl_id)
    flash('Template opgeslagen', 'success')
    return redirect(url_for('templates_list'))


@app.route('/templates/<int:id>/edit')
def template_edit(id):
    from db import init_db, get_template
    init_db()
    tpl = get_template(id)
    if not tpl:
        flash('Template niet gevonden', 'warning')
        return redirect(url_for('templates_list'))
    from db import get_app_setting
    return render_template('template_editor.html', template=tpl, page='templates',
                           logo_url=get_app_setting('logo_url') or '',
                           admin_email=get_app_setting('admin_email') or '')


@app.route('/templates/<int:id>/delete', methods=['POST'])
def template_delete(id):
    from db import delete_template
    ok, msg = delete_template(id)
    flash(msg, 'success' if ok else 'warning')
    return redirect(url_for('templates_list'))


@app.route('/templates/<int:id>/test-mail', methods=['POST'])
def template_test_mail(id):
    from db import get_template
    from mailer import get_smtp_config, _render_template, _send
    test_email = request.form.get('test_email', '').strip()
    if not test_email:
        return jsonify({'ok': False, 'msg': 'Geen e-mailadres opgegeven'})
    tpl = get_template(id)
    if not tpl:
        return jsonify({'ok': False, 'msg': 'Template niet gevonden'})
    try:
        smtp = get_smtp_config()
        review_link = smtp.get('google_review_link') or '#'
        html_body = _render_template(tpl['body_html'], 'Jan (Test)', review_link, smtp.get('logo_url', ''))
        _send(test_email, 'Jan (Test)', review_link, smtp,
              subject=f"[TEST] {tpl['onderwerp']}",
              html_body=html_body)
        return jsonify({'ok': True, 'msg': f'Testmail verstuurd naar {test_email}'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/templates/<int:id>/activate', methods=['POST'])
def template_activate(id):
    from db import set_active_template
    set_active_template(id)
    flash('Template geactiveerd', 'success')
    return redirect(url_for('templates_list'))


# ─── Contacts routes ──────────────────────────────────────────────────────────

@app.route('/contacts')
def contacts():
    from db import init_db, get_all_contacts
    init_db()
    all_contacts = get_all_contacts()
    return render_template('contacts.html', contacts=all_contacts, page='contacts')


@app.route('/uitsluitingen', methods=['GET', 'POST'])
def uitsluitingen():
    from db import (init_db, get_reviewed_names_full, add_reviewed_name, delete_reviewed_name,
                    get_blocked_list, add_blocked_person, delete_blocked_person)
    init_db()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_reviewed':
            naam = request.form.get('naam', '').strip()
            if naam:
                add_reviewed_name(naam)
                flash(f'"{naam}" toegevoegd aan Al gereviewd', 'success')
        elif action == 'delete_reviewed':
            naam = request.form.get('naam', '')
            delete_reviewed_name(naam)
            flash(f'"{naam}" verwijderd', 'info')
        elif action == 'add_blocked':
            naam = request.form.get('naam', '').strip()
            email = request.form.get('email', '').strip()
            reden = request.form.get('reden', '').strip()
            if naam:
                add_blocked_person(naam, email, reden)
                flash(f'"{naam}" toegevoegd aan Niet mailen', 'success')
        elif action == 'delete_blocked':
            bid = request.form.get('id', '')
            delete_blocked_person(bid)
            flash('Verwijderd', 'info')
        return redirect(url_for('uitsluitingen'))

    reviewed = get_reviewed_names_full()
    blocked = get_blocked_list()
    return render_template('uitsluitingen.html', reviewed=reviewed, blocked=blocked, page='uitsluitingen')


@app.route('/contacts/export/csv')
def contacts_export_csv():
    import csv as csv_module
    from db import get_all_contacts
    contacts = get_all_contacts()
    output = io.StringIO()
    writer = csv_module.writer(output, delimiter=';')
    writer.writerow(['naam', 'email', 'eerste_mail', 'laatste_mail', 'aantal_mails'])
    for c in contacts:
        writer.writerow([c['naam'], c['email'], c['eerste_mail'], c['laatste_mail'], c['aantal_mails']])
    output.seek(0)
    return Response(
        '﻿' + output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=contacten.csv'}
    )


@app.route('/contacts/export/xlsx')
def contacts_export_xlsx():
    from db import get_all_contacts
    import openpyxl
    contacts = get_all_contacts()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Contacten'
    ws.append(['Naam', 'E-mail', 'Eerste mail', 'Laatste mail', 'Aantal mails'])
    for c in contacts:
        ws.append([c['naam'], c['email'], c['eerste_mail'], c['laatste_mail'], c['aantal_mails']])
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': 'attachment; filename=contacten.xlsx'}
    )


# ─── Google Reviews page ──────────────────────────────────────────────────────

@app.route('/google-reviews')
def google_reviews_page():
    from db import init_db, get_app_setting
    init_db()
    api_key  = get_app_setting('google_places_api_key') or os.getenv('GOOGLE_PLACES_API_KEY', '')
    place_id = get_app_setting('google_place_id') or os.getenv('GOOGLE_PLACE_ID', '')

    reviews        = []
    overall_rating = None
    total_ratings  = None
    error          = None

    if not api_key or not place_id:
        missing = []
        if not api_key:  missing.append('Google Places API Key')
        if not place_id: missing.append('Google Place ID')
        error = f"Vereiste instellingen ontbreken: {', '.join(missing)}. Stel deze in via Instellingen."
    else:
        try:
            import requests as req_lib
            url = 'https://maps.googleapis.com/maps/api/place/details/json'
            params = {
                'place_id': place_id,
                'fields': 'reviews,rating,user_ratings_total',
                'language': 'nl',
                'key': api_key,
            }
            resp = req_lib.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get('status') == 'OK':
                result         = data.get('result', {})
                reviews        = result.get('reviews', [])
                overall_rating = result.get('rating')
                total_ratings  = result.get('user_ratings_total')
                if total_ratings:
                    from db import record_review_snapshot
                    record_review_snapshot(total_ratings)
            else:
                error = f"Google API fout: {data.get('status')} — {data.get('error_message', '')}"
        except Exception as e:
            error = str(e)

    maps_url = f'https://www.google.com/maps/search/?api=1&query_place_id={place_id}&query=reviews' if place_id else ''
    return render_template('google_reviews.html',
                           reviews=reviews,
                           overall_rating=overall_rating,
                           total_ratings=total_ratings,
                           error=error,
                           maps_url=maps_url,
                           page='reviews')


if __name__ == '__main__':
    from db import init_db
    init_db()
    reload_schedule()
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
