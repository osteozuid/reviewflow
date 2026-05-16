#!/usr/bin/env python
"""
Local test bootstrap — safe setup for UI testing.

Initializes a clean PostgreSQL database with:
- Empty schema (no patient data)
- Superadmin account
- Two test tenants (testpraktijk, fysiozuid)
- Owner users for each tenant
- Valid invite tokens for testing

IMPORTANT:
- This script uses ONLY TEST DATA (fake emails, fake names)
- No real patient information is imported
- Safe to run locally without affecting production

Usage:
    python scripts/bootstrap_local_test.py
"""

import os
import sys
from pathlib import Path

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import db
from auth import hash_password


def main():
    print("=" * 70)
    print("ReviewFlow SaaS — Local Test Bootstrap (TEST DATA ONLY)")
    print("=" * 70)

    # ── 1. Initialize schema ──────────────────────────────────────────────
    print("\n[1/5] Initializing PostgreSQL schema...")
    db.init_db()
    print("      [OK] Schema created")

    # ── 2. Create superadmin ──────────────────────────────────────────────
    print("\n[2/5] Creating superadmin account...")
    superadmin_email = "admin@test.com"
    existing_admin = db.get_user_by_email(superadmin_email)
    if existing_admin:
        print(f"      [WARN] Superadmin already exists (id={existing_admin['id']})")
        superadmin_id = existing_admin['id']
    else:
        superadmin_id = db.create_user(
            email=superadmin_email,
            password_hash=hash_password("securepass123"),
            role='superadmin',
            full_name='Test Superadmin',
        )
        print(f"      [OK] Superadmin created")

    # ── 3. Create test tenants ────────────────────────────────────────────
    print("\n[3/5] Creating test tenants...")

    tenants = [
        ('testpraktijk', 'Test Praktijk'),
        ('fysiozuid', 'Fysio Zuid'),
    ]

    tenant_ids = {}
    for slug, name in tenants:
        existing = db.get_tenant_by_slug(slug)
        if existing:
            print(f"      [WARN] Tenant '{slug}' already exists (id={existing['id']})")
            tenant_ids[slug] = existing['id']
        else:
            tenant_id = db.create_tenant(slug, name)
            tenant_ids[slug] = tenant_id
            print(f"      [OK] Tenant '{slug}' created (id={tenant_id})")

    # ── 4. Create owner users for each tenant ──────────────────────────────
    print("\n[4/5] Creating test owner users...")

    for slug, name in tenants:
        tenant_id = tenant_ids[slug]
        owner_email = f"owner-{slug}@test.com"

        existing_owner = db.get_user_by_email(owner_email)
        if existing_owner:
            print(f"      [WARN] Owner for '{slug}' already exists (id={existing_owner['id']})")
        else:
            owner_id = db.create_user(
                email=owner_email,
                password_hash=hash_password("ownerpass123"),
                role='owner',
                tenant_id=tenant_id,
                full_name=f'{name} Owner',
            )
            print(f"      [OK] Owner created: {owner_email}")

    # ── 5. Create invite tokens for testing ───────────────────────────────
    print("\n[5/5] Creating test invite tokens...")

    for slug, name in tenants:
        tenant_id = tenant_ids[slug]
        invite_email = f"invite-{slug}@test.com"

        token = db.create_invite_token(
            email=invite_email,
            tenant_id=tenant_id,
            role='owner',
            created_by=superadmin_id,
        )

        invite_url = f"http://localhost:5000/invite/{token}"
        print(f"      [OK] Invite token created for '{slug}'")
        print(f"        Email: {invite_email}")
        print(f"        URL:   {invite_url}")

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("[OK] LOCAL TEST DATABASE READY")
    print("=" * 70)

    print("\nLogin Credentials:")
    print(f"  Superadmin: admin@test.com / securepass123")
    print()

    for slug, name in tenants:
        print(f"  Tenant '{slug}' Owner:")
        print(f"    Email: owner-{slug}@test.com")
        print(f"    Password: ownerpass123")
        print()

    print("Next steps:")
    print("  1. Start app: python app.py")
    print("  2. Open http://localhost:5000")
    print("  3. Log in with superadmin or owner account")
    print("  4. Test multi-tenant features")
    print()
    print("[WARN] This is TEST DATA ONLY - no real patients")
    print("=" * 70)


if __name__ == '__main__':
    main()
