from functools import wraps

from flask import abort, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import db


def hash_password(password):
    return generate_password_hash(password)


def verify_password(password, pw_hash):
    return check_password_hash(pw_hash, password)


def get_current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    return db.get_user_by_id(user_id)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = g.get('user')
            if not user or user['role'] not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = g.get('user')
        if not user or user['role'] != 'superadmin':
            abort(403)
        return f(*args, **kwargs)
    return decorated


def owner_or_above(f):
    return role_required('superadmin', 'owner')(f)


def any_authenticated_role(f):
    return role_required('superadmin', 'owner', 'staff')(f)
