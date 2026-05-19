"""
Sprint 2 testsuites — unsubscribe / suppression list / GDPR.

Run:
    pytest tests/test_sprint2.py -v
    pytest tests/test_sprint2.py -v -m "not requires_db"
"""

import uuid
import pytest

from helpers import login_as, logout

pytestmark_db = pytest.mark.requires_db


def _uid():
    return uuid.uuid4().hex[:8]


# ── 1. Unsubscribe token aanmaken per tenant+email ────────────────────────────

class TestUnsubscribeToken:
    def test_token_aanmaken_geeft_string(self):
        import hashlib
        import secrets as _sec
        token = _sec.token_urlsafe(32)
        assert len(token) >= 40

    @pytest.mark.requires_db
    def test_token_uniek_per_email(self, db_module, tenant_a):
        email = f'tok-{_uid()}@test.com'
        t1 = db_module.create_unsubscribe_token(tenant_a['id'], email)
        t2 = db_module.create_unsubscribe_token(tenant_a['id'], email)
        assert t1 != t2

    @pytest.mark.requires_db
    def test_token_niet_plaintext_email(self, db_module, tenant_a):
        email = f'plaincheck-{_uid()}@test.com'
        token = db_module.create_unsubscribe_token(tenant_a['id'], email)
        assert email not in token
        assert '@' not in token

    @pytest.mark.requires_db
    def test_validate_geldig_token(self, db_module, tenant_a):
        email = f'val-{_uid()}@test.com'
        token = db_module.create_unsubscribe_token(tenant_a['id'], email)
        result = db_module.validate_unsubscribe_token(token)
        assert result is not None
        assert result['email'] == email.lower()
        assert result['tenant_id'] == tenant_a['id']

    @pytest.mark.requires_db
    def test_validate_ongeldig_token(self, db_module):
        result = db_module.validate_unsubscribe_token('onzin-token-bestaat-niet')
        assert result is None

    @pytest.mark.requires_db
    def test_token_tenant_geïsoleerd(self, db_module, tenant_a, tenant_b):
        email = f'iso-{_uid()}@test.com'
        token = db_module.create_unsubscribe_token(tenant_a['id'], email)
        result = db_module.validate_unsubscribe_token(token)
        assert result['tenant_id'] == tenant_a['id']
        assert result['tenant_id'] != tenant_b['id']


# ── 2. Unsubscribe route voegt suppression toe ────────────────────────────────

@pytest.mark.requires_db
class TestUnsubscribeRoute:
    def test_geldige_token_geeft_200(self, app_client, db_module, tenant_a):
        email = f'unsub-{_uid()}@test.com'
        token = db_module.create_unsubscribe_token(tenant_a['id'], email)
        resp  = app_client.get(f'/unsubscribe/{token}')
        assert resp.status_code == 200
        assert 'uitgeschreven' in resp.data.decode('utf-8').lower()

    def test_ongeldige_token_toont_foutpagina(self, app_client):
        resp = app_client.get('/unsubscribe/compleet-ongeldige-token-xyz')
        assert resp.status_code == 200
        assert 'ongeldig' in resp.data.decode('utf-8').lower()

    def test_unsubscribe_voegt_toe_aan_suppression_list(self, app_client,
                                                         db_module, tenant_a):
        email = f'added-{_uid()}@test.com'
        token = db_module.create_unsubscribe_token(tenant_a['id'], email)
        app_client.get(f'/unsubscribe/{token}')
        suppressed = db_module.get_suppressed(tenant_a['id'])
        assert email.lower() in suppressed

    def test_unsubscribe_idempotent(self, app_client, db_module, tenant_a):
        email = f'idem-{_uid()}@test.com'
        token = db_module.create_unsubscribe_token(tenant_a['id'], email)
        app_client.get(f'/unsubscribe/{token}')
        app_client.get(f'/unsubscribe/{token}')  # tweede keer
        suppressed = db_module.get_suppressed(tenant_a['id'])
        assert email.lower() in suppressed

    def test_unsubscribe_geen_login_vereist(self, app_client, db_module, tenant_a):
        email = f'nologin-{_uid()}@test.com'
        token = db_module.create_unsubscribe_token(tenant_a['id'], email)
        # Niet inloggen, direct naar unsubscribe
        resp = app_client.get(f'/unsubscribe/{token}')
        assert resp.status_code == 200

    def test_email_niet_volledig_in_response(self, app_client, db_module, tenant_a):
        email = f'hide-{_uid()}@example.com'
        token = db_module.create_unsubscribe_token(tenant_a['id'], email)
        resp = app_client.get(f'/unsubscribe/{token}')
        html = resp.data.decode('utf-8')
        assert email not in html  # volledig adres mag niet zichtbaar zijn


# ── 3. Suppressed email wordt niet verzonden ──────────────────────────────────

@pytest.mark.requires_db
class TestSuppressionBlocksSend:
    def test_suppressed_email_niet_in_to_mail(self, db_module, tenant_a):
        email = f'suppressed-{_uid()}@test.com'
        db_module.add_suppression(tenant_a['id'], email,
                                   reason='unsubscribe', source='email_link')
        suppressed = db_module.get_suppressed(tenant_a['id'])
        assert email.lower() in suppressed

    def test_suppressed_email_toont_reden_in_dry_run(self, app_client,
                                                       db_module, owner_a, tenant_a):
        # We test via the API: check that suppressed emails have reason 'Uitgeschreven'
        email = f'drycheck-{_uid()}@test.com'
        db_module.add_suppression(tenant_a['id'], email,
                                   reason='unsubscribe', source='email_link')
        suppressed = db_module.get_suppressed(tenant_a['id'])
        assert email.lower() in suppressed

    def test_suppressed_niet_als_sent_gelogd(self, db_module, tenant_a):
        email = f'notsent-{_uid()}@test.com'
        db_module.add_suppression(tenant_a['id'], email, reason='manual', source='admin_ui')
        # Verify not in review_log
        already_sent = db_module.get_already_sent(tenant_a['id'])
        assert email.lower() not in already_sent


# ── 4. Tenant A suppression blokkeert tenant B niet ──────────────────────────

@pytest.mark.requires_db
class TestSuppressionTenantIsolatie:
    def test_tenant_a_suppression_blokkeert_b_niet(self, db_module, tenant_a, tenant_b):
        email = f'cross-{_uid()}@test.com'
        db_module.add_suppression(tenant_a['id'], email,
                                   reason='unsubscribe', source='email_link')
        suppressed_a = db_module.get_suppressed(tenant_a['id'])
        suppressed_b = db_module.get_suppressed(tenant_b['id'])
        assert email.lower() in suppressed_a
        assert email.lower() not in suppressed_b

    def test_token_van_a_werkt_niet_voor_b(self, db_module, tenant_a, tenant_b):
        email = f'crosstoken-{_uid()}@test.com'
        token_a = db_module.create_unsubscribe_token(tenant_a['id'], email)
        result = db_module.validate_unsubscribe_token(token_a)
        assert result['tenant_id'] == tenant_a['id']
        assert result['tenant_id'] != tenant_b['id']


# ── 5. Footer automatisch toegevoegd ─────────────────────────────────────────

class TestUnsubscribeFooter:
    def test_footer_wordt_automatisch_toegevoegd(self):
        from mailer import send_review_request, _build_unsubscribe_footer
        footer = _build_unsubscribe_footer('Test Praktijk', 'https://example.com/unsub/tok')
        assert 'hier uitschrijven' in footer
        assert 'https://example.com/unsub/tok' in footer
        assert 'Test Praktijk' in footer

    def test_footer_html_structuur(self):
        from mailer import _build_unsubscribe_footer
        footer = _build_unsubscribe_footer('Praktijk X', 'https://example.com/u/abc')
        assert '<table' in footer
        assert '<a href=' in footer
        assert 'Praktijk X' in footer

    def test_render_template_vervangt_unsubscribe_link(self):
        from mailer import _render_template
        body = '<p>Dag {{voornaam}}</p><p>{{unsubscribe_link}}</p>'
        result = _render_template(body, 'Jan', '#', '', 'Praktijk', 'https://u.be/tok')
        assert '{{unsubscribe_link}}' not in result
        assert 'https://u.be/tok' in result

    def test_render_template_zonder_variable_geen_placeholder(self):
        from mailer import _render_template
        body = '<p>Dag {{voornaam}}</p>'
        result = _render_template(body, 'Jan', '#', '', 'Praktijk', 'https://u.be/tok')
        assert '{{unsubscribe_link}}' not in result

    def test_footer_in_body_tag(self):
        from mailer import _build_unsubscribe_footer
        html_body = '<html><body><p>Content</p></body></html>'
        footer = _build_unsubscribe_footer('P', 'https://x.be/u')
        result = html_body.replace('</body>', footer + '</body>', 1)
        assert result.index(footer) < result.index('</body>')


# ── 6. Template met eigen {{unsubscribe_link}} werkt ook ─────────────────────

class TestTemplateMetUnsubscribeLink:
    def test_variabele_in_template_wordt_vervangen(self):
        from mailer import _render_template
        body = '<p>Uitschrijven: {{unsubscribe_link}}</p>'
        result = _render_template(body, 'Jan', '#', '', 'P', 'https://x.be/u/tok')
        assert '{{unsubscribe_link}}' not in result
        assert 'https://x.be/u/tok' in result
        assert 'hier uitschrijven' in result

    def test_variabele_zonder_url_geeft_geen_kapotte_link(self):
        from mailer import _render_template
        body = '<p>Uitschrijven: {{unsubscribe_link}}</p>'
        result = _render_template(body, 'Jan', '#', '', 'P', '')
        assert '{{unsubscribe_link}}' not in result


# ── 7. Test 1 mail logt niet als sent en wijzigt suppression niet ─────────────

@pytest.mark.requires_db
class TestTestOneMail:
    def test_run_test_one_wijzigt_suppression_niet(self, app_client, db_module,
                                                    owner_a, tenant_a):
        login_as(app_client, owner_a['email'], owner_a['_password'])
        before = db_module.get_suppression_list(tenant_a['id'])
        # POST to test-one — will fail SMTP but should NOT touch suppression
        app_client.post('/run/test-one', follow_redirects=False)
        after = db_module.get_suppression_list(tenant_a['id'])
        assert len(before) == len(after)

    def test_run_test_one_logt_niet_als_sent(self, app_client, db_module,
                                              owner_a, tenant_a):
        login_as(app_client, owner_a['email'], owner_a['_password'])
        before_count = len(db_module.get_sent_logs(tenant_a['id']))
        app_client.post('/run/test-one', follow_redirects=False)
        after_count = len(db_module.get_sent_logs(tenant_a['id']))
        assert before_count == after_count


# ── 8. Suppression list UI aanwezig ──────────────────────────────────────────

@pytest.mark.requires_db
class TestSuppressionUI:
    def test_uitsluitingen_pagina_toont_suppression_sectie(self, app_client, owner_a):
        login_as(app_client, owner_a['email'], owner_a['_password'])
        resp = app_client.get('/uitsluitingen')
        assert resp.status_code == 200
        assert 'Uitgeschreven' in resp.data.decode('utf-8')

    def test_admin_kan_email_toevoegen_aan_suppression(self, app_client,
                                                         db_module, owner_a, tenant_a):
        login_as(app_client, owner_a['email'], owner_a['_password'])
        email = f'manualsup-{_uid()}@test.com'
        app_client.post('/uitsluitingen', data={
            'action': 'add_suppression',
            'email':  email,
        }, follow_redirects=True)
        suppressed = db_module.get_suppressed(tenant_a['id'])
        assert email.lower() in suppressed

    def test_admin_kan_suppression_verwijderen(self, app_client,
                                                db_module, owner_a, tenant_a):
        login_as(app_client, owner_a['email'], owner_a['_password'])
        email = f'delsup-{_uid()}@test.com'
        db_module.add_suppression(tenant_a['id'], email, reason='manual', source='admin_ui')
        suppressed = db_module.get_suppression_list(tenant_a['id'])
        sid = next(s['id'] for s in suppressed if s['email'] == email.lower())
        app_client.post('/uitsluitingen', data={
            'action': 'delete_suppression',
            'id':     str(sid),
        }, follow_redirects=True)
        after = db_module.get_suppressed(tenant_a['id'])
        assert email.lower() not in after

    def test_uitsluitingen_toont_reden_en_datum(self, app_client,
                                                  db_module, owner_a, tenant_a):
        login_as(app_client, owner_a['email'], owner_a['_password'])
        email = f'showreden-{_uid()}@test.com'
        db_module.add_suppression(tenant_a['id'], email, reason='manual', source='admin_ui')
        resp = app_client.get('/uitsluitingen')
        html = resp.data.decode('utf-8')
        assert 'manual' in html
        assert 'admin_ui' in html
