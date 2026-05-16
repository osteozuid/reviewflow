#!/usr/bin/env python
"""
Staging/production bootstrap — initializes schema and superadmin from .env.

Does NOT create fake tenants, fake users, or fake data.
All credentials come from environment variables — nothing is hardcoded.

Required .env variables:
    DATABASE_URL        PostgreSQL connection string
    SUPERADMIN_EMAIL    E-mail address for the superadmin account
    SUPERADMIN_PASSWORD Plain-text password (hashed before storage)

Usage:
    python scripts/bootstrap_staging.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import db
from auth import hash_password


def main():
    print("=" * 70)
    print("ReviewFlow SaaS — Staging Bootstrap")
    print("=" * 70)

    # ── Validate required env vars ────────────────────────────────────────
    superadmin_email = os.environ.get("SUPERADMIN_EMAIL", "").strip()
    superadmin_password = os.environ.get("SUPERADMIN_PASSWORD", "").strip()

    if not superadmin_email:
        print("\n[ERROR] SUPERADMIN_EMAIL is not set in .env")
        sys.exit(1)
    if not superadmin_password:
        print("\n[ERROR] SUPERADMIN_PASSWORD is not set in .env")
        sys.exit(1)
    if len(superadmin_password) < 12:
        print("\n[ERROR] SUPERADMIN_PASSWORD must be at least 12 characters")
        sys.exit(1)

    # ── 1. Initialize schema ──────────────────────────────────────────────
    print("\n[1/2] Initializing PostgreSQL schema...")
    db.init_db()
    print("      [OK] Schema ready")

    # ── 2. Create superadmin ──────────────────────────────────────────────
    print(f"\n[2/2] Creating superadmin: {superadmin_email}")
    existing = db.get_user_by_email(superadmin_email)
    if existing:
        print(f"      [WARN] Superadmin already exists (id={existing['id']}) — skipped")
    else:
        db.create_user(
            email=superadmin_email,
            password_hash=hash_password(superadmin_password),
            role='superadmin',
            full_name='Superadmin',
        )
        print("      [OK] Superadmin created")

    print("\n" + "=" * 70)
    print("[OK] STAGING DATABASE READY")
    print("=" * 70)
    print(f"\n  Login at: {os.environ.get('APP_BASE_URL', 'https://reviewflow.osteozuid.be')}")
    print(f"  Email:    {superadmin_email}")
    print(f"  Password: (as set in .env)")
    print()
    print("Next: create tenants via the superadmin UI.")
    print("=" * 70)


if __name__ == '__main__':
    main()
