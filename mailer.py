import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from urllib.parse import quote as _urlquote

from dotenv import load_dotenv

load_dotenv()


# ─── FAIL-CLOSED test-mail safety ─────────────────────────────────────────────
# A real SMTP send must be impossible while tests run, even if a production .env
# is loaded. We capture the genuine smtplib.SMTP; in a test environment (and
# only when no test mock is installed) we swap in a no-op transport that records
# the attempt instead of opening a socket.
_ORIG_SMTP = smtplib.SMTP
sent_outbox = []


def _is_test_environment():
    return (
        'pytest' in sys.modules
        or bool(os.environ.get('PYTEST_CURRENT_TEST'))
        or os.environ.get('REVIEWFLOW_DISABLE_REAL_SMTP') == '1'
    )


class _BlockedSMTP:
    def __init__(self, host=None, port=None, timeout=None):
        sent_outbox.append({'host': host, 'port': port})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self, *a, **k):
        return (250, b'OK')

    def starttls(self, *a, **k):
        return (220, b'OK')

    def login(self, *a, **k):
        return (235, b'OK')

    def sendmail(self, from_addr, to_addrs, msg, *a, **k):
        sent_outbox.append({'from': from_addr, 'to': to_addrs})

    def quit(self):
        pass


def _smtp(host, port, *a, **k):
    cls = smtplib.SMTP
    if _is_test_environment() and cls is _ORIG_SMTP:
        return _BlockedSMTP(host, port)
    return cls(host, port, *a, **k)


def get_smtp_config():
    # First try app_settings DB, fall back to .env
    try:
        import db
        config = {
            'host':               db.get_app_setting('smtp_host') or os.getenv('SMTP_HOST', 'smtp.gmail.com'),
            'port':               int(db.get_app_setting('smtp_port') or os.getenv('SMTP_PORT', '587')),
            'user':               db.get_app_setting('smtp_user') or os.getenv('SMTP_USER', ''),
            'password':           db.get_app_setting('smtp_password') or os.getenv('SMTP_PASSWORD', ''),
            'from_email':         db.get_app_setting('from_email') or os.getenv('FROM_EMAIL', ''),
            'from_name':          db.get_app_setting('from_name') or os.getenv('FROM_NAME', 'Osteozuid'),
            'admin_email':        db.get_app_setting('admin_email') or os.getenv('ADMIN_EMAIL', ''),
            'google_review_link': db.get_app_setting('google_review_link') or os.getenv('GOOGLE_REVIEW_LINK', ''),
            'logo_url':           db.get_app_setting('logo_url') or '',
        }
    except Exception:
        config = {
            'host':               os.getenv('SMTP_HOST', 'smtp.gmail.com'),
            'port':               int(os.getenv('SMTP_PORT', '587')),
            'user':               os.getenv('SMTP_USER', ''),
            'password':           os.getenv('SMTP_PASSWORD', ''),
            'from_email':         os.getenv('FROM_EMAIL', ''),
            'from_name':          os.getenv('FROM_NAME', 'Osteozuid'),
            'admin_email':        os.getenv('ADMIN_EMAIL', ''),
            'google_review_link': os.getenv('GOOGLE_REVIEW_LINK', ''),
            'logo_url':           '',
        }
    missing = [k for k in ('user', 'password', 'from_email') if not config[k]]
    if missing:
        raise ValueError(f"Ontbrekende SMTP instellingen: {', '.join(missing).upper()}")
    return config


def build_body_plain(voornaam, google_review_link):
    return (
        f"Dag {voornaam},\n\n"
        "Bedankt voor uw bezoek aan Osteozuid.\n\n"
        "We proberen elke patiënt zo goed mogelijk te begeleiden. "
        "Als u enkele minuten tijd heeft, zouden we het enorm waarderen als u uw ervaring "
        "wilt delen via Google — dat helpt andere mensen om een praktijk te vinden die bij hen past.\n\n"
        f"Deel uw ervaring via Google:\n{google_review_link}\n\n"
        "Wilt u liever rechtstreeks iets aan ons doorgeven? "
        "Antwoord dan gerust op deze mail.\n\n"
        "Vriendelijke groeten,\n"
        "Osteozuid Groepspraktijk"
    )


def build_body_html(voornaam, google_review_link):
    return f"""<!DOCTYPE html>
<html lang="nl">
<head><meta charset="UTF-8"></head>
<body style="margin: 0; padding: 0; background-color: #ffffff;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #ffffff;">
    <tr>
      <td style="padding: 24px 0 0 0; font-family: Calibri, Candara, 'Segoe UI', Arial, sans-serif; font-size: 15px; line-height: 1.7; color: #1a1a1a; text-align: left;">
        <p style="margin: 0 0 16px 0;">Dag {voornaam},</p>
        <p style="margin: 0 0 16px 0;">Bedankt voor uw bezoek aan Osteozuid.</p>
        <p style="margin: 0 0 16px 0;">We proberen elke patiënt zo goed mogelijk te begeleiden. Als u enkele minuten tijd heeft, zouden we het enorm waarderen als u uw ervaring wilt delen via Google — dat helpt andere mensen om een praktijk te vinden die bij hen past.</p>
        <p style="margin: 0 0 16px 0;"><a href="{google_review_link}" style="color: #f28c00; text-decoration: underline; font-weight: bold;">Deel uw ervaring via Google</a></p>
        <p style="margin: 0 0 16px 0;">Wilt u liever rechtstreeks iets aan ons doorgeven? Antwoord dan gerust op deze mail.</p>
        <p style="margin: 0;">Vriendelijke groeten,<br>Osteozuid Groepspraktijk</p>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _render_template(body_html, voornaam, google_review_link, logo_url=''):
    rendered = body_html.replace('{{voornaam}}', voornaam)
    rendered = rendered.replace('{{google_link}}', google_review_link)
    logo_html = f'<img src="{logo_url}" alt="Logo" style="max-height:80px;display:block;margin-bottom:16px;">' if logo_url else ''
    rendered = rendered.replace('{{logo}}', logo_html)
    return rendered


def _build_unsubscribe_footer(unsubscribe_url):
    """Visible footer with the real, personal unsubscribe link (HTTPS)."""
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:28px;">'
        '<tr><td style="border-top:1px solid #e0e0e0;padding-top:14px;'
        "font-family:Calibri,Candara,'Segoe UI',Arial,sans-serif;font-size:12px;"
        'color:#999999;line-height:1.6;">'
        f'Geen reviewmails meer ontvangen? <a href="{unsubscribe_url}" '
        'style="color:#999999;text-decoration:underline;">Uitschrijven</a>.'
        '</td></tr></table>'
    )


# Inert placeholder shown in TEST/preview mails — never a working patient token.
_TEST_UNSUB_FOOTER = (
    '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:28px;">'
    '<tr><td style="border-top:1px solid #e0e0e0;padding-top:14px;'
    "font-family:Calibri,Candara,'Segoe UI',Arial,sans-serif;font-size:12px;"
    'color:#999999;line-height:1.6;">'
    '[TEST] In een echte mail staat hier de persoonlijke uitschrijflink.'
    '</td></tr></table>'
)


def _send(to_email, voornaam, google_review_link, smtp_config, subject=None,
          html_body=None, unsubscribe_url=None):
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject or "Uw ervaring bij Osteozuid"
    msg['From']    = formataddr((smtp_config['from_name'], smtp_config['from_email']))
    msg['To']      = to_email
    # `Date:` is required by RFC 5322 and explicitly checked by SpamAssassin
    # (MISSING_DATE = +1.4 spam points). smtplib does not add it by itself
    # and one.com's relay does not always backfill — so set it locally.
    msg['Date']    = formatdate(localtime=True)
    # `Message-ID:` strengthens DKIM coverage and eliminates the
    # MSGID_FROM_MTA_HEADER warning. Anchored on the sender domain so
    # DKIM signs it correctly.
    from_email = (smtp_config.get('from_email') or '').strip()
    sender_domain = from_email.split('@')[-1] if '@' in from_email else 'reviewflow.local'
    msg['Message-ID'] = make_msgid(domain=sender_domain)
    # `List-Unsubscribe:` (RFC 2369/8058). When a real HTTPS unsubscribe link is
    # available we advertise it for one-click (List-Unsubscribe-Post), with the
    # practice mailbox as mailto fallback. Test/preview mails pass no URL and
    # keep the mailto-only flavour.
    if unsubscribe_url:
        if from_email:
            subj = _urlquote('Uitschrijven van reviewmails')
            msg['List-Unsubscribe'] = f'<{unsubscribe_url}>, <mailto:{from_email}?subject={subj}>'
        else:
            msg['List-Unsubscribe'] = f'<{unsubscribe_url}>'
        msg['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
    elif from_email:
        subj = _urlquote('Uitschrijven van reviewmails')
        msg['List-Unsubscribe'] = f'<mailto:{from_email}?subject={subj}>'

    plain = build_body_plain(voornaam, google_review_link)
    html  = html_body or build_body_html(voornaam, google_review_link)
    msg.attach(MIMEText(plain, 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    with _smtp(smtp_config['host'], smtp_config['port']) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_config['user'], smtp_config['password'])
        server.sendmail(smtp_config['from_email'], to_email, msg.as_bytes())


def send_test_email(to_email, smtp_config):
    review_link = smtp_config['google_review_link'] or 'https://maps.google.com/'
    _send(to_email, 'Test', review_link, smtp_config)


def send_review_request(patient, smtp_config, template=None, unsubscribe_url=None):
    voornaam = patient.get('voornaam') or patient['naam'].split()[-1]
    review_link = smtp_config['google_review_link']
    if not review_link:
        raise ValueError("Google Review link is niet ingesteld")

    if template:
        subject = template['onderwerp']
        html_body = _render_template(template['body_html'], voornaam, review_link, smtp_config.get('logo_url', ''))
    else:
        subject = None
        html_body = build_body_html(voornaam, review_link)

    # Unsubscribe footer: the real personal link for actual sends, an inert
    # placeholder for tests/previews (unsubscribe_url is None there).
    footer = _build_unsubscribe_footer(unsubscribe_url) if unsubscribe_url else _TEST_UNSUB_FOOTER
    if '</body>' in html_body:
        html_body = html_body.replace('</body>', footer + '</body>', 1)
    else:
        html_body = html_body + footer

    _send(patient['email'], voornaam, review_link, smtp_config,
          subject=subject, html_body=html_body, unsubscribe_url=unsubscribe_url)
