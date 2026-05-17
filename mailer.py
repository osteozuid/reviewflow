import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from dotenv import load_dotenv

load_dotenv()


def get_smtp_config(tenant_id):
    """Load SMTP config from tenant_settings (DB). No .env fallback for patient mail."""
    import db
    cfg = {
        'host':               db.get_tenant_setting(tenant_id, 'smtp_host', ''),
        'port':               int(db.get_tenant_setting(tenant_id, 'smtp_port', '587') or '587'),
        'user':               db.get_tenant_setting(tenant_id, 'smtp_user', ''),
        'password':           db.get_tenant_setting(tenant_id, 'smtp_password', ''),
        'from_email':         db.get_tenant_setting(tenant_id, 'from_email', ''),
        'from_name':          db.get_tenant_setting(tenant_id, 'from_name', ''),
        'admin_email':        db.get_tenant_setting(tenant_id, 'admin_email', ''),
        'google_review_link': db.get_tenant_setting(tenant_id, 'google_review_link', ''),
        'logo_url':           db.get_tenant_setting(tenant_id, 'logo_url', ''),
    }
    missing = [k for k in ('host', 'user', 'password', 'from_email') if not cfg[k]]
    if missing:
        raise ValueError(
            f"SMTP niet geconfigureerd. Ga naar Instellingen en vul in: "
            f"{', '.join(missing).upper()}"
        )
    return cfg


def get_system_smtp_config():
    """System-level SMTP for transactional mails (invites etc.), loaded from env vars."""
    cfg = {
        'host':       os.getenv('SYSTEM_SMTP_HOST', 'smtp.gmail.com'),
        'port':       int(os.getenv('SYSTEM_SMTP_PORT', '587')),
        'user':       os.getenv('SYSTEM_SMTP_USER', ''),
        'password':   os.getenv('SYSTEM_SMTP_PASSWORD', ''),
        'from_email': os.getenv('SYSTEM_FROM_EMAIL', ''),
        'from_name':  os.getenv('SYSTEM_FROM_NAME', 'ReviewFlow'),
    }
    missing = [k for k in ('user', 'password', 'from_email') if not cfg[k]]
    if missing:
        raise ValueError(f"Ontbrekende systeem-SMTP instellingen: {', '.join(missing).upper()}")
    return cfg


def _render_template(body_html, voornaam, google_review_link,
                     logo_url='', praktijknaam=''):
    rendered = body_html.replace('{{voornaam}}', voornaam)
    rendered = rendered.replace('{{praktijknaam}}', praktijknaam)
    rendered = rendered.replace('{{google_link}}', google_review_link)
    logo_html = (
        f'<img src="{logo_url}" alt="Logo" '
        f'style="max-height:80px;display:block;margin-bottom:16px;">'
        if logo_url else ''
    )
    rendered = rendered.replace('{{logo}}', logo_html)
    return rendered


def render_subject(onderwerp, praktijknaam=''):
    return onderwerp.replace('{{praktijknaam}}', praktijknaam)


def _build_body_plain(voornaam, google_review_link):
    return (
        f"Dag {voornaam},\n\n"
        "Bedankt voor uw bezoek aan onze praktijk.\n\n"
        "Als u een moment heeft, zouden we het enorm waarderen als u uw ervaring "
        "wilt delen via Google — dat helpt andere mensen om een geschikte praktijk te vinden.\n\n"
        f"Deel uw ervaring via Google:\n{google_review_link}\n\n"
        "Wilt u liever rechtstreeks iets doorgeven? Antwoord dan gerust op deze mail.\n\n"
        "Vriendelijke groeten"
    )


def _send(to_email, voornaam, google_review_link, smtp_config,
          subject=None, html_body=None):
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject or "Uw ervaring bij onze praktijk"
    msg['From']    = formataddr((smtp_config['from_name'], smtp_config['from_email']))
    msg['To']      = to_email

    plain = _build_body_plain(voornaam, google_review_link)
    html  = html_body or f"<p>Dag {voornaam},</p><p><a href='{google_review_link}'>Schrijf uw review</a></p>"

    msg.attach(MIMEText(plain, 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    with smtplib.SMTP(smtp_config['host'], smtp_config['port']) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_config['user'], smtp_config['password'])
        server.sendmail(smtp_config['from_email'], to_email, msg.as_bytes())


def send_review_request(patient, smtp_config, template=None):
    voornaam    = patient.get('voornaam') or patient['naam'].split()[-1]
    review_link = smtp_config['google_review_link']
    if not review_link:
        raise ValueError("Google Review link is niet ingesteld")

    if template:
        praktijknaam = smtp_config.get('from_name', '')
        subject   = render_subject(template['onderwerp'], praktijknaam)
        html_body = _render_template(
            template['body_html'], voornaam, review_link,
            smtp_config.get('logo_url', ''), praktijknaam,
        )
        _send(patient['email'], voornaam, review_link, smtp_config,
              subject=subject, html_body=html_body)
    else:
        _send(patient['email'], voornaam, review_link, smtp_config)


def send_test_email(to_email, smtp_config):
    review_link = smtp_config.get('google_review_link') or 'https://maps.google.com/'
    _send(to_email, 'Test', review_link, smtp_config,
          subject='[TEST] ReviewFlow testmail')


def send_invite_email(to_email, tenant_name, invite_url):
    """Send invite email using system SMTP (not tenant SMTP)."""
    app_name = os.getenv('APP_NAME', 'ReviewFlow')
    smtp = get_system_smtp_config()

    html = f"""<!DOCTYPE html>
<html lang="nl">
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#f5f5f5;padding:32px;">
<div style="max-width:500px;margin:0 auto;background:#fff;border-radius:8px;padding:32px;">
  <h2 style="color:#f28c00;margin-top:0;">{app_name}</h2>
  <p>U bent uitgenodigd om <strong>{tenant_name}</strong> te beheren in {app_name}.</p>
  <p>Klik op de knop hieronder om uw account aan te maken:</p>
  <p style="margin:24px 0;">
    <a href="{invite_url}"
       style="background:#f28c00;color:#fff;text-decoration:none;padding:12px 28px;border-radius:4px;font-size:14px;font-weight:600;display:inline-block;">
      Account aanmaken
    </a>
  </p>
  <p style="color:#888;font-size:13px;">
    Deze uitnodiging verloopt na 7 dagen.<br>
    Als u deze mail niet verwacht heeft, kunt u hem negeren.
  </p>
</div>
</body>
</html>"""

    plain = (
        f"U bent uitgenodigd voor {tenant_name} in {app_name}.\n\n"
        f"Maak uw account aan via:\n{invite_url}\n\n"
        "Deze uitnodiging verloopt na 7 dagen."
    )

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"Uitnodiging voor {tenant_name} — {app_name}"
    msg['From']    = formataddr((smtp['from_name'], smtp['from_email']))
    msg['To']      = to_email
    msg.attach(MIMEText(plain, 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    with smtplib.SMTP(smtp['host'], smtp['port']) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp['user'], smtp['password'])
        server.sendmail(smtp['from_email'], to_email, msg.as_bytes())
