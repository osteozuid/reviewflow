"""
Pytest fixtures for ReviewFlow Sprint 1 tests.

DB-tests worden overgeslagen als PostgreSQL niet beschikbaar is.
Stel TEST_DATABASE_URL in als env var (default: postgresql://localhost/reviewflow_test).
"""

import os
import sys
import uuid
from pathlib import Path

import psycopg2
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

TEST_DATABASE_URL = os.environ.get(
    'TEST_DATABASE_URL',
    'postgresql://localhost/reviewflow_test',
)
os.environ['DATABASE_URL'] = TEST_DATABASE_URL


# ── DB beschikbaarheid ────────────────────────────────────────────────────────
def _db_available():
    try:
        conn = psycopg2.connect(TEST_DATABASE_URL)
        conn.close()
        return True
    except Exception:
        return False


DB_AVAILABLE = _db_available()


def pytest_configure(config):
    config.addinivalue_line(
        'markers',
        'requires_db: test vereist een draaiende PostgreSQL-server',
    )


def pytest_runtest_setup(item):
    if item.get_closest_marker('requires_db') and not DB_AVAILABLE:
        pytest.skip('PostgreSQL niet beschikbaar — start postgres of stel TEST_DATABASE_URL in')


# ── Session-scoped schema ─────────────────────────────────────────────────────
@pytest.fixture(scope='session')
def pg_conn():
    if not DB_AVAILABLE:
        pytest.skip('PostgreSQL niet beschikbaar')
    base_url = TEST_DATABASE_URL.rsplit('/', 1)[0]
    db_name  = TEST_DATABASE_URL.rsplit('/', 1)[-1]
    try:
        admin = psycopg2.connect(base_url + '/postgres')
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute('SELECT 1 FROM pg_database WHERE datname = %s', (db_name,))
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{db_name}"')
        admin.close()
    except Exception:
        pass
    conn = psycopg2.connect(TEST_DATABASE_URL)
    yield conn
    # Teardown
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("""
            TRUNCATE TABLE audit_logs, document_acceptances, contacts,
                          review_snapshots, email_templates, schedule_config,
                          reviewed_names, blocked, suppression_list, unsubscribe_tokens,
                          import_log, review_log,
                          tenant_settings, invite_tokens, users, tenants
            RESTART IDENTITY CASCADE
        """)
    conn.commit()
    conn.close()


@pytest.fixture(scope='session')
def setup_schema(pg_conn):
    import db as db_module
    db_module.DATABASE_URL = TEST_DATABASE_URL
    db_module.init_db()


# ── Per-test fixtures ─────────────────────────────────────────────────────────
@pytest.fixture
def db_module(setup_schema):
    import db as _db
    _db.DATABASE_URL = TEST_DATABASE_URL
    return _db


@pytest.fixture
def app_client(db_module):
    import app as app_module
    app_module.app.config['TESTING'] = True
    app_module.app.config['SECRET_KEY'] = 'test-secret'
    with app_module.app.test_client() as client:
        yield client


def _uid():
    return uuid.uuid4().hex[:8]


@pytest.fixture
def superadmin(db_module):
    from auth import hash_password
    email = f'superadmin-{_uid()}@test.com'
    uid = db_module.create_user(
        email=email,
        password_hash=hash_password('superpass123'),
        role='superadmin',
    )
    user = db_module.get_user_by_id(uid)
    user['_password'] = 'superpass123'
    return user


@pytest.fixture
def tenant_a(db_module):
    tid = db_module.create_tenant(f'tenant-a-{_uid()}', 'Tenant A')
    return db_module.get_tenant(tid)


@pytest.fixture
def tenant_b(db_module):
    tid = db_module.create_tenant(f'tenant-b-{_uid()}', 'Tenant B')
    return db_module.get_tenant(tid)


@pytest.fixture
def owner_a(db_module, tenant_a):
    from auth import hash_password
    email = f'owner-a-{_uid()}@test.com'
    uid = db_module.create_user(
        email=email,
        password_hash=hash_password('ownerpass123'),
        role='owner',
        tenant_id=tenant_a['id'],
        full_name='Owner A',
    )
    user = db_module.get_user_by_id(uid)
    user['_password'] = 'ownerpass123'
    return user


@pytest.fixture
def owner_b(db_module, tenant_b):
    from auth import hash_password
    email = f'owner-b-{_uid()}@test.com'
    uid = db_module.create_user(
        email=email,
        password_hash=hash_password('ownerpass123'),
        role='owner',
        tenant_id=tenant_b['id'],
        full_name='Owner B',
    )
    user = db_module.get_user_by_id(uid)
    user['_password'] = 'ownerpass123'
    return user
