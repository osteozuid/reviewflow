"""
Pytest fixtures for ReviewFlow Sprint 1 tests.

Requires a running PostgreSQL server.
Set TEST_DATABASE_URL env var (default: postgresql://localhost/reviewflow_test).
"""

import os
import sys
from pathlib import Path

import psycopg2
import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

TEST_DATABASE_URL = os.environ.get(
    'TEST_DATABASE_URL',
    'postgresql://localhost/reviewflow_test',
)

# Override db module DATABASE_URL before importing db
os.environ['DATABASE_URL'] = TEST_DATABASE_URL


@pytest.fixture(scope='session')
def pg_conn():
    """Raw psycopg2 connection to the test database (creates DB if needed)."""
    # Try to create the test DB if it doesn't exist
    base_url = TEST_DATABASE_URL.rsplit('/', 1)[0]
    db_name  = TEST_DATABASE_URL.rsplit('/', 1)[-1]
    try:
        admin_conn = psycopg2.connect(base_url + '/postgres')
        admin_conn.autocommit = True
        with admin_conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{db_name}"')
        admin_conn.close()
    except Exception:
        pass  # DB may already exist or user may not have CREATE privilege

    conn = psycopg2.connect(TEST_DATABASE_URL)
    yield conn
    conn.close()


@pytest.fixture(scope='session', autouse=True)
def setup_schema(pg_conn):
    """Create all tables once per test session."""
    import db as db_module
    db_module.DATABASE_URL = TEST_DATABASE_URL
    db_module.init_db()
    yield
    # Teardown: drop all data (keep schema)
    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute("""
            TRUNCATE TABLE audit_logs, document_acceptances, contacts,
                          review_snapshots, email_templates, schedule_config,
                          reviewed_names, blocked, import_log, review_log,
                          tenant_settings, invite_tokens, users, tenants
            RESTART IDENTITY CASCADE
        """)
    pg_conn.commit()


@pytest.fixture
def db_module():
    import db as _db
    _db.DATABASE_URL = TEST_DATABASE_URL
    return _db


@pytest.fixture
def app_client(db_module):
    """Flask test client with app configured for tests."""
    import app as app_module
    app_module.app.config['TESTING'] = True
    app_module.app.config['SECRET_KEY'] = 'test-secret'
    app_module.app.config['WTF_CSRF_ENABLED'] = False
    with app_module.app.test_client() as client:
        yield client


@pytest.fixture
def superadmin(db_module):
    from auth import hash_password
    uid = db_module.create_user(
        email='superadmin@test.com',
        password_hash=hash_password('superpass123'),
        role='superadmin',
    )
    return db_module.get_user_by_id(uid)


@pytest.fixture
def tenant_a(db_module):
    tid = db_module.create_tenant('tenant-a', 'Tenant A')
    return db_module.get_tenant(tid)


@pytest.fixture
def tenant_b(db_module):
    tid = db_module.create_tenant('tenant-b', 'Tenant B')
    return db_module.get_tenant(tid)


@pytest.fixture
def owner_a(db_module, tenant_a):
    from auth import hash_password
    uid = db_module.create_user(
        email='owner-a@test.com',
        password_hash=hash_password('ownerpass123'),
        role='owner',
        tenant_id=tenant_a['id'],
        full_name='Owner A',
    )
    return db_module.get_user_by_id(uid)


@pytest.fixture
def owner_b(db_module, tenant_b):
    from auth import hash_password
    uid = db_module.create_user(
        email='owner-b@test.com',
        password_hash=hash_password('ownerpass123'),
        role='owner',
        tenant_id=tenant_b['id'],
        full_name='Owner B',
    )
    return db_module.get_user_by_id(uid)


def login_as(client, email, password):
    return client.post('/login', data={'email': email, 'password': password},
                       follow_redirects=True)


def logout(client):
    return client.get('/logout', follow_redirects=True)
