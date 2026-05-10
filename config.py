"""
Branding and platform configuration.

This file centralizes all branding, platform names, and URLs.
Change these values to rebrand the entire SaaS platform.

Values are loaded from environment variables with sensible defaults.
"""

import os


# ── Platform / Main Brand ──────────────────────────────────────────────────────
# The main platform brand name (e.g., "ReviewFlow", "Domeina", "TrustFlow", etc.)
# This appears in page titles, sidebar, and emails.
PLATFORM_NAME = os.environ.get('PLATFORM_NAME', 'ReviewFlow')

# The main platform domain/URL for marketing and public info
# Leave empty if not yet set
MARKETING_URL = os.environ.get('MARKETING_URL', '')


# ── Tool / Product Name ───────────────────────────────────────────────────────
# The name of the current tool/module within the platform
# Foreseeable future: platform may have ReviewFlow, BookingFlow, AI Secretary, etc.
# For now, ReviewFlow is both platform and tool.
TOOL_NAME = os.environ.get('TOOL_NAME', 'ReviewFlow')


# ── Application URLs ─────────────────────────────────────────────────────────
# Base URL for the application (used for invite links, login redirects, etc.)
# Must include https:// and no trailing slash
APP_BASE_URL = os.environ.get('APP_BASE_URL', 'https://reviewflow.osteozuid.be')

# Application name (used in titles, breadcrumbs)
APP_NAME = os.environ.get('APP_NAME', PLATFORM_NAME)


# ── SMTP Configuration ────────────────────────────────────────────────────────
# System SMTP for invite emails (from system, not tenant-specific)
SYSTEM_SMTP_HOST = os.environ.get('SYSTEM_SMTP_HOST', '')
SYSTEM_SMTP_PORT = int(os.environ.get('SYSTEM_SMTP_PORT', '587'))
SYSTEM_SMTP_USER = os.environ.get('SYSTEM_SMTP_USER', '')
SYSTEM_SMTP_PASSWORD = os.environ.get('SYSTEM_SMTP_PASSWORD', '')
SYSTEM_FROM_EMAIL = os.environ.get('SYSTEM_FROM_EMAIL', 'noreply@reviewflow.com')
SYSTEM_FROM_NAME = os.environ.get('SYSTEM_FROM_NAME', PLATFORM_NAME)


# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://localhost/reviewflow')


# ── Secrets ───────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-insecure-key-change-in-production')


# ── Display / Template Helpers ────────────────────────────────────────────────
def get_page_title(page_name=''):
    """Build page title from platform name."""
    if page_name:
        return f'{page_name} — {PLATFORM_NAME}'
    return PLATFORM_NAME


def get_invite_email_subject(tenant_name):
    """Build invite email subject."""
    return f'Uitnodiging: {PLATFORM_NAME} bij {tenant_name}'


def get_invite_email_from_name():
    """Get sender name for invite emails."""
    return SYSTEM_FROM_NAME


# ── Future: Multi-platform Features ───────────────────────────────────────────
# When the platform grows to include ReviewFlow, BookingFlow, etc.,
# you may want to enable/disable features per tenant or globally.
# This is a placeholder structure:
FEATURES = {
    'review_flow': True,           # Patient review collection
    'booking_flow': False,          # Appointment booking (future)
    'ai_secretary': False,          # AI patient assistant (future)
    'profit_calculator': False,     # Business analytics (future)
}
