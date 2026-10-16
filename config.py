"""Application configuration for the Belay Demeke Family website."""

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

_ENV_PLACEHOLDER = "change-this-to-a-long-random-string"


def _load_dotenv(path: Path) -> None:
    """Populate os.environ from a .env file without requiring python-dotenv."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")

INSTANCE_DIR = BASE_DIR / "instance"
_SECRET_KEY_FILE = INSTANCE_DIR / "secret_key"


def _resolve_secret_key() -> str:
    """Return a strong session signing key.

    Prefers an explicitly configured SECRET_KEY. When absent (or still set to the
    shipped placeholder) a random key is generated once and persisted, so sessions
    survive restarts without a predictable secret being committed to the repo.
    """
    configured = os.environ.get("SECRET_KEY") or os.environ.get("SESSION_SECRET")
    if configured and configured != _ENV_PLACEHOLDER:
        return configured

    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    existing = _SECRET_KEY_FILE.read_text(encoding="utf-8").strip() if _SECRET_KEY_FILE.is_file() else ""
    if existing:
        return existing

    generated = secrets.token_urlsafe(48)
    _SECRET_KEY_FILE.write_text(generated, encoding="utf-8")
    return generated


class Config:
    SECRET_KEY = _resolve_secret_key()

    MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100 MB per upload

    UPLOAD_DIR = BASE_DIR / "uploads"
    DATA_DIR = BASE_DIR

    USERS_FILE = DATA_DIR / "users.json"
    POSTS_FILE = DATA_DIR / "posts.json"
    ANNOUNCEMENTS_FILE = DATA_DIR / "announcements.json"
    EVENTS_FILE = DATA_DIR / "events.json"
    MESSAGES_FILE = DATA_DIR / "messages.json"
    JOIN_REQUESTS_FILE = DATA_DIR / "join_requests.json"
    NOTIFICATIONS_FILE = DATA_DIR / "notifications.json"
    ACTIVITY_FILE = DATA_DIR / "activity_log.json"
    AVATAR_MAX_BYTES = 5 * 1024 * 1024  # profile photos stay small

    # Activity log keeps the most recent entries per family.
    ACTIVITY_KEEP = 200

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("FLASK_ENV") == "production"
    PERMANENT_SESSION_LIFETIME_DAYS = 7

    # Login throttling: block an address after this many failures for a while.
    LOGIN_MAX_ATTEMPTS = 5
    LOGIN_LOCKOUT_SECONDS = 300

    PASSWORD_MIN_LENGTH = 8

    ROLE_ADMIN = "admin"
    ROLE_MEMBER = "member"

    # Display-only figures shown on the homepage; edit to match your family.
    FAMILY_NAME = "Belay Demeke"
    FAMILY_SINCE = 2016
    FAMILY_GENERATIONS = 1


ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_VIDEO_EXT = {"mp4", "mov", "webm", "m4v"}
ALLOWED_DOC_EXT = {"pdf", "docx", "xlsx", "txt"}
ALLOWED_EXT = ALLOWED_IMAGE_EXT | ALLOWED_VIDEO_EXT | ALLOWED_DOC_EXT


def media_kind(ext: str) -> str:
    """Map a file extension to the renderer category used by the templates."""
    ext = (ext or "").lower()
    if ext in ALLOWED_IMAGE_EXT:
        return "image"
    if ext in ALLOWED_VIDEO_EXT:
        return "video"
    return "document"


Config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
