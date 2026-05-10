# Branding & Platform Configuration

## Overview

ReviewFlow SaaS is designed to be easily rebranded to a different platform name in the future (e.g., "Domeina", "TrustFlow", "ClientFlow", etc.).

All branding names, platform identities, and URLs are **centralized in `config.py`** and controlled via **environment variables**. No hardcoded brand names in application code or templates.

---

## Current Branding (Sprint 1)

```
PLATFORM_NAME   = "ReviewFlow"      (env: PLATFORM_NAME)
TOOL_NAME       = "ReviewFlow"      (env: TOOL_NAME)
APP_NAME        = "ReviewFlow"      (env: APP_NAME)
APP_BASE_URL    = "https://reviewflow.osteozuid.be"
MARKETING_URL   = ""                (not yet set)
```

---

## How It Works

### 1. **Central Config** (`config.py`)
All branding variables are loaded from environment variables with sensible defaults.

```python
PLATFORM_NAME = os.environ.get('PLATFORM_NAME', 'ReviewFlow')
TOOL_NAME = os.environ.get('TOOL_NAME', 'ReviewFlow')
APP_NAME = os.environ.get('APP_NAME', 'ReviewFlow')
APP_BASE_URL = os.environ.get('APP_BASE_URL', 'https://reviewflow.osteozuid.be')
MARKETING_URL = os.environ.get('MARKETING_URL', '')
```

### 2. **Flask App** (`app.py`)
Config is imported and values are injected into Jinja template context:

```python
import config

app.jinja_env.globals['PLATFORM_NAME'] = config.PLATFORM_NAME
app.jinja_env.globals['TOOL_NAME'] = config.TOOL_NAME
app.jinja_env.globals['APP_NAME'] = config.APP_NAME
```

### 3. **Templates** (e.g., `templates/base.html`)
Branding is used dynamically:

```html
<title>{{ APP_NAME }} — Uitnodigingen</title>
<h1>{{ PLATFORM_NAME }}</h1>
```

### 4. **Python Code** (`auth.py`, `mailer.py`, etc.)
When branding names are needed (e.g., in email subjects):

```python
from config import PLATFORM_NAME, TOOL_NAME
subject = f"Uitnodiging: {PLATFORM_NAME} bij {tenant_name}"
```

---

## How to Rebrand (Step-by-Step)

**Example: Rebrand from "ReviewFlow" → "Domeina"**

### Step 1: Update `.env`
```bash
# Before:
PLATFORM_NAME=ReviewFlow
TOOL_NAME=ReviewFlow
APP_NAME=ReviewFlow

# After:
PLATFORM_NAME=Domeina
TOOL_NAME=ReviewFlow          (still the module name within Domeina)
APP_NAME=Domeina
MARKETING_URL=https://domeina.eu
```

### Step 2: Verify in `config.py`
Config automatically loads from env vars. No code changes needed.

### Step 3: Update `.env.example` for documentation
```bash
# Make it clear for next developer
PLATFORM_NAME=Domeina
TOOL_NAME=ReviewFlow
```

### Step 4: Test
- Restart Flask app: `python app.py`
- Check sidebar, page titles, emails
- Verify invite emails show "Uitnodiging: Domeina"

### Step 5: Deploy
Set env vars on production server:
```bash
export PLATFORM_NAME=Domeina
export APP_NAME=Domeina
systemctl restart reviewflow-saas
```

---

## Variables by Use Case

| Variable | Usage | Appears in |
|---|---|---|
| `PLATFORM_NAME` | Main brand/company name | Sidebar, page titles, emails, invites |
| `TOOL_NAME` | Current module/tool name | Future: when platform has multiple tools |
| `APP_NAME` | Display name in this app | Browser title, flash messages |
| `APP_BASE_URL` | Technical: invite URLs, redirects | Email links, login redirects |
| `MARKETING_URL` | Public marketing site | Footer links (when implemented) |

---

## Where Branding Appears

### ✅ Centrally Configured (Already Dynamic)
- [ ] Sidebar app title
- [ ] Page titles (`<title>` tag)
- [ ] Login page header
- [ ] Invite email subject & body
- [ ] Flash messages
- [ ] Footer text

### ⚠️ Hardcoded (Need Review)
- [ ] SVG logo filename (`ReviewFlow_SVG.svg` in `static/`)
- [ ] Logo alt-text references
- [ ] Domain name in documentation
- [ ] Docker container names, file paths

### 📝 Not Yet Implemented (Future)
- [ ] Brand color scheme (currently hardcoded as orange #f28c00)
- [ ] Logo upload per platform
- [ ] Multiple brands on same infrastructure

---

## Future: Multi-Tool Platform

When the platform grows to include multiple tools (ReviewFlow, BookingFlow, AI Secretary, etc.):

```python
FEATURES = {
    'review_flow': True,       # Patient review collection
    'booking_flow': False,     # Appointment booking (future)
    'ai_secretary': False,     # AI assistant (future)
}
```

Each tool can be enabled/disabled per tenant or globally in `config.py`.

---

## Summary

**To rebrand:** Change env vars in `.env` (or on production server). Nothing else needed.

**For developers:** Always use `config.VARIABLE` instead of hardcoding strings. When adding new branding-related features, add to `config.py` first, then reference in code/templates.

**Deployment checklist:**
- [ ] Update `.env` with new `PLATFORM_NAME`, `TOOL_NAME`, `APP_NAME`
- [ ] Update `APP_BASE_URL` if domain changed
- [ ] Update `MARKETING_URL` if marketing site exists
- [ ] Restart app: `systemctl restart reviewflow-saas`
- [ ] Verify invite emails show correct platform name
- [ ] Test login, dashboard, sidebar
