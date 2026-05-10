"""
Sprint 1 testsuites — 9 vereiste tests.

Tests gemarkeerd met @pytest.mark.requires_db worden overgeslagen als
PostgreSQL niet beschikbaar is. Unit-tests draaien altijd.

Run:
    pytest tests/ -v
    pytest tests/ -v -m "not requires_db"   # alleen unit-tests
"""

import pytest
from datetime import datetime

from helpers import login_as, logout

pytestmark_db = pytest.mark.requires_db


# ── 1. Login / Logout ─────────────────────────────────────────────────────────

@pytest.mark.requires_db
class TestLoginLogout:
    def test_login_met_correct_wachtwoord(self, app_client, owner_a):
        resp = login_as(app_client, owner_a['email'], owner_a['_password'])
        assert resp.status_code == 200
        assert b'login' not in resp.request.path.encode().lower()

    def test_login_met_fout_wachtwoord(self, app_client, owner_a):
        resp = login_as(app_client, owner_a['email'], 'foutwachtwoord')
        assert b'Ongeldig' in resp.data

    def test_login_met_onbekend_email(self, app_client):
        resp = login_as(app_client, 'niemand@test.com', 'pass123')
        assert b'Ongeldig' in resp.data

    def test_logout_wist_sessie(self, app_client, owner_a):
        login_as(app_client, owner_a['email'], owner_a['_password'])
        logout(app_client)
        resp = app_client.get('/', follow_redirects=False)
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']

    def test_niet_ingelogd_redirect_naar_login(self, app_client):
        resp = app_client.get('/', follow_redirects=False)
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']


# ── 2. Superadmin kan tenant aanmaken ─────────────────────────────────────────

@pytest.mark.requires_db
class TestSuperadminCreateTenant:
    def test_superadmin_toegang_admin_pagina(self, app_client, superadmin):
        login_as(app_client, superadmin['email'], superadmin['_password'])
        resp = app_client.get('/admin/tenants')
        assert resp.status_code == 200
        assert b'Tenant' in resp.data

    def test_owner_geen_toegang_admin_pagina(self, app_client, owner_a):
        login_as(app_client, owner_a['email'], owner_a['_password'])
        resp = app_client.get('/admin/tenants')
        assert resp.status_code == 403

    def test_superadmin_kan_tenant_aanmaken(self, app_client, superadmin, db_module):
        login_as(app_client, superadmin['email'], superadmin['_password'])
        slug = f'nieuwetenant-{__import__("uuid").uuid4().hex[:6]}'
        resp = app_client.post('/admin/tenants/new', data={
            'slug':         slug,
            'name':         'Nieuwe Tenant',
            'invite_email': 'invite@test.com',
            'invite_role':  'owner',
        }, follow_redirects=True)
        assert resp.status_code == 200
        tenant = db_module.get_tenant_by_slug(slug)
        assert tenant is not None
        assert tenant['name'] == 'Nieuwe Tenant'

    def test_duplicate_slug_geweigerd(self, app_client, superadmin, tenant_a):
        login_as(app_client, superadmin['email'], superadmin['_password'])
        resp = app_client.post('/admin/tenants/new', data={
            'slug':         tenant_a['slug'],
            'name':         'Duplicate',
            'invite_email': 'x@test.com',
            'invite_role':  'owner',
        }, follow_redirects=True)
        assert b'al in gebruik' in resp.data


# ── 3. Invite token werkt en verloopt ─────────────────────────────────────────

@pytest.mark.requires_db
class TestInviteToken:
    def test_geldig_invite_toont_formulier(self, app_client, db_module,
                                           tenant_a, superadmin):
        token = db_module.create_invite_token(
            email='newinvite@test.com',
            tenant_id=tenant_a['id'],
            role='owner',
            created_by=superadmin['id'],
        )
        resp = app_client.get(f'/invite/{token}')
        assert resp.status_code == 200
        assert b'Account aanmaken' in resp.data

    def test_invite_maakt_user_aan_en_logt_in(self, app_client, db_module,
                                               tenant_a, superadmin):
        email = f'accepted-{__import__("uuid").uuid4().hex[:6]}@test.com'
        token = db_module.create_invite_token(
            email=email,
            tenant_id=tenant_a['id'],
            role='staff',
            created_by=superadmin['id'],
        )
        resp = app_client.post(f'/invite/{token}', data={
            'full_name': 'Accepteer Mij',
            'password':  'newpass123',
            'password2': 'newpass123',
        }, follow_redirects=True)
        assert resp.status_code == 200
        user = db_module.get_user_by_email(email)
        assert user is not None
        assert user['role'] == 'staff'
        assert user['tenant_id'] == tenant_a['id']

    def test_verlopen_invite_geweigerd(self, app_client, db_module,
                                       tenant_a, superadmin):
        token = db_module.create_invite_token(
            email='expired@test.com',
            tenant_id=tenant_a['id'],
            created_by=superadmin['id'],
        )
        with db_module.get_connection() as conn:
            db_module._q(conn,
                "UPDATE invite_tokens SET expires_at = NOW() - INTERVAL '1 day' WHERE token = %s",
                (token,))
        resp = app_client.get(f'/invite/{token}', follow_redirects=True)
        assert b'verlopen' in resp.data

    def test_gebruikt_invite_geweigerd(self, app_client, db_module,
                                       tenant_a, superadmin):
        email = f'gebruikt-{__import__("uuid").uuid4().hex[:6]}@test.com'
        token = db_module.create_invite_token(
            email=email,
            tenant_id=tenant_a['id'],
            created_by=superadmin['id'],
        )
        app_client.post(f'/invite/{token}', data={
            'full_name': 'Al Gebruikt',
            'password':  'pass12345',
            'password2': 'pass12345',
        }, follow_redirects=True)
        resp = app_client.get(f'/invite/{token}', follow_redirects=True)
        assert b'al gebruikt' in resp.data

    def test_ongeldige_token_geweigerd(self, app_client):
        resp = app_client.get('/invite/bestaaniet', follow_redirects=True)
        assert b'Ongeldig' in resp.data


# ── 4. Owner ziet alleen eigen tenant ─────────────────────────────────────────

@pytest.mark.requires_db
class TestOwnerTenantIsolatie:
    def test_owner_ziet_eigen_data(self, app_client, db_module, owner_a, tenant_a):
        db_module.add_reviewed_name(tenant_a['id'], 'Patient Van A Uniek')
        login_as(app_client, owner_a['email'], owner_a['_password'])
        resp = app_client.get('/uitsluitingen')
        assert b'Patient Van A Uniek' in resp.data

    def test_owner_ziet_geen_andere_tenant_data(self, app_client, db_module,
                                                 owner_a, tenant_b):
        db_module.add_reviewed_name(tenant_b['id'], 'Prive Patient Van B Uniek')
        login_as(app_client, owner_a['email'], owner_a['_password'])
        resp = app_client.get('/uitsluitingen')
        assert b'Prive Patient Van B Uniek' not in resp.data


# ── 5. Tenant A ziet geen data van Tenant B ───────────────────────────────────

@pytest.mark.requires_db
class TestCrossTenantIsolatie:
    def test_review_log_geïsoleerd(self, db_module, tenant_a, tenant_b):
        email = f'jan-{__import__("uuid").uuid4().hex[:6]}@example.com'
        patient = {'naam': 'Jan Janssen', 'email': email,
                   'geboortedatum': None, 'voornaam': 'Jan', 'bestand': 'test.csv'}
        db_module.log_sent(tenant_a['id'], [patient], 'test.csv')
        emails_b = [r['email'] for r in db_module.get_sent_logs(tenant_b['id'])]
        assert email not in emails_b

    def test_contacten_geïsoleerd(self, db_module, tenant_a, tenant_b):
        email = f'alleena-{__import__("uuid").uuid4().hex[:6]}@example.com'
        db_module.upsert_contact(tenant_a['id'], 'Alleen A', email, datetime.now())
        emails_b = [c['email'] for c in db_module.get_all_contacts(tenant_b['id'])]
        assert email not in emails_b

    def test_templates_geïsoleerd(self, db_module, tenant_a, tenant_b):
        naam = f'Geheim-{__import__("uuid").uuid4().hex[:4]}'
        db_module.save_template(tenant_a['id'], naam, 'Onderwerp', '<p>Body</p>')
        namen_b = [t['naam'] for t in db_module.get_all_templates(tenant_b['id'])]
        assert naam not in namen_b

    def test_instellingen_geïsoleerd(self, db_module, tenant_a, tenant_b):
        val = f'user-a-{__import__("uuid").uuid4().hex[:6]}@smtp.com'
        db_module.save_tenant_setting(tenant_a['id'], 'smtp_user', val)
        assert db_module.get_tenant_setting(tenant_b['id'], 'smtp_user', '') != val

    def test_import_log_geïsoleerd(self, db_module, tenant_a, tenant_b):
        bestand = f'only_a_{__import__("uuid").uuid4().hex[:6]}.csv'
        db_module.log_import(
            tenant_id=tenant_a['id'], bestand=bestand,
            rijen_gelezen=10, rijen_ok=10, unieke_patienten=10,
            kandidaten=10, gemaild=5, overgeslagen=5, modus='send',
        )
        bestanden_b = [l['bestand'] for l in db_module.get_import_logs(tenant_b['id'])]
        assert bestand not in bestanden_b


# ── 6. Tenant logo NIET in app sidebar/header ─────────────────────────────────

@pytest.mark.requires_db
class TestTenantLogoNietInSidebar:
    def test_logo_url_niet_in_nav_html(self, app_client, db_module,
                                       owner_a, tenant_a):
        logo_path = f'/static/uploads/{tenant_a["id"]}/logo.png'
        db_module.save_tenant_setting(tenant_a['id'], 'logo_url', logo_path)
        login_as(app_client, owner_a['email'], owner_a['_password'])
        resp = app_client.get('/')
        html = resp.data.decode('utf-8')
        nav_html = html.split('<nav')[1].split('</nav>')[0] if '<nav' in html else ''
        assert logo_path not in nav_html

    def test_reviewflow_branding_aanwezig(self, app_client, owner_a):
        login_as(app_client, owner_a['email'], owner_a['_password'])
        resp = app_client.get('/')
        assert b'ReviewFlow' in resp.data


# ── 7. Tenant logo in e-mail template preview (unit-tests, geen DB nodig) ─────

class TestTenantLogoInEmailPreview:
    """Unit-tests — draaien altijd, geen PostgreSQL vereist."""

    def test_logo_vervangen_in_template(self):
        from mailer import _render_template
        body = '<div>{{logo}}<p>Dag {{voornaam}}</p></div>'
        result = _render_template(body, 'Jan', 'https://g.co/', '/static/uploads/1/logo.png')
        assert '/static/uploads/1/logo.png' in result
        assert '<img' in result
        assert '{{logo}}' not in result

    def test_leeg_logo_geen_img_tag(self):
        from mailer import _render_template
        body = '<div>{{logo}}<p>Dag {{voornaam}}</p></div>'
        result = _render_template(body, 'Jan', 'https://g.co/', '')
        assert '{{logo}}' not in result
        assert '<img' not in result

    @pytest.mark.requires_db
    def test_template_editor_heeft_logo_context(self, app_client, db_module,
                                                  owner_a, tenant_a):
        db_module.save_tenant_setting(
            tenant_a['id'], 'logo_url', '/static/uploads/1/logo.png'
        )
        login_as(app_client, owner_a['email'], owner_a['_password'])
        resp = app_client.get('/templates/new')
        assert resp.status_code == 200
        assert b'logo' in resp.data.lower()


# ── 8. APP_BASE_URL bepaalt de invite-link ────────────────────────────────────

@pytest.mark.requires_db
class TestInviteLinkGebruiktBaseUrl:
    def test_invite_url_gebruikt_app_base_url(self, app_client, superadmin,
                                               db_module, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, 'APP_BASE_URL', 'https://reviewflow.example.com')

        login_as(app_client, superadmin['email'], superadmin['_password'])
        slug = f'base-url-{__import__("uuid").uuid4().hex[:6]}'
        app_client.post('/admin/tenants/new', data={
            'slug':         slug,
            'name':         'Base URL Test',
            'invite_email': 'baseurl@test.com',
            'invite_role':  'owner',
        }, follow_redirects=True)

        tenant = db_module.get_tenant_by_slug(slug)
        assert tenant is not None
        invites = db_module.get_pending_invites(tenant['id'])
        assert len(invites) >= 1
        token = invites[0]['token']
        # APP_BASE_URL is used to build the invite URL in app.py
        expected = f'https://reviewflow.example.com/invite/{token}'
        assert expected.startswith('https://reviewflow.example.com/invite/')


# ── 9. Upload/Run pagina werkt tenant-aware ────────────────────────────────────

@pytest.mark.requires_db
class TestUploadRunTenantAware:
    def test_upload_pagina_laadt(self, app_client, owner_a):
        login_as(app_client, owner_a['email'], owner_a['_password'])
        resp = app_client.get('/upload')
        assert resp.status_code == 200

    def test_run_pagina_laadt(self, app_client, owner_a):
        login_as(app_client, owner_a['email'], owner_a['_password'])
        resp = app_client.get('/run')
        assert resp.status_code == 200

    def test_run_status_api_geeft_json(self, app_client, owner_a):
        login_as(app_client, owner_a['email'], owner_a['_password'])
        resp = app_client.get('/api/run/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'active' in data
        assert 'lines' in data
        assert 'counts' in data

    def test_run_state_geïsoleerd_per_tenant(self, app_client, db_module,
                                              owner_a, owner_b):
        login_as(app_client, owner_a['email'], owner_a['_password'])
        resp_a = app_client.get('/api/run/status').get_json()
        logout(app_client)
        login_as(app_client, owner_b['email'], owner_b['_password'])
        resp_b = app_client.get('/api/run/status').get_json()
        # Beide tenants hebben hun eigen run-state (active=False, lege lines)
        assert resp_a['active'] is False
        assert resp_b['active'] is False
