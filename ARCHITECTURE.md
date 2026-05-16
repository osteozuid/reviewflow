# ReviewFlow SaaS — Architecture & Security

## Multi-Tenant Architecture

### Tenant Isolation Mechanism

**Current Implementation: Application-Level Filtering**

Tenant isolation is achieved through **application-level row filtering**, NOT PostgreSQL Row Level Security (RLS) policies.

```
┌─────────────────────────────────────────────────────────┐
│ User Login → g.tenant_id = user.tenant_id               │
│                                                          │
│ Route Handler (before_request):                         │
│   g.tenant_id = g.user['tenant_id']                     │
│                                                          │
│ All Database Queries:                                   │
│   db.get_sent_logs(g.tenant_id)    # Pass tenant_id    │
│   db.get_contacts(g.tenant_id)     # Always filtered    │
│                                                          │
│ SQL Level:                                              │
│   WHERE tenant_id = %s             # Filter by tenant  │
└─────────────────────────────────────────────────────────┘
```

### How It Works

1. **Authentication** (`app.py @before_request`)
   ```python
   g.user = db.get_user_by_id(session.get('user_id'))
   g.tenant_id = g.user['tenant_id']    # ← Tenant set here
   g.tenant = db.get_tenant(g.tenant_id)
   ```

2. **Route Protection**
   ```python
   @app.route('/dashboard')
   def dashboard():
       # g.tenant_id is automatically available
       stats = db.get_dashboard_stats(g.tenant_id)  # ← Filtered
       contacts = db.get_all_contacts(g.tenant_id)  # ← Filtered
   ```

3. **Database Queries** (`db.py`)
   ```python
   def get_sent_logs(tenant_id, limit=100):
       with get_connection() as conn:
           cur = _q(conn,
               "SELECT * FROM review_log WHERE tenant_id = %s",
               (tenant_id,))  # ← Filter enforced
           return cur.fetchall()
   ```

### Database Schema

All data tables include `tenant_id` as a foreign key:

```sql
CREATE TABLE review_log (
    id        SERIAL PRIMARY KEY,
    tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE,  ← FK
    email     VARCHAR(255),
    ...
);

CREATE TABLE contacts (
    id        SERIAL PRIMARY KEY,
    tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE,  ← FK
    email     VARCHAR(255),
    ...
);
```

**No PostgreSQL Row Level Security (RLS) policies are used.**

---

## Security Considerations

### ✅ Strengths

- **Simple and transparent**: Filtering logic is visible in application code
- **Debuggable**: SQL queries and tenant_id can be logged for auditing
- **Portable**: Works with any SQL database (PostgreSQL, MySQL, SQLite)
- **Testable**: Tests explicitly verify cross-tenant isolation (31 tests cover this)

### ⚠️ Risks

Each database query MUST include `WHERE tenant_id = %s` filtering:
- If a query forgets the WHERE clause → **data leak across tenants**
- If g.tenant_id is None → request should fail before querying
- If a developer hardcodes queries without tenant_id → **cross-tenant read**

### 🛡️ Mitigations

1. **Code Review Discipline**
   - All queries reviewed to ensure `WHERE tenant_id = %s`
   - No hardcoded queries allowed

2. **Automated Testing**
   - `test_review_log_geïsoleerd` — verifies tenant A cannot see tenant B's review logs
   - `test_contacten_geïsoleerd` — verifies contact data is isolated
   - `test_templates_geïsoleerd` — verifies templates are isolated
   - `test_instellingen_geïsoleerd` — verifies settings are isolated
   - `test_import_log_geïsoleerd` — verifies import logs are isolated
   - All 5 tests MUST PASS before deployment

3. **Superadmin Access**
   - Superadmin has `tenant_id = NULL` (no tenant affiliation)
   - Superadmin can view tenant management, not tenant data
   - Superadmin cannot accidentally see patient review logs

4. **Request-Level Checks** (`@before_request` in app.py)
   ```python
   if request.endpoint not in open_endpoints and not g.user:
       return redirect(url_for('login'))
   ```

---

## Future: PostgreSQL Row Level Security (RLS)

If deeper database-level isolation is needed:

1. Enable RLS on all tables
2. Create policies for each tenant
3. Set `current_user_id` in session
4. Database enforces row access without application logic

Example (not yet implemented):
```sql
ALTER TABLE review_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_policy ON review_log
    USING (tenant_id = current_setting('app.current_tenant_id')::integer);
```

**Decision**: RLS not yet implemented due to complexity; application-level filtering sufficient for current scale (1-50 tenants).

---

## Data Flow for Multi-Tenant Request

```
1. User makes request to /dashboard
   ↓
2. @before_request hook runs
   → Fetch user from session
   → Set g.tenant_id from user.tenant_id
   ↓
3. Route handler processes request
   → Calls db.get_dashboard_stats(g.tenant_id)
   ↓
4. Database query executes
   → SELECT * FROM dashboard_stats WHERE tenant_id = %s
   → Parameter: g.tenant_id
   ↓
5. Results returned (only for that tenant)
   ↓
6. Template renders with tenant-scoped data
   ↓
7. User sees only their tenant's information
```

---

## Testing Multi-Tenant Isolation

Run the test suite to verify isolation:

```bash
pytest tests/test_sprint1.py::TestCrossTenantIsolatie -v
```

All 5 cross-tenant tests MUST pass:
- ✅ review_log geïsoleerd
- ✅ contacten geïsoleerd
- ✅ templates geïsoleerd
- ✅ instellingen geïsoleerd
- ✅ import_log geïsoleerd

If any test fails: **data isolation is broken**.

---

## Tenant Management (Superadmin Only)

Superadmins can:
- ✅ Create new tenants
- ✅ Invite users to tenants
- ✅ Reset tenant settings
- ❌ Cannot access tenant data (review logs, contacts, templates)

Routes:
- `GET /admin/tenants` — list all tenants
- `POST /admin/tenants/new` — create tenant
- `POST /admin/tenants/<id>/invite` — send invite to email

---

## Invite Token Flow (No Hardcoded Emails)

Users onboard via invite tokens:

```
1. Superadmin creates tenant invitation
   → Generate random token (secrets.token_urlsafe(32))
   → Store in invite_tokens table with 7-day expiry
   ↓
2. Invite link sent to email (if SYSTEM_SMTP configured)
   → URL: {APP_BASE_URL}/invite/{token}
   ↓
3. User clicks link
   → GET /invite/{token}
   → Shows form: full name + password
   ↓
4. User submits form
   → POST /invite/{token}
   → Create user account
   → Attach to tenant
   → Delete/mark token as used
   ↓
5. User logged in automatically
   → g.tenant_id = tenant_id from invitation
   → Can only see their tenant's data
```

---

## Environment Variables (No Hardcoded Values)

All configuration from environment:

```bash
# Branding
PLATFORM_NAME=ReviewFlow
TOOL_NAME=ReviewFlow
APP_NAME=ReviewFlow

# URLs
APP_BASE_URL=http://localhost:5000
MARKETING_URL=https://example.com  (optional)

# Database
DATABASE_URL=postgresql://user:pass@host/database

# SMTP (system/invite emails only)
SYSTEM_SMTP_HOST=smtp.example.com
SYSTEM_SMTP_USER=user@example.com
SYSTEM_SMTP_PASSWORD=secret

# Authentication
SECRET_KEY=random-secret-key
SUPERADMIN_EMAIL=admin@example.com
SUPERADMIN_PASSWORD=secure-password
```

---

## Database Cleanup (Per Tenant)

When a tenant is deleted:

```sql
DELETE FROM tenants WHERE id = X;
-- CASCADE deletes:
-- - All users for this tenant
-- - All invite tokens
-- - All review logs
-- - All contacts
-- - All templates
-- - All settings
-- - All import logs
```

---

## Deployment Checklist

Before going to production:

- [ ] All 31 tests pass locally
- [ ] Cross-tenant isolation tests (5) pass
- [ ] PostgreSQL available on production
- [ ] DATABASE_URL configured
- [ ] SECRET_KEY is random (not "dev-key")
- [ ] SYSTEM_SMTP configured for invite emails
- [ ] SUPERADMIN account created via migrate script
- [ ] SSL/HTTPS enabled
- [ ] Logging/monitoring in place
- [ ] Backup strategy for PostgreSQL
