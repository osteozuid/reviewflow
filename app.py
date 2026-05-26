import io
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import (Flask, abort, flash, g, jsonify, redirect,
                   render_template, request, send_file, session, url_for, Response)
from werkzeug.utils import secure_filename
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

load_dotenv()

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import config

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')
app.logger.setLevel(logging.INFO)
app.jinja_env.globals['enumerate'] = enumerate
app.jinja_env.globals['PLATFORM_NAME'] = config.PLATFORM_NAME
app.jinja_env.globals['TOOL_NAME'] = config.TOOL_NAME
app.jinja_env.globals['APP_NAME'] = config.APP_NAME

APP_NAME     = config.APP_NAME
APP_BASE_URL = config.APP_BASE_URL.rstrip('/')
CSS_VERSION  = '5'  # bump on each static asset deploy

ALLOWED = {'.csv', '.xlsx', '.xls'}


def get_tenant_input_dir(tenant_id):
    d = ROOT / 'input' / str(tenant_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_tenant_output_dir(tenant_id):
    d = ROOT / 'output' / str(tenant_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_tenant_upload_dir(tenant_id):
    d = ROOT / 'static' / 'uploads' / str(tenant_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ─── Run state (per tenant) ───────────────────────────────────────────────────
_runs = {}
_run_lock = threading.Lock()


def _get_run(tenant_id):
    with _run_lock:
        if tenant_id not in _runs:
            _runs[tenant_id] = {
                'active': False, 'lines': [], 'done': False,
                'modus': None, 'counts': {},
            }
        return _runs[tenant_id]


def _log(tenant_id, msg):
    ts = datetime.now().strftime('%H:%M:%S')
    run = _get_run(tenant_id)
    with _run_lock:
        run['lines'].append(f'[{ts}]  {msg}')


def do_run(tenant_id, modus, test_email=None):
    import db
    from csv_import import load_all_csv
    from dedup import deduplicate, matches_reviewed
    from mailer import get_smtp_config, send_review_request

    run = _get_run(tenant_id)
    with _run_lock:
        run.update({'active': True, 'lines': [], 'done': False,
                    'modus': modus, 'counts': {}})

    input_dir  = get_tenant_input_dir(tenant_id)
    output_dir = get_tenant_output_dir(tenant_id)

    try:
        db.init_db()

        if modus == 'send':
            geblokkeerd = db.get_tenant_setting(tenant_id, 'send_geblokkeerd', '0')
            if geblokkeerd == '1':
                _log(tenant_id, 'GEBLOKKEERD: Versturen is uitgeschakeld in Instellingen.')
                _log(tenant_id, 'Zet "Versturen geblokkeerd" uit in Instellingen om echte mails te sturen.')
                return

        _log(tenant_id, 'Bestanden laden...')
        try:
            all_rows, load_stats = load_all_csv(input_dir)
        except FileNotFoundError:
            _log(tenant_id, 'Geen bestanden gevonden — upload eerst een CSV of Excel.')
            return

        candidates, skip_stats = deduplicate(all_rows)
        already_sent      = db.get_already_sent(tenant_id)
        blocked_emails    = db.get_blocked(tenant_id)
        blocked_names     = db.get_blocked_names(tenant_id)
        reviewed          = db.get_reviewed_names(tenant_id)
        suppressed_emails = db.get_suppressed(tenant_id)

        to_mail, skipped = [], []
        for c in candidates:
            if c['email'] in already_sent:
                skipped.append({**c, 'reden': 'Al gemaild'})
            elif c['email'] in suppressed_emails:
                skipped.append({**c, 'reden': 'Uitgeschreven'})
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
        _log(tenant_id, f'{total_gelezen} rijen gelezen uit {len(load_stats)} bestand(en)')
        _log(tenant_id, f'{len(to_mail)} te mailen  ·  {len(skipped)} overgeslagen')

        verzonden, gefaald = [], []

        if modus == 'dry':
            for p in skipped:
                reden = p.get('reden', '')
                if reden == 'Uitgeschreven':
                    _log(tenant_id, f'[UNSUB]  {p["naam"]}  <{p["email"]}> — uitgeschreven')
            for p in to_mail:
                _log(tenant_id, f'[DRY]  {p["naam"]}  <{p["email"]}>')
            _log(tenant_id, '─' * 40)
            _log(tenant_id, 'DRY RUN klaar — geen mails verstuurd')

        else:
            cfg = get_smtp_config(tenant_id)
            active_template = db.get_active_template(tenant_id)
            if modus == 'test':
                _log(tenant_id, f'TEST modus — alle mails → {test_email}')
            if active_template:
                _log(tenant_id, f'Template: "{active_template["naam"]}"')

            SEND_LIMIT = 20
            if modus == 'send' and len(to_mail) > SEND_LIMIT:
                _log(tenant_id,
                     f'⚠  Limiet: max {SEND_LIMIT} mails per run — '
                     f'{len(to_mail) - SEND_LIMIT} patiënten worden overgeslagen.')
                to_mail = to_mail[:SEND_LIMIT]

            _log(tenant_id, '─' * 40)

            for patient in to_mail:
                try:
                    if modus == 'send':
                        unsub_token = db.create_unsubscribe_token(tenant_id, patient['email'])
                        unsub_url   = f'{APP_BASE_URL}/unsubscribe/{unsub_token}'
                    else:
                        unsub_url = ''
                    target = {**patient, 'email': test_email} if modus == 'test' else patient
                    send_review_request(target, cfg, template=active_template,
                                        unsubscribe_url=unsub_url)
                    if modus == 'send':
                        db.log_sent(tenant_id, [patient], bestand=patient['bestand'])
                    verzonden.append(patient)
                    _log(tenant_id, f'✓  {patient["naam"]}  <{patient["email"]}>')
                    time.sleep(20)
                except Exception as e:
                    gefaald.append(patient)
                    err_str = str(e)
                    if '451' in err_str:
                        friendly = 'Tijdelijke SMTP-limiet. Wacht 10 minuten en probeer opnieuw.'
                    elif '535' in err_str or 'Authentication' in err_str:
                        friendly = 'SMTP login fout. Controleer gebruikersnaam/wachtwoord in Instellingen.'
                    else:
                        friendly = err_str
                    _log(tenant_id, f'✗  {patient["naam"]}  —  {friendly}')

            _log(tenant_id, '─' * 40)
            if modus == 'send' and verzonden:
                db.export_review_log(tenant_id, output_dir / 'verzonden.csv')
                _log(tenant_id, 'verzonden.csv bijgewerkt')

            all_ov = sum(
                s.get('rijen_leeg', 0) + s.get('rijen_fout_type', 0) +
                s.get('rijen_geen_naam', 0) + s.get('rijen_geen_email', 0) +
                s.get('rijen_ongeldig_email', 0) for s in load_stats
            )
            db.log_import(
                tenant_id=tenant_id,
                bestand=', '.join(s['bestand'] for s in load_stats),
                rijen_gelezen=total_gelezen,
                rijen_ok=sum(s['rijen_ok'] for s in load_stats),
                unieke_patienten=len(to_mail) + len(skipped),
                kandidaten=len(to_mail),
                gemaild=len(verzonden),
                overgeslagen=all_ov + len(skip_stats.get('dubbel', [])) + len(skipped),
                modus=modus,
            )

        with _run_lock:
            run['counts'] = {
                'kandidaten': len(to_mail),
                'overgeslagen': len(skipped),
                'verzonden': len(verzonden),
                'gefaald': len(gefaald),
            }

    except Exception as e:
        _log(tenant_id, f'FOUT: {e}')
    finally:
        with _run_lock:
            run['done']   = True
            run['active'] = False


# ─── Scheduler (per tenant) ───────────────────────────────────────────────────
DAGEN = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
scheduler = BackgroundScheduler(timezone='Europe/Brussels')
scheduler.start()


def reload_schedule(tenant_id):
    import db
    job_id = f'auto_{tenant_id}'
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    cfg = db.get_schedule_config(tenant_id)
    if not cfg or not cfg['actief'] or cfg['modus'] == 'manual':
        return
    h, m = cfg['tijdstip'].split(':')
    if cfg['modus'] == 'weekly':
        trigger = CronTrigger(day_of_week=DAGEN[cfg['dag_van_week']],
                              hour=int(h), minute=int(m))
    else:
        trigger = CronTrigger(hour=int(h), minute=int(m))
    scheduler.add_job(
        lambda: do_run(tenant_id, 'send'), trigger,
        id=job_id, name=f'Auto verzending tenant {tenant_id}', replace_existing=True,
    )


# ─── Auth + request context ───────────────────────────────────────────────────
@app.before_request
def load_user():
    import db
    open_endpoints = {'login', 'logout', 'invite_accept', 'unsubscribe', 'static'}
    g.user      = None
    g.tenant_id = None
    g.tenant    = None

    user_id = session.get('user_id')
    if user_id:
        g.user = db.get_user_by_id(user_id)
        if g.user and g.user.get('tenant_id'):
            g.tenant_id = g.user['tenant_id']
            g.tenant    = db.get_tenant(g.tenant_id)

    if request.endpoint not in open_endpoints and not g.user:
        return redirect(url_for('login', next=request.path))


@app.template_filter('fmtdt')
def fmtdt(s):
    if not s:
        return '—'
    try:
        return str(s)[:16].replace('T', ' ')
    except Exception:
        return str(s)


@app.template_filter('fmtdt_short')
def fmtdt_short(s):
    """Format datetime as 'vandaag 09:02', 'gisteren 09:01', or '14 mei 09:02'"""
    if not s:
        return '—'
    try:
        from datetime import datetime, timedelta
        dt = s if isinstance(s, datetime) else datetime.fromisoformat(str(s))
        now = datetime.now()
        today = now.date()
        yesterday = (now - timedelta(days=1)).date()

        if dt.date() == today:
            return dt.strftime('vandaag %H:%M')
        elif dt.date() == yesterday:
            return dt.strftime('gisteren %H:%M')
        else:
            return dt.strftime('%d %b %H:%M')
    except Exception:
        return str(s)[:16]


@app.template_filter('initials')
def initials(name):
    if not name:
        return '?'
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[:2].upper()


def make_sparkline(values, w=120, h=28, pad=2):
    """Generate SVG path data for sparkline: (line_path, area_path)"""
    if not values:
        return ('', '')
    lo, hi = min(values), max(values)
    rng = max(hi - lo, 1)
    n = len(values)
    points = []
    for i, v in enumerate(values):
        x = i / max(n-1, 1) * w
        y = h - pad - (v - lo) / rng * (h - 2*pad)
        points.append((x, y))
    line = 'M' + ' L'.join(f'{x:.1f},{y:.1f}' for x,y in points)
    area = f'M{points[0][0]:.1f},{h} L' + ' L'.join(f'{x:.1f},{y:.1f}' for x,y in points) + f' L{points[-1][0]:.1f},{h} Z'
    return line, area


def get_delta(current, previous):
    """Calculate percentage change: (current - previous) / previous * 100"""
    if not previous or previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


def get_last_sync(tenant_id):
    """Get timestamp of most recent successful run"""
    import db
    with db.get_connection() as conn:
        cur = db._q(conn,
            "SELECT import_at FROM import_log WHERE tenant_id = %s ORDER BY import_at DESC LIMIT 1",
            (tenant_id,))
        row = cur.fetchone()
        return row['import_at'] if row else None


@app.context_processor
def inject_globals():
    result = {'css_v': CSS_VERSION}
    if hasattr(g, 'user') and g.user:
        result['last_sync'] = get_last_sync(g.tenant_id) if hasattr(g, 'tenant_id') else None
    return result


# ─── Login / Logout ───────────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    import db
    from auth import verify_password
    if g.user:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = db.get_user_by_email(email)
        if user and verify_password(password, user['password_hash']):
            session.clear()
            session['user_id'] = user['id']
            db.update_user_last_login(user['id'])
            db.log_audit('login', user_id=user['id'],
                         tenant_id=user.get('tenant_id'), ip=request.remote_addr)
            return redirect(request.args.get('next') or url_for('dashboard'))
        flash('Ongeldig e-mailadres of wachtwoord', 'error')
    return render_template('login.html', app_name=APP_NAME)


@app.route('/logout')
def logout():
    import db
    if g.user:
        db.log_audit('logout', user_id=g.user['id'],
                     tenant_id=g.user.get('tenant_id'), ip=request.remote_addr)
    session.clear()
    return redirect(url_for('login'))


# ─── Invite flow ──────────────────────────────────────────────────────────────
@app.route('/invite/<token>', methods=['GET', 'POST'])
def invite_accept(token):
    import db
    from auth import hash_password

    invite = db.get_invite_token(token)
    now = datetime.now()

    if not invite:
        flash('Ongeldige uitnodigingslink', 'error')
        return redirect(url_for('login'))
    if invite['accepted_at']:
        flash('Deze uitnodiging is al gebruikt', 'warning')
        return redirect(url_for('login'))
    if invite['expires_at'] < now:
        flash('Deze uitnodiging is verlopen', 'error')
        return redirect(url_for('login'))

    tenant = db.get_tenant(invite['tenant_id'])

    if request.method == 'POST':
        full_name  = request.form.get('full_name', '').strip()
        password   = request.form.get('password', '')
        password2  = request.form.get('password2', '')

        if not full_name:
            flash('Volledige naam is verplicht', 'error')
        elif len(password) < 8:
            flash('Wachtwoord moet minstens 8 tekens zijn', 'error')
        elif password != password2:
            flash('Wachtwoorden komen niet overeen', 'error')
        else:
            existing = db.get_user_by_email(invite['email'])
            if existing:
                flash(
                    'Er bestaat al een account met dit e-mailadres. '
                    'Log in via de normale loginpagina.',
                    'error'
                )
            else:
                user_id = db.create_user(
                    email=invite['email'],
                    password_hash=hash_password(password),
                    role=invite['role'],
                    tenant_id=invite['tenant_id'],
                    full_name=full_name,
                )
                db.accept_invite_token(token)
                db.log_audit('invite_accepted', user_id=user_id,
                             tenant_id=invite['tenant_id'], ip=request.remote_addr)
                session['user_id'] = user_id
                flash(f'Welkom bij {APP_NAME}, {full_name}!', 'success')
                return redirect(url_for('dashboard'))

    return render_template('invite.html', invite=invite, tenant=tenant, app_name=APP_NAME)


# ─── Dashboard ────────────────────────────────────────────────────────────────
@app.route('/')
def dashboard():
    import db
    db.init_db()

    db.sync_contacts_from_log(g.tenant_id)

    period = request.args.get('period', '30d')
    if period not in db.VALID_PERIODS:
        period = '30d'

    stats = db.get_dashboard_stats(g.tenant_id)

    # Period-aware sent count + delta
    period_count = db.get_sent_count_for_period(g.tenant_id, period)
    prev_count   = db.get_sent_count_prev_period(g.tenant_id, period)
    delta_total  = get_delta(period_count, prev_count) if prev_count is not None else None

    # Month-on-month delta for "Deze maand" card (always calendar-month comparison)
    with db.get_connection() as conn:
        cur = db._q(conn,
            """SELECT COUNT(*) AS cnt FROM review_log
               WHERE tenant_id = %s
               AND sent_at >= date_trunc('month', NOW() - interval '1 month')
               AND sent_at <  date_trunc('month', NOW())""",
            (g.tenant_id,))
        prev_month = cur.fetchone()['cnt']
    delta_month = stats['this_month'] - prev_month

    # review_rate delta: no per-date conversion tracking — show None honestly
    delta_review_rate = None

    # Sparkline series (period-aware, tenant-scoped)
    sent_series = db.get_sent_series(g.tenant_id, period)

    # Review snapshots (period-aware for large chart)
    review_baseline  = db.get_tenant_setting(g.tenant_id, 'review_baseline') or None
    review_growth    = db.get_review_growth(g.tenant_id, baseline=review_baseline)
    review_snapshots = db.get_review_snapshots_for_period(g.tenant_id, period)

    if period == 'all' and review_baseline and stats['first_sent_date']:
        start_date = stats['first_sent_date']
        if not review_snapshots or review_snapshots[0]['date'] > start_date:
            review_snapshots = [{'date': start_date,
                                 'total': int(review_baseline)}] + review_snapshots

    cfg = db.get_schedule_config(g.tenant_id)
    job = scheduler.get_job(f'auto_{g.tenant_id}')
    next_run = job.next_run_time.strftime('%a %d/%m %H:%M') if job else None

    input_count = sum(
        1 for f in get_tenant_input_dir(g.tenant_id).iterdir()
        if f.suffix.lower() in ALLOWED
    )

    for r in stats['recent']:
        r['status'] = 'delivered'
        r['status_label'] = 'Bezorgd'
        r['status_class'] = ''
        r['template_name'] = stats['last_run'].get('bestand', '') if stats['last_run'] else None

    return render_template('dashboard.html',
        period=period,
        period_count=period_count,
        total=stats['total'],
        this_month=stats['this_month'],
        delta_total=delta_total,
        delta_month=delta_month,
        delta_review_rate=delta_review_rate,
        sent_series=sent_series,
        last_run=stats['last_run'],
        recent=stats['recent'],
        reviewed_count=stats['reviewed_count'],
        input_count=input_count,
        next_run=next_run,
        schedule=cfg,
        review_growth=review_growth,
        review_snapshots=review_snapshots,
        review_baseline=review_baseline,
        page='dashboard',
        app_name=APP_NAME,
    )


# ─── Upload ───────────────────────────────────────────────────────────────────
@app.route('/upload', methods=['GET', 'POST'])
def upload():
    input_dir = get_tenant_input_dir(g.tenant_id)
    if request.method == 'POST':
        files = request.files.getlist('files')
        saved = []
        for f in files:
            if f and f.filename and Path(f.filename).suffix.lower() in ALLOWED:
                name = secure_filename(f.filename)
                f.save(input_dir / name)
                saved.append(name)
        if saved:
            flash(f'{len(saved)} bestand(en) geüpload', 'success')
        else:
            flash('Geen geldige bestanden (.csv, .xlsx)', 'warning')
        return redirect(url_for('upload'))

    raw = sorted(input_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    files = []
    for f in raw:
        if f.suffix.lower() in ALLOWED:
            st = f.stat()
            files.append({
                'name': f.name,
                'size': f'{st.st_size / 1024:.1f} KB',
                'modified': datetime.fromtimestamp(st.st_mtime).strftime('%d/%m/%Y %H:%M'),
            })
    return render_template('upload.html', files=files, page='upload', app_name=APP_NAME)


@app.route('/upload/delete/<path:filename>')
def delete_file(filename):
    input_dir = get_tenant_input_dir(g.tenant_id)
    p = input_dir / secure_filename(filename)
    if p.exists():
        p.unlink()
        flash(f'{p.name} verwijderd', 'info')
    return redirect(url_for('upload'))


@app.route('/upload/delete-all', methods=['POST'])
def delete_all_files():
    input_dir = get_tenant_input_dir(g.tenant_id)
    removed = 0
    for f in input_dir.iterdir():
        if f.suffix.lower() in ALLOWED:
            f.unlink()
            removed += 1
    flash(f'{removed} bestand(en) verwijderd uit de wachtrij', 'info')
    return redirect(url_for('upload'))


@app.route('/upload/logo', methods=['POST'])
def upload_logo():
    import db
    upload_dir = get_tenant_upload_dir(g.tenant_id)

    if request.form.get('action') == 'delete_logo':
        logo_url = db.get_tenant_setting(g.tenant_id, 'logo_url', '')
        if logo_url:
            logo_path = ROOT / logo_url.lstrip('/')
            if logo_path.exists():
                logo_path.unlink()
        db.save_tenant_setting(g.tenant_id, 'logo_url', '')
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
    try:
        f.save(upload_dir / f'logo.{ext}')
    except Exception as exc:
        flash(f'Logo opslaan mislukt: {exc}', 'error')
        return redirect(url_for('settings'))
    db.save_tenant_setting(
        g.tenant_id, 'logo_url',
        f'/static/uploads/{g.tenant_id}/logo.{ext}',
    )
    flash('Logo opgeslagen ✓', 'success')
    return redirect(url_for('settings'))


# ─── Run ─────────────────────────────────────────────────────────────────────
@app.route('/run')
def run_page():
    import db
    admin          = db.get_tenant_setting(g.tenant_id, 'admin_email', '')
    send_geblokkeerd = db.get_tenant_setting(g.tenant_id, 'send_geblokkeerd', '0') == '1'
    run_snapshot   = dict(_get_run(g.tenant_id))
    return render_template('run.html', run=run_snapshot, admin=admin,
                           send_geblokkeerd=send_geblokkeerd, page='run', app_name=APP_NAME)


@app.route('/run/preview')
def run_preview():
    import db
    from csv_import import load_all_csv
    from dedup import deduplicate, matches_reviewed
    from mailer import get_smtp_config

    admin            = db.get_tenant_setting(g.tenant_id, 'admin_email', '')
    send_geblokkeerd = db.get_tenant_setting(g.tenant_id, 'send_geblokkeerd', '0') == '1'
    input_dir        = get_tenant_input_dir(g.tenant_id)
    SEND_LIMIT       = 20
    error            = None
    preview          = None

    try:
        all_rows, load_stats = load_all_csv(input_dir)
        candidates, _ = deduplicate(all_rows)
        already_sent      = db.get_already_sent(g.tenant_id)
        blocked_emails    = db.get_blocked(g.tenant_id)
        blocked_names     = db.get_blocked_names(g.tenant_id)
        reviewed          = db.get_reviewed_names(g.tenant_id)
        suppressed_emails = db.get_suppressed(g.tenant_id)

        to_mail, skipped = [], []
        for c in candidates:
            if c['email'] in already_sent:
                skipped.append({**c, 'reden': 'Al gemaild'})
            elif c['email'] in suppressed_emails:
                skipped.append({**c, 'reden': 'Uitgeschreven'})
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

        active_template = db.get_active_template(g.tenant_id)
        try:
            smtp_cfg = get_smtp_config(g.tenant_id)
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
        send_geblokkeerd=send_geblokkeerd, page='run', app_name=APP_NAME)


@app.route('/run/test-one', methods=['POST'])
def run_test_one():
    import db
    import re as _re
    from csv_import import load_all_csv
    from dedup import deduplicate, matches_reviewed
    from mailer import get_smtp_config, _render_template, render_subject, _send

    input_dir = get_tenant_input_dir(g.tenant_id)

    try:
        all_rows, _ = load_all_csv(input_dir)
    except FileNotFoundError:
        flash('Geen bestanden gevonden — upload eerst een CSV of Excel.', 'warning')
        return redirect(url_for('run_page'))
    except Exception as e:
        flash(f'Fout bij laden bestanden: {e}', 'error')
        return redirect(url_for('run_page'))

    candidates, _ = deduplicate(all_rows)
    already_sent   = db.get_already_sent(g.tenant_id)
    blocked_emails = db.get_blocked(g.tenant_id)
    blocked_names  = db.get_blocked_names(g.tenant_id)
    reviewed       = db.get_reviewed_names(g.tenant_id)

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
        cfg = get_smtp_config(g.tenant_id)
    except ValueError as e:
        flash(f'SMTP niet geconfigureerd: {e}', 'error')
        return redirect(url_for('run_page'))

    admin_email = cfg.get('admin_email') or ''
    if not admin_email:
        flash('Geen admin e-mailadres ingesteld in Instellingen.', 'warning')
        return redirect(url_for('run_page'))

    active_template = db.get_active_template(g.tenant_id)
    voornaam     = candidate.get('voornaam') or candidate['naam'].split()[-1]
    praktijknaam = cfg.get('from_name', '')
    review_link  = cfg.get('google_review_link', '') or 'https://maps.google.com/'
    logo_url     = cfg.get('logo_url', '')

    test_banner = (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:16px;">'
        '<tr><td style="background:#fff3cd;border:2px solid #ffc107;padding:12px 16px;'
        'border-radius:4px;font-family:Arial,sans-serif;font-size:13px;color:#856404;">'
        '&#9888; <strong>TESTMAIL</strong> &mdash; deze mail werd niet naar de pati&euml;nt verstuurd. '
        f'Voorbeelddata van: <strong>{candidate["naam"]}</strong>'
        ' &lt;' + candidate["email"] + '&gt;'
        '</td></tr></table>'
    )

    test_unsub_footer = (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin-top:32px;">'
        '<tr><td style="border-top:1px solid #e0e0e0;padding-top:14px;'
        'font-family:Arial,sans-serif;font-size:12px;color:#999999;line-height:1.6;">'
        f'[TEST] Uitschrijflink — in echte mail staat hier de unsubscribe-link voor '
        f'{praktijknaam}.'
        '</td></tr></table>'
    )

    if active_template:
        subject   = f"[TEST] {render_subject(active_template['onderwerp'], praktijknaam)}"
        html_body = _render_template(
            active_template['body_html'], voornaam, review_link, logo_url, praktijknaam,
            unsubscribe_url='#test-unsubscribe',
        )
        if '<body' in html_body:
            html_body = _re.sub(r'(<body[^>]*>)', r'\1' + test_banner, html_body, count=1)
        else:
            html_body = test_banner + html_body
    else:
        subject   = f"[TEST] Review request — {praktijknaam}"
        html_body = (
            test_banner +
            f'<p style="font-family:Arial,sans-serif;font-size:15px;">'
            f'Dag {voornaam}, dit is een testmail. Geen actieve template ingesteld.</p>'
        )

    # Always append test footer (no real unsubscribe token)
    if '</body>' in html_body:
        html_body = html_body.replace('</body>', test_unsub_footer + '</body>', 1)
    else:
        html_body = html_body + test_unsub_footer

    try:
        _send(admin_email, voornaam, review_link, cfg, subject=subject, html_body=html_body)
        app.logger.info(f'[test-one] Testmail verstuurd naar {admin_email} '
                        f'(tenant {g.tenant_id})')
        flash(
            f'Testmail verstuurd naar {admin_email} met voorbeelddata van '
            f'{candidate["naam"]} <{candidate["email"]}>',
            'success'
        )
    except Exception as e:
        app.logger.error(f'[test-one] SMTP fout (tenant {g.tenant_id}): {e}',
                         exc_info=True)
        err_str = str(e)
        if '451' in err_str:
            msg = 'Tijdelijke SMTP-limiet. Wacht 10 minuten en probeer opnieuw.'
        elif '535' in err_str or 'Authentication' in err_str:
            msg = 'SMTP login fout. Controleer gebruikersnaam/wachtwoord in Instellingen.'
        elif 'timed out' in err_str.lower() or 'timeout' in err_str.lower():
            msg = 'SMTP-server reageert niet (timeout). Controleer host en poort in Instellingen.'
        else:
            msg = err_str
        flash(f'Fout bij testmail: {msg}', 'error')

    return redirect(url_for('run_page'))


@app.route('/run/start', methods=['POST'])
def run_start():
    import db
    modus       = request.form.get('modus', 'dry')
    admin_email = db.get_tenant_setting(g.tenant_id, 'admin_email', '')
    test_email  = request.form.get('test_email') or admin_email

    if modus == 'send' and not request.form.get('confirmed'):
        return redirect(url_for('run_preview'))

    run = _get_run(g.tenant_id)
    with _run_lock:
        if run['active']:
            flash('Er loopt al een run', 'warning')
            return redirect(url_for('run_page'))
    threading.Thread(
        target=do_run, args=(g.tenant_id, modus, test_email), daemon=True
    ).start()
    return redirect(url_for('run_page'))


@app.route('/api/run/status')
def run_status():
    run = _get_run(g.tenant_id)
    return jsonify({
        'active': run['active'],
        'done':   run['done'],
        'lines':  run['lines'][-200:],
        'counts': run['counts'],
        'modus':  run['modus'],
    })


# ─── Schedule ─────────────────────────────────────────────────────────────────
@app.route('/schedule', methods=['GET', 'POST'])
def schedule():
    import db
    if request.method == 'POST':
        db.save_schedule_config(g.tenant_id, {
            'modus':        request.form.get('modus', 'manual'),
            'dag_van_week': int(request.form.get('dag_van_week', 0)),
            'tijdstip':     request.form.get('tijdstip', '09:00'),
            'actief':       bool(request.form.get('actief')),
        })
        reload_schedule(g.tenant_id)
        flash('Schema opgeslagen', 'success')
        return redirect(url_for('schedule'))

    cfg = db.get_schedule_config(g.tenant_id)
    job = scheduler.get_job(f'auto_{g.tenant_id}')
    next_run = job.next_run_time.strftime('%A %d/%m/%Y om %H:%M') if job else None
    return render_template('schedule.html', cfg=cfg, next_run=next_run,
                           page='schedule', app_name=APP_NAME)


# ─── Logs ────────────────────────────────────────────────────────────────────
@app.route('/logs')
def logs():
    import db
    runs = db.get_import_logs(g.tenant_id)
    sent = db.get_sent_logs(g.tenant_id)
    return render_template('logs.html', runs=runs, sent=sent,
                           page='logs', app_name=APP_NAME)


@app.route('/logs/export')
def export_log():
    import db
    output_dir = get_tenant_output_dir(g.tenant_id)
    path = output_dir / 'review_log_export.csv'
    db.export_review_log(g.tenant_id, path)
    return send_file(str(path), as_attachment=True, download_name='review_log.csv')


# ─── Settings ────────────────────────────────────────────────────────────────
@app.route('/settings', methods=['GET', 'POST'])
def settings():
    import db
    from auth import hash_password, verify_password

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add_reviewed':
            naam = request.form.get('naam', '').strip()
            if naam:
                db.add_reviewed_name(g.tenant_id, naam)
                flash(f'"{naam}" toegevoegd', 'success')

        elif action == 'delete_reviewed':
            naam = request.form.get('naam', '')
            db.delete_reviewed_name(g.tenant_id, naam)
            flash(f'"{naam}" verwijderd', 'info')

        elif action == 'save_settings':
            fields = {
                'smtp_host':             request.form.get('smtp_host', '').strip(),
                'smtp_port':             request.form.get('smtp_port', '587').strip(),
                'smtp_user':             request.form.get('smtp_user', '').strip(),
                'smtp_password':         request.form.get('smtp_password', '').strip(),
                'from_email':            request.form.get('from_email', '').strip(),
                'from_name':             request.form.get('from_name', '').strip(),
                'admin_email':           request.form.get('admin_email', '').strip(),
                'google_review_link':    request.form.get('google_review_link', '').strip(),
                'send_geblokkeerd':      '1' if request.form.get('send_geblokkeerd') else '0',
                'google_places_api_key': request.form.get('google_places_api_key', '').strip(),
                'google_place_id':       request.form.get('google_place_id', '').strip(),
                'review_baseline':       request.form.get('review_baseline', '').strip(),
                'praktijknaam':          request.form.get('praktijknaam', '').strip(),
            }
            # Preserve logo_url (managed via /upload/logo)
            fields['logo_url'] = db.get_tenant_setting(g.tenant_id, 'logo_url', '')
            # Don't overwrite smtp_password if left blank
            if not fields['smtp_password']:
                fields['smtp_password'] = db.get_tenant_setting(g.tenant_id, 'smtp_password', '')
            db.save_tenant_settings(g.tenant_id, fields)
            flash('Instellingen opgeslagen', 'success')

        elif action == 'change_password':
            current_pw = request.form.get('password_current', '').strip()
            new_pw     = request.form.get('password_new', '').strip()
            confirm_pw = request.form.get('password_confirm', '').strip()
            if not verify_password(current_pw, g.user['password_hash']):
                flash('Huidig wachtwoord klopt niet', 'error')
            elif len(new_pw) < 8:
                flash('Nieuw wachtwoord moet minstens 8 tekens zijn', 'error')
            elif new_pw != confirm_pw:
                flash('Nieuwe wachtwoorden komen niet overeen', 'error')
            else:
                db.update_user_password(g.user['id'], hash_password(new_pw))
                flash('Wachtwoord gewijzigd ✓', 'success')

        return redirect(url_for('settings'))

    reviewed   = db.get_reviewed_names(g.tenant_id)
    db_settings = db.get_all_tenant_settings(g.tenant_id)
    defaults = {
        'smtp_host': '', 'smtp_port': '587', 'smtp_user': '',
        'smtp_password': '', 'from_email': '', 'from_name': '',
        'admin_email': '', 'google_review_link': '', 'send_geblokkeerd': '0',
        'google_places_api_key': '', 'google_place_id': '', 'logo_url': '',
        'review_baseline': '', 'praktijknaam': '',
    }
    cfg = {k: db_settings.get(k) or defaults.get(k, '') for k in defaults}
    return render_template('settings.html', reviewed=reviewed, cfg=cfg,
                           page='settings', app_name=APP_NAME)


# ─── Templates ───────────────────────────────────────────────────────────────
@app.route('/templates')
def templates_list():
    import db
    templates = db.get_all_templates(g.tenant_id)
    return render_template('templates_list.html', templates=templates,
                           page='templates', app_name=APP_NAME)


@app.route('/templates/new')
def template_new():
    import db
    logo_url    = db.get_tenant_setting(g.tenant_id, 'logo_url', '')
    admin_email = db.get_tenant_setting(g.tenant_id, 'admin_email', '')
    return render_template('template_editor.html', template=None, page='templates',
                           logo_url=logo_url, admin_email=admin_email, app_name=APP_NAME)


@app.route('/templates/save', methods=['POST'])
def template_save():
    import db
    naam       = request.form.get('naam', '').strip()
    onderwerp  = request.form.get('onderwerp', '').strip()
    body_html  = request.form.get('body_html', '')
    tpl_id     = request.form.get('id') or None
    if not naam or not onderwerp:
        flash('Naam en onderwerp zijn verplicht', 'warning')
        return redirect(url_for('templates_list'))
    db.save_template(g.tenant_id, naam, onderwerp, body_html, template_id=tpl_id)
    flash('Template opgeslagen', 'success')
    return redirect(url_for('templates_list'))


@app.route('/templates/<int:id>/edit')
def template_edit(id):
    import db
    tpl = db.get_template(g.tenant_id, id)
    if not tpl:
        flash('Template niet gevonden', 'warning')
        return redirect(url_for('templates_list'))
    logo_url    = db.get_tenant_setting(g.tenant_id, 'logo_url', '')
    admin_email = db.get_tenant_setting(g.tenant_id, 'admin_email', '')
    return render_template('template_editor.html', template=tpl, page='templates',
                           logo_url=logo_url, admin_email=admin_email, app_name=APP_NAME)


@app.route('/templates/<int:id>/delete', methods=['POST'])
def template_delete(id):
    import db
    ok, msg = db.delete_template(g.tenant_id, id)
    flash(msg, 'success' if ok else 'warning')
    return redirect(url_for('templates_list'))


@app.route('/templates/<int:id>/test-mail', methods=['POST'])
def template_test_mail(id):
    import db
    from mailer import get_smtp_config, _render_template, render_subject, _send
    test_email = request.form.get('test_email', '').strip()
    if not test_email:
        return jsonify({'ok': False, 'msg': 'Geen e-mailadres opgegeven'})
    tpl = db.get_template(g.tenant_id, id)
    if not tpl:
        return jsonify({'ok': False, 'msg': 'Template niet gevonden'})
    try:
        smtp = get_smtp_config(g.tenant_id)
        review_link  = smtp.get('google_review_link') or '#'
        logo_url     = smtp.get('logo_url', '')
        praktijknaam = smtp.get('from_name', '')
        html_body    = _render_template(tpl['body_html'], 'Jan (Test)',
                                        review_link, logo_url, praktijknaam)
        subject      = f"[TEST] {render_subject(tpl['onderwerp'], praktijknaam)}"
        _send(test_email, 'Jan (Test)', review_link, smtp,
              subject=subject, html_body=html_body)
        return jsonify({'ok': True, 'msg': f'Testmail verstuurd naar {test_email}'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/templates/<int:id>/activate', methods=['POST'])
def template_activate(id):
    import db
    db.set_active_template(g.tenant_id, id)
    flash('Template geactiveerd', 'success')
    return redirect(url_for('templates_list'))


# ─── Contacts ────────────────────────────────────────────────────────────────
@app.route('/contacts')
def contacts():
    import db
    all_contacts = db.get_all_contacts(g.tenant_id)
    return render_template('contacts.html', contacts=all_contacts,
                           page='contacts', app_name=APP_NAME)


@app.route('/uitsluitingen', methods=['GET', 'POST'])
def uitsluitingen():
    import db
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_reviewed':
            naam = request.form.get('naam', '').strip()
            if naam:
                db.add_reviewed_name(g.tenant_id, naam)
                flash(f'"{naam}" toegevoegd aan Al gereviewd', 'success')
        elif action == 'delete_reviewed':
            db.delete_reviewed_name(g.tenant_id, request.form.get('naam', ''))
            flash('Verwijderd', 'info')
        elif action == 'add_blocked':
            naam  = request.form.get('naam', '').strip()
            email = request.form.get('email', '').strip()
            reden = request.form.get('reden', '').strip()
            if naam:
                db.add_blocked_person(g.tenant_id, naam, email, reden)
                flash(f'"{naam}" toegevoegd aan Niet mailen', 'success')
        elif action == 'delete_blocked':
            db.delete_blocked_person(g.tenant_id, request.form.get('id', ''))
            flash('Verwijderd', 'info')
        elif action == 'add_suppression':
            email = request.form.get('email', '').strip().lower()
            if email:
                db.add_suppression(g.tenant_id, email, reason='manual', source='admin_ui')
                flash(f'{email} toegevoegd aan uitgeschreven lijst', 'success')
            else:
                flash('E-mailadres is verplicht', 'warning')
        elif action == 'delete_suppression':
            db.delete_suppression(g.tenant_id, request.form.get('id', ''))
            flash('Uitschrijving verwijderd — persoon kan opnieuw mails ontvangen', 'info')
        return redirect(url_for('uitsluitingen'))

    reviewed    = db.get_reviewed_names_full(g.tenant_id)
    blocked     = db.get_blocked_list(g.tenant_id)
    suppressed  = db.get_suppression_list(g.tenant_id)
    return render_template('uitsluitingen.html', reviewed=reviewed, blocked=blocked,
                           suppressed=suppressed, page='uitsluitingen', app_name=APP_NAME)


@app.route('/contacts/export/csv')
def contacts_export_csv():
    import csv as csv_module
    import db
    all_contacts = db.get_all_contacts(g.tenant_id)
    output = io.StringIO()
    writer = csv_module.writer(output, delimiter=';')
    writer.writerow(['naam', 'email', 'eerste_mail', 'laatste_mail', 'aantal_mails'])
    for c in all_contacts:
        writer.writerow([c['naam'], c['email'], c['eerste_mail'],
                         c['laatste_mail'], c['aantal_mails']])
    output.seek(0)
    return Response(
        '﻿' + output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=contacten.csv'},
    )


@app.route('/contacts/export/xlsx')
def contacts_export_xlsx():
    import db
    import openpyxl
    all_contacts = db.get_all_contacts(g.tenant_id)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Contacten'
    ws.append(['Naam', 'E-mail', 'Eerste mail', 'Laatste mail', 'Aantal mails'])
    for c in all_contacts:
        ws.append([c['naam'], c['email'], c['eerste_mail'],
                   c['laatste_mail'], c['aantal_mails']])
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': 'attachment; filename=contacten.xlsx'},
    )


# ─── Google Reviews ───────────────────────────────────────────────────────────
@app.route('/google-reviews')
def google_reviews_page():
    import db
    import requests as req_lib
    api_key  = db.get_tenant_setting(g.tenant_id, 'google_places_api_key', '')
    place_id = db.get_tenant_setting(g.tenant_id, 'google_place_id', '')

    reviews = []
    overall_rating = None
    total_ratings  = None
    error = None

    if not api_key or not place_id:
        missing = []
        if not api_key:  missing.append('Google Places API Key')
        if not place_id: missing.append('Google Place ID')
        error = f"Vereiste instellingen ontbreken: {', '.join(missing)}. Stel in via Instellingen."
    else:
        try:
            resp = req_lib.get(
                'https://maps.googleapis.com/maps/api/place/details/json',
                params={'place_id': place_id,
                        'fields': 'reviews,rating,user_ratings_total',
                        'language': 'nl', 'key': api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get('status') == 'OK':
                result        = data.get('result', {})
                reviews       = result.get('reviews', [])
                overall_rating = result.get('rating')
                total_ratings  = result.get('user_ratings_total')
                if total_ratings:
                    db.record_review_snapshot(g.tenant_id, total_ratings)
            else:
                error = f"Google API fout: {data.get('status')} — {data.get('error_message', '')}"
        except Exception as e:
            error = str(e)

    maps_url = (
        f'https://www.google.com/maps/search/?api=1&query_place_id={place_id}&query=reviews'
        if place_id else ''
    )
    return render_template('google_reviews.html',
                           reviews=reviews,
                           overall_rating=overall_rating,
                           total_ratings=total_ratings,
                           error=error,
                           maps_url=maps_url,
                           page='reviews',
                           app_name=APP_NAME)


# ─── Superadmin routes ────────────────────────────────────────────────────────
@app.route('/admin/tenants')
def admin_tenants():
    import db
    from auth import superadmin_required
    if not g.user or g.user['role'] != 'superadmin':
        abort(403)
    tenants = db.get_all_tenants()
    pending_invites = {}
    for t in tenants:
        pending_invites[t['id']] = db.get_pending_invites(t['id'])
    db.log_audit('superadmin_view_tenants', user_id=g.user['id'], ip=request.remote_addr)
    return render_template('admin_tenants.html',
                           tenants=tenants,
                           pending_invites=pending_invites,
                           app_name=APP_NAME,
                           page='admin')


@app.route('/admin/tenants/new', methods=['POST'])
def admin_tenant_create():
    import db
    from mailer import send_invite_email
    if not g.user or g.user['role'] != 'superadmin':
        abort(403)

    slug          = request.form.get('slug', '').strip().lower()
    name          = request.form.get('name', '').strip()
    invite_email  = request.form.get('invite_email', '').strip().lower()
    invite_role   = request.form.get('invite_role', 'owner')

    if not slug or not name or not invite_email:
        flash('Slug, naam en uitnodigingsmail zijn verplicht', 'error')
        return redirect(url_for('admin_tenants'))

    if db.get_tenant_by_slug(slug):
        flash(f'Slug "{slug}" is al in gebruik', 'error')
        return redirect(url_for('admin_tenants'))

    try:
        tenant_id = db.create_tenant(slug, name)
        token     = db.create_invite_token(
            email=invite_email,
            tenant_id=tenant_id,
            role=invite_role,
            created_by=g.user['id'],
        )
        invite_url = f'{APP_BASE_URL}/invite/{token}'
        try:
            send_invite_email(invite_email, name, invite_url)
            flash(f'Tenant "{name}" aangemaakt en uitnodiging verstuurd naar {invite_email}', 'success')
        except Exception as e:
            flash(f'Tenant aangemaakt maar mail kon niet verstuurd worden: {e}. '
                  f'Invite URL: {invite_url}', 'warning')

        db.log_audit('tenant_created', user_id=g.user['id'],
                     details={'slug': slug, 'name': name, 'invite_email': invite_email},
                     ip=request.remote_addr)
    except Exception as e:
        flash(f'Fout bij aanmaken tenant: {e}', 'error')

    return redirect(url_for('admin_tenants'))


@app.route('/admin/tenants/<int:tenant_id>/invite', methods=['POST'])
def admin_tenant_invite(tenant_id):
    import db
    from mailer import send_invite_email
    if not g.user or g.user['role'] != 'superadmin':
        abort(403)

    tenant       = db.get_tenant(tenant_id)
    invite_email = request.form.get('invite_email', '').strip().lower()
    invite_role  = request.form.get('invite_role', 'staff')

    if not tenant or not invite_email:
        flash('Ongeldige aanvraag', 'error')
        return redirect(url_for('admin_tenants'))

    token      = db.create_invite_token(invite_email, tenant_id, role=invite_role,
                                        created_by=g.user['id'])
    invite_url = f'{APP_BASE_URL}/invite/{token}'
    try:
        send_invite_email(invite_email, tenant['name'], invite_url)
        flash(f'Uitnodiging verstuurd naar {invite_email}', 'success')
    except Exception as e:
        flash(f'Uitnodiging aangemaakt maar mail kon niet verstuurd worden: {e}. '
              f'URL: {invite_url}', 'warning')

    db.log_audit('invite_sent', user_id=g.user['id'],
                 tenant_id=tenant_id,
                 details={'invite_email': invite_email, 'role': invite_role},
                 ip=request.remote_addr)
    referer = request.referrer or ''
    if f'/admin/tenants/{tenant_id}' in referer:
        return redirect(url_for('admin_tenant_detail', tenant_id=tenant_id))
    return redirect(url_for('admin_tenants'))


@app.route('/admin/tenants/<int:tenant_id>')
def admin_tenant_detail(tenant_id):
    import db
    if not g.user or g.user['role'] != 'superadmin':
        abort(403)
    tenant = db.get_tenant(tenant_id)
    if not tenant:
        abort(404)
    users = db.get_tenant_users(tenant_id)
    pending_invites = db.get_pending_invites(tenant_id)
    db.log_audit('superadmin_view_tenant_detail', user_id=g.user['id'],
                 tenant_id=tenant_id, ip=request.remote_addr)
    return render_template('admin_tenant_detail.html',
                           tenant=tenant,
                           users=users,
                           pending_invites=pending_invites,
                           app_name=APP_NAME,
                           page='admin')


@app.route('/admin/tenants/<int:tenant_id>/users/<int:user_id>', methods=['POST'])
def admin_tenant_user_action(tenant_id, user_id):
    import db
    from auth import hash_password
    if not g.user or g.user['role'] != 'superadmin':
        abort(403)

    tenant = db.get_tenant(tenant_id)
    if not tenant:
        abort(404)

    action = request.form.get('action', '')

    if action == 'deactivate':
        if user_id == g.user['id']:
            flash('Je kunt je eigen account niet deactiveren', 'error')
        else:
            db.deactivate_user(user_id)
            db.log_audit('user_deactivated', user_id=g.user['id'],
                         tenant_id=tenant_id, details={'target_user_id': user_id},
                         ip=request.remote_addr)
            flash('Gebruiker gedeactiveerd', 'success')

    elif action == 'reactivate':
        db.reactivate_user(user_id)
        db.log_audit('user_reactivated', user_id=g.user['id'],
                     tenant_id=tenant_id, details={'target_user_id': user_id},
                     ip=request.remote_addr)
        flash('Gebruiker opnieuw geactiveerd', 'success')

    elif action == 'update_profile':
        email     = request.form.get('email', '').strip()
        full_name = request.form.get('full_name', '').strip()
        role      = request.form.get('role', '').strip()
        _TENANT_ROLES = ('owner', 'staff')
        if role not in _TENANT_ROLES:
            db.log_audit('invalid_role_attempt', user_id=g.user['id'],
                         tenant_id=tenant_id,
                         details={'target_user_id': user_id, 'attempted_role': role},
                         ip=request.remote_addr)
            flash('Ongeldige rol — alleen owner en staff zijn toegestaan', 'error')
        else:
            db.update_user_profile(user_id,
                                   email=email or None,
                                   full_name=full_name or None,
                                   role=role)
            db.log_audit('user_profile_updated', user_id=g.user['id'],
                         tenant_id=tenant_id,
                         details={'target_user_id': user_id, 'email': email, 'role': role},
                         ip=request.remote_addr)
            flash('Profiel bijgewerkt', 'success')

    elif action == 'set_password':
        new_pw = request.form.get('new_password', '').strip()
        if len(new_pw) < 8:
            flash('Wachtwoord moet minimaal 8 tekens zijn', 'error')
        else:
            db.update_user_password(user_id, hash_password(new_pw))
            db.log_audit('user_password_reset', user_id=g.user['id'],
                         tenant_id=tenant_id, details={'target_user_id': user_id},
                         ip=request.remote_addr)
            flash('Wachtwoord bijgewerkt', 'success')

    else:
        flash('Onbekende actie', 'error')

    return redirect(url_for('admin_tenant_detail', tenant_id=tenant_id))


# ─── Unsubscribe (public, no login required) ─────────────────────────────────
@app.route('/unsubscribe/<token>')
def unsubscribe(token):
    import db
    import hashlib
    result = db.validate_unsubscribe_token(token)
    if not result:
        return render_template('unsubscribe.html', status='invalid', app_name=APP_NAME)

    tenant_id    = result['tenant_id']
    email        = result['email']
    tenant       = db.get_tenant(tenant_id)
    praktijknaam = (db.get_tenant_setting(tenant_id, 'praktijknaam', '')
                    or db.get_tenant_setting(tenant_id, 'from_name', '')
                    or (tenant['name'] if tenant else ''))

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    db.add_suppression(tenant_id, email,
                       reason='unsubscribe', source='email_link',
                       token_hash=token_hash)

    masked = (email[:2] + '***@' + email.split('@')[1]) if '@' in email else '***'
    return render_template('unsubscribe.html',
        status='success', praktijknaam=praktijknaam,
        masked_email=masked, app_name=APP_NAME)


# ─── Error handlers ───────────────────────────────────────────────────────────
@app.errorhandler(403)
def forbidden(e):
    return render_template('login.html', error='Geen toegang', app_name=APP_NAME), 403


if __name__ == '__main__':
    import db
    db.init_db()
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
