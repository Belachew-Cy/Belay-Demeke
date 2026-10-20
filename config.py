"""Application configuration for the Belay Demeke Family website."""

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

_ENV_PLACEHOLDER = "change-this-to-a-long-random-string"


def _load_dotenv(path: Path) -> None:
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

# Render and friends set RENDER=true; FLASK_ENV=production is the manual switch.
# Both mean "this copy is on the public internet", so nothing may run on defaults.
LIVE_HOST = bool(os.environ.get("RENDER")) or os.environ.get("FLASK_ENV") == "production"


def _resolve_secret_key() -> str:
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


def _resolve_database_uri() -> str:
    """Return the SQLAlchemy URL, refusing to guess on a public host.

    Hosts such as Neon hand out a DSN spelled ``postgres://``, which SQLAlchemy 2.x
    no longer recognises; it is rewritten to ``postgresql://`` before use. Without
    DATABASE_URL the app runs on a local SQLite file — but only off a live host,
    because an ephemeral SQLite database on a hosting service looks like data loss
    the first time the service restarts.
    """
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url:
        return url
    if LIVE_HOST:
        raise RuntimeError(
            "DATABASE_URL is not set. Add your Postgres DSN to the environment "
            "(Render: Environment tab -> Add Environment Variable) before starting "
            "the service; the app will not run on a throwaway local database."
        )
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + (INSTANCE_DIR / "belay.db").as_posix()


class Config:
    SECRET_KEY = _resolve_secret_key()

    MAX_CONTENT_LENGTH = 100 * 1024 * 1024

    # Render's filesystem is wiped on every deploy, so a hosted copy points this at
    # a mounted persistent disk; locally it stays the uploads folder in the app.
    UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR") or BASE_DIR / "uploads")
    DATA_DIR = BASE_DIR

    SQLALCHEMY_DATABASE_URI = _resolve_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    AVATAR_MAX_BYTES = 5 * 1024 * 1024

    ACTIVITY_KEEP = 200

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = LIVE_HOST
    PERMANENT_SESSION_LIFETIME_DAYS = 7

    LOGIN_MAX_ATTEMPTS = 5
    LOGIN_LOCKOUT_SECONDS = 300

    PASSWORD_MIN_LENGTH = 8

    ROLE_ADMIN = "admin"
    ROLE_MEMBER = "member"

    FAMILY_NAME = "Belay Demeke"
    FAMILY_SINCE = 2016
    FAMILY_GENERATIONS = 1


ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_VIDEO_EXT = {"mp4", "mov", "webm", "m4v"}
ALLOWED_DOC_EXT = {"pdf", "docx", "xlsx", "txt"}
ALLOWED_EXT = ALLOWED_IMAGE_EXT | ALLOWED_VIDEO_EXT | ALLOWED_DOC_EXT


def media_kind(ext: str) -> str:
    ext = (ext or "").lower()
    if ext in ALLOWED_IMAGE_EXT:
        return "image"
    if ext in ALLOWED_VIDEO_EXT:
        return "video"
    return "document"


Config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
