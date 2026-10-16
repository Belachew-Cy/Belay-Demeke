"""Authentication helpers, CSRF protection and request throttling."""

import re
import secrets
import time
from collections import defaultdict, deque
from functools import wraps
from hmac import compare_digest

from flask import abort, flash, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config

# ---------------------------------------------------------------- passwords


def hash_password(plain: str) -> str:
    return generate_password_hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    if not stored_hash or plain is None:
        return False
    try:
        return check_password_hash(stored_hash, plain)
    except ValueError:
        # Unrecognised/legacy hash format — reject rather than crash.
        return False


def password_problem(password: str, confirm: str | None = None) -> str | None:
    """Return a human-readable reason the password is unacceptable, else None."""
    if not password or len(password) < Config.PASSWORD_MIN_LENGTH:
        return f"Password must be at least {Config.PASSWORD_MIN_LENGTH} characters long."
    if not re.search(r"[A-Za-z]", password):
        return "Password must contain at least one letter."
    if not re.search(r"\d", password):
        return "Password must contain at least one number."
    if password.lower() in {"password", "password1", "12345678", "qwerty123"}:
        return "That password is too common. Please choose something less predictable."
    if confirm is not None and password != confirm:
        return "Passwords do not match."
    return None


# ---------------------------------------------------------------- validation

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,24}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def username_problem(username: str) -> str | None:
    if not USERNAME_RE.match((username or "").strip().lower()):
        return ("Username must be 3-24 characters and may only contain lowercase "
                "letters, numbers, dots, hyphens or underscores.")
    return None


def email_problem(email: str) -> str | None:
    if not EMAIL_RE.match((email or "").strip()):
        return "Please enter a valid email address."
    return None


# ---------------------------------------------------------------- CSRF

_CSRF_SESSION_KEY = "_csrf_token"


def csrf_token() -> str:
    token = session.get(_CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[_CSRF_SESSION_KEY] = token
    return token


def init_csrf(app) -> None:
    """Require a matching token on every state-changing request."""

    @app.context_processor
    def _inject():
        return {"csrf_token": csrf_token}

    @app.before_request
    def _verify():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        if request.endpoint in app.view_functions and getattr(
            app.view_functions[request.endpoint], "csrf_exempt", False
        ):
            return None
        submitted = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        expected = session.get(_CSRF_SESSION_KEY, "")
        if not submitted or not expected or not compare_digest(submitted, expected):
            abort(400, description="Your session expired or the form was tampered with. Please try again.")
        return None


def csrf_exempt(view):
    view.csrf_exempt = True
    return view


# ---------------------------------------------------------------- throttling


class LoginThrottle:
    """In-memory failed-attempt tracking, keyed by client address."""

    def __init__(self, max_attempts: int, lockout_seconds: int):
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._failures: dict[str, deque] = defaultdict(deque)
        self._lock_time: dict[str, float] = {}

    def _prune(self, key: str, now: float) -> None:
        window = self._failures[key]
        while window and now - window[0] > self.lockout_seconds:
            window.popleft()
        if not window:
            del self._failures[key]

    def seconds_remaining(self, key: str) -> int:
        now = time.monotonic()
        until = self._lock_time.get(key, 0)
        if until > now:
            return int(until - now) + 1
        self._prune(key, now)
        if len(self._failures.get(key, ())) >= self.max_attempts:
            self._lock_time[key] = now + self.lockout_seconds
            return self.lockout_seconds
        return 0

    def record_failure(self, key: str) -> None:
        self._failures[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
        self._lock_time.pop(key, None)


login_throttle = LoginThrottle(Config.LOGIN_MAX_ATTEMPTS, Config.LOGIN_LOCKOUT_SECONDS)


def client_key() -> str:
    return f"{request.remote_addr or 'unknown'}|{request.form.get('username', '').strip().lower()}"


# ---------------------------------------------------------------- decorators


def current_member():
    """The signed-in user record, or None. Kept fresh from storage each request."""
    identity = session.get("user")
    if not identity:
        return None
    from storage import get_user

    record = get_user(identity.get("username", ""))
    if not record or not record.get("is_active", True):
        session.pop("user", None)
        return None
    return record


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            flash("Please sign in to continue.", "info")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        member = current_member()
        if not member:
            return redirect(url_for("login", next=request.path))
        if member.get("role") != Config.ROLE_ADMIN:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def init_session(app) -> None:
    """Configure cookie hardening and the rolling session lifetime."""
    from datetime import timedelta

    app.permanent_session_lifetime = timedelta(days=Config.PERMANENT_SESSION_LIFETIME_DAYS)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=Config.SESSION_COOKIE_HTTPONLY,
        SESSION_COOKIE_SAMESITE=Config.SESSION_COOKIE_SAMESITE,
        SESSION_COOKIE_SECURE=Config.SESSION_COOKIE_SECURE,
    )

    @app.before_request
    def _mark_permanent():
        session.permanent = True


def safe_text(value, limit: int = 500) -> str:
    """Trim and collapse whitespace on user-supplied free text."""
    return re.sub(r"\s+", " ", (value or "")).strip()[:limit]
