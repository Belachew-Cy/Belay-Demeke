"""Database-backed persistence for the family website (SQLAlchemy)."""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import DateTime

from config import Config
from models import (
    Activity, Announcement, Event, JoinRequest, Message,
    Notification, Post, User, db,
)
from security import hash_password

NOTIFY_TYPES = ("announcement", "event", "memory", "message", "account")
EDITABLE_NOTIFY_TYPES = ("announcement", "event", "memory", "message")
NOTIFY_DEFAULTS = {kind: True for kind in NOTIFY_TYPES}
NOTIFY_KEEP_PER_USER = 40


def utcnow():
    return datetime.now(timezone.utc)


def new_id():
    return uuid.uuid4().hex


def display_date(iso_timestamp):
    if not iso_timestamp:
        return ""
    try:
        moment = datetime.fromisoformat(iso_timestamp)
    except (ValueError, TypeError):
        return str(iso_timestamp)
    # Every stored timestamp is UTC; a naive string is read as UTC, not local time,
    # so a memory posted after 21:00Z still shows the correct day here.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%b %d, %Y")


def _iso(dt):
    return dt.isoformat() if dt else ""


# ---------------------------------------------------------------- users

def _user_dict(u):
    if u is None:
        return None
    return {
        "username": u.username, "name": u.name, "role": u.role,
        "email": u.email or "", "phone": u.phone or "",
        "relationship": u.relationship or "", "location": u.location or "",
        "bio": u.bio or "", "avatar": u.avatar or "",
        "is_active": bool(u.is_active), "password": u.password,
        "notify": u.notify or dict(NOTIFY_DEFAULTS),
        "joined_at": _iso(u.joined_at),
    }


def get_user(username):
    if not username:
        return None
    u = User.query.filter(db.func.lower(User.username) == username.strip().lower()).first()
    return _user_dict(u)


def find_user_by_email(email):
    if not email:
        return None
    u = User.query.filter(db.func.lower(User.email) == email.strip().lower()).first()
    return (u.username, _user_dict(u)) if u else None


def list_members():
    users = User.query.filter_by(is_active=True).all()
    users.sort(key=lambda u: (u.role != Config.ROLE_ADMIN, (u.name or "").lower()))
    return [_user_dict(u) for u in users]


def list_inactive_members():
    users = User.query.filter_by(is_active=False).all()
    users.sort(key=lambda u: (u.name or "").lower())
    return [_user_dict(u) for u in users]


def create_user(username, password, **profile):
    u = User(
        username=username.strip().lower(),
        name=profile.get("name", ""),
        password=hash_password(password),
        role=profile.get("role", Config.ROLE_MEMBER),
        email=profile.get("email", ""),
        phone=profile.get("phone", ""),
        relationship=profile.get("relationship", ""),
        location=profile.get("location", ""),
        bio=profile.get("bio", ""),
        avatar=profile.get("avatar", ""),
        is_active=True,
        joined_at=utcnow(),
        notify=dict(NOTIFY_DEFAULTS),
    )
    db.session.add(u)
    db.session.commit()
    return _user_dict(u)


def update_user(username, **changes):
    u = User.query.filter(db.func.lower(User.username) == username.strip().lower()).first()
    if not u:
        return None
    for k, v in changes.items():
        if v is None or k == "username":
            continue
        if hasattr(u, k):
            setattr(u, k, v)
    db.session.commit()
    return _user_dict(u)


def set_password(username, password):
    """Admins and members both land here: only the scrypt hash is ever stored."""
    return update_user(username, password=hash_password(password))


def avatar_files():
    """Names of photos still served from the uploads folder, for the old URLs."""
    users = User.query.filter(User.avatar.isnot(None)).all()
    names = set()
    for u in users:
        a = u.avatar or ""
        if a.startswith("/uploads/"):
            names.add(a.rsplit("/", 1)[1])
    return names


# ---------------------------------------------------------------- profile photos

def set_avatar(username, data, content_type):
    """Store a member's photo as bytes and point the display URL at the reader.

    The ?v= token is the upload time: a browser that cached an older face asks for
    a new URL rather than showing the stale one for the year the response is
    cached for. Templates keep reading the ``avatar`` column, so nothing else has
    to know the bytes exist.
    """
    u = User.query.filter(db.func.lower(User.username) == username.strip().lower()).first()
    if not u:
        return None
    moment = utcnow()
    u.avatar_image = data
    u.avatar_type = content_type
    u.avatar_updated_at = moment
    u.avatar = f"/members/{u.username}/photo?v={int(moment.timestamp())}"
    db.session.commit()
    return _user_dict(u)


def get_avatar(username):
    """Return (bytes, content type, modified time) for one stored photo."""
    u = User.query.filter(db.func.lower(User.username) == username.strip().lower()).first()
    if not u or u.avatar_image is None:
        return None
    return bytes(u.avatar_image), (u.avatar_type or "image/jpeg"), u.avatar_updated_at


# ---------------------------------------------------------------- posts

def _post_dict(p):
    if p is None:
        return None
    return {
        "id": p.id, "title": p.title, "desc": p.description or "",
        "visibility": p.visibility or "family", "filename": p.filename or "",
        "original": p.original or "", "url": p.url or "", "kind": p.kind or "",
        "type": p.type or "", "uploaded_by": p.uploaded_by or "",
        "uploaded_by_username": p.uploaded_by_username or "",
        "date": p.date or "", "created_at": _iso(p.created_at),
    }


def add_post(**fields):
    p = Post(
        id=new_id(),
        title=fields.get("title", ""),
        description=fields.get("desc", ""),
        visibility=fields.get("visibility", "family"),
        filename=fields.get("filename", ""),
        original=fields.get("original", ""),
        url=fields.get("url", ""),
        kind=fields.get("kind", ""),
        type=fields.get("type", ""),
        uploaded_by=fields.get("uploaded_by", ""),
        uploaded_by_username=fields.get("uploaded_by_username") or None,
        date=fields.get("date", ""),
        created_at=utcnow(),
    )
    db.session.add(p)
    db.session.commit()
    return _post_dict(p)


def get_post(post_id):
    return _post_dict(Post.query.get(post_id))


def list_posts(visibility=None, limit=None):
    q = Post.query.order_by(Post.created_at.desc())
    if visibility:
        q = q.filter_by(visibility=visibility)
    if limit:
        q = q.limit(limit)
    return [_post_dict(p) for p in q.all()]


def update_post(post_id, **changes):
    p = Post.query.get(post_id)
    if not p:
        return None
    for k, v in changes.items():
        if hasattr(p, k):
            setattr(p, k, v)
    db.session.commit()
    return _post_dict(p)


def remove_post(post_id):
    p = Post.query.get(post_id)
    if p:
        d = _post_dict(p)
        db.session.delete(p)
        db.session.commit()
        return d
    return None


# ---------------------------------------------------------------- announcements

def add_announcement(**fields):
    a = Announcement(
        id=new_id(),
        title=fields.get("title", ""),
        body=fields.get("body", ""),
        author=fields.get("author", ""),
        pinned=bool(fields.get("pinned", False)),
        created_at=utcnow(),
    )
    db.session.add(a)
    db.session.commit()
    return {"id": a.id, "title": a.title, "body": a.body, "author": a.author,
            "pinned": a.pinned, "created_at": _iso(a.created_at)}


def list_announcements(limit=None):
    q = Announcement.query.order_by(Announcement.created_at.desc())
    if limit:
        q = q.limit(limit)
    return [{"id": a.id, "title": a.title, "body": a.body, "author": a.author,
             "pinned": a.pinned, "created_at": _iso(a.created_at)} for a in q.all()]


def remove_announcement(record_id):
    a = Announcement.query.get(record_id)
    if a:
        d = {"id": a.id, "title": a.title}
        db.session.delete(a)
        db.session.commit()
        return d
    return None


# ---------------------------------------------------------------- events

def add_event(**fields):
    e = Event(
        id=new_id(),
        title=fields.get("title", ""),
        date=fields.get("date", ""),
        time=fields.get("time", ""),
        location=fields.get("location", ""),
        description=fields.get("description", ""),
        created_at=utcnow(),
    )
    db.session.add(e)
    db.session.commit()
    return {"id": e.id, "title": e.title, "date": e.date, "time": e.time,
            "location": e.location, "description": e.description}


def list_events(upcoming_only=False):
    q = Event.query
    if upcoming_only:
        today = utcnow().date().isoformat()
        q = q.filter(Event.date >= today)
    q = q.order_by(Event.date.asc())
    return [{"id": e.id, "title": e.title, "date": e.date, "time": e.time,
             "location": e.location, "description": e.description} for e in q.all()]


def remove_event(record_id):
    e = Event.query.get(record_id)
    if e:
        d = {"id": e.id, "title": e.title}
        db.session.delete(e)
        db.session.commit()
        return d
    return None


# ---------------------------------------------------------------- messages

def add_message(**fields):
    m = Message(
        id=new_id(),
        name=fields.get("name", ""),
        email=fields.get("email", ""),
        message=fields.get("message", ""),
        read=bool(fields.get("read", False)),
        created_at=utcnow(),
    )
    db.session.add(m)
    db.session.commit()
    return {"id": m.id, "name": m.name, "email": m.email, "message": m.message,
            "read": m.read, "created_at": _iso(m.created_at)}


def list_messages():
    return [{"id": m.id, "name": m.name, "email": m.email, "message": m.message,
             "read": m.read, "created_at": _iso(m.created_at)}
            for m in Message.query.order_by(Message.created_at.desc()).all()]


def remove_message(record_id):
    m = Message.query.get(record_id)
    if m:
        d = {"id": m.id, "name": m.name}
        db.session.delete(m)
        db.session.commit()
        return d
    return None


# ---------------------------------------------------------------- join requests

def add_join_request(**fields):
    j = JoinRequest(
        id=new_id(),
        name=fields.get("name", ""),
        email=fields.get("email", ""),
        phone=fields.get("phone", ""),
        relationship=fields.get("relationship", ""),
        location=fields.get("location", ""),
        note=fields.get("note", ""),
        status="pending",
        created_at=utcnow(),
    )
    db.session.add(j)
    db.session.commit()
    return {"id": j.id, "name": j.name, "email": j.email, "phone": j.phone,
            "relationship": j.relationship, "location": j.location,
            "note": j.note, "status": j.status}


def list_join_requests():
    q = JoinRequest.query.filter_by(status="pending").order_by(JoinRequest.created_at.asc())
    return [{"id": j.id, "name": j.name, "email": j.email or "", "phone": j.phone or "",
             "relationship": j.relationship or "", "location": j.location or "",
             "note": j.note or "", "status": j.status} for j in q.all()]


def get_join_request(record_id):
    j = JoinRequest.query.get(record_id)
    if not j:
        return None
    return {"id": j.id, "name": j.name, "email": j.email or "", "phone": j.phone or "",
            "relationship": j.relationship or "", "location": j.location or "",
            "note": j.note or "", "status": j.status}


def resolve_join_request(record_id, status):
    j = JoinRequest.query.get(record_id)
    if j:
        j.status = status
        j.resolved_at = utcnow()
        db.session.commit()
    return get_join_request(record_id)


# ---------------------------------------------------------------- notifications

def _lower(value):
    return str(value or "").strip().lower()


def notify(kind, title, body="", link="", actor="", usernames=None, exclude=None):
    wanted = None if usernames is None else {_lower(u) for u in usernames}
    skip = {_lower(u) for u in ([exclude] if isinstance(exclude, str) else (exclude or []))}

    users = User.query.filter_by(is_active=True).all()
    count = 0
    for u in users:
        key = _lower(u.username)
        if key in skip:
            continue
        if wanted is not None and key not in wanted:
            continue
        prefs = u.notify or dict(NOTIFY_DEFAULTS)
        if not prefs.get(kind, True):
            continue
        db.session.add(Notification(
            id=new_id(), username=key, kind=kind, title=title,
            body=body, link=link, actor=actor, created_at=utcnow(),
        ))
        count += 1
    db.session.commit()
    return count


def list_notifications(username, limit=None, unread_only=False):
    q = Notification.query.filter_by(username=_lower(username))
    if unread_only:
        q = q.filter(Notification.read_at.is_(None))
    q = q.order_by(Notification.created_at.desc())
    if limit:
        q = q.limit(limit)
    return [{"id": n.id, "username": n.username, "kind": n.kind, "title": n.title,
             "body": n.body or "", "link": n.link or "", "actor": n.actor or "",
             "created_at": _iso(n.created_at),
             "read_at": _iso(n.read_at) or None} for n in q.all()]


def unread_count(username):
    return Notification.query.filter_by(username=_lower(username)).filter(
        Notification.read_at.is_(None)).count()


def mark_notifications_read(username, record_id=None):
    q = Notification.query.filter_by(username=_lower(username)).filter(
        Notification.read_at.is_(None))
    if record_id:
        q = q.filter_by(id=record_id)
    items = q.all()
    stamp = utcnow()
    for n in items:
        n.read_at = stamp
    db.session.commit()
    return len(items)


def clear_notifications(username):
    q = Notification.query.filter_by(username=_lower(username))
    n = q.count()
    q.delete()
    db.session.commit()
    return n


def set_notify_preferences(username, enabled):
    prefs = dict(NOTIFY_DEFAULTS)
    chosen = {_lower(k) for k in enabled}
    for kind in EDITABLE_NOTIFY_TYPES:
        prefs[kind] = kind in chosen
    prefs["account"] = True
    return update_user(username, notify=prefs)


# ---------------------------------------------------------------- activity log

def add_activity(action, target_type, target, actor, detail=""):
    a = Activity(
        id=new_id(), action=action, target_type=target_type,
        target=target, actor=actor, detail=detail, created_at=utcnow(),
    )
    db.session.add(a)
    db.session.commit()
    return {"id": a.id, "action": a.action, "target_type": a.target_type,
            "target": a.target, "actor": a.actor, "detail": a.detail}


def list_activities(limit=None):
    q = Activity.query.order_by(Activity.created_at.desc())
    if limit:
        q = q.limit(limit)
    return [{"id": a.id, "action": a.action, "target_type": a.target_type,
             "target": a.target, "actor": a.actor, "detail": a.detail or "",
             "created_at": _iso(a.created_at)} for a in q.all()]


# ---------------------------------------------------------------- seeding

ADMIN_PROFILE = {
    "name": "Belay Demeke",
    "relationship": "Father & Patriarch",
    "bio": "The heart of our home.",
}


def seed_defaults():
    """Create the first administrator, and only on a completely empty database.

    The passphrase comes from ADMIN_PASSWORD. Nothing is shipped in the code:
    this runs during boot on a public host, so a built-in password would be an
    account any visitor could claim a few seconds after the service starts.
    """
    if User.query.count():
        return None
    password = os.environ.get("ADMIN_PASSWORD") or ""
    if not password:
        return None
    username = (os.environ.get("ADMIN_USERNAME") or "belay").strip().lower()
    profile = dict(ADMIN_PROFILE)
    profile["name"] = os.environ.get("ADMIN_NAME") or profile["name"]
    return create_user(username, password, role=Config.ROLE_ADMIN, **profile)


# ---------------------------------------------------------------- legacy JSON store

# (file, model, column that identifies an existing row). users.json is a dict
# keyed by username, so its records are matched on username; everything else is
# a list of rows keyed by id.
LEGACY_SOURCES = (
    ("users.json", User, "username"),
    ("posts.json", Post, "id"),
    ("announcements.json", Announcement, "id"),
    ("events.json", Event, "id"),
    ("messages.json", Message, "id"),
    ("join_requests.json", JoinRequest, "id"),
    ("notifications.json", Notification, "id"),
    ("activity_log.json", Activity, "id"),
)


def _row_fields(model, record):
    """Map a JSON record onto the model's columns, parsing timestamps."""
    fields = {}
    for column in model.__table__.columns:
        if column.name not in record:
            continue
        value = record[column.name]
        if isinstance(column.type, DateTime) and isinstance(value, str):
            try:
                moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            # Columns are naive UTC: both drivers store the same wall time.
            value = (moment.astimezone(timezone.utc).replace(tzinfo=None)
                     if moment.tzinfo else moment)
        if column.foreign_keys and value == "":
            value = None
        fields[column.name] = value
    return fields


def import_legacy_json(data_dir=None):
    """Copy the old JSON store into the database and report {file: (added, kept)}.

    Password hashes are carried over as they are, so every family member keeps
    signing in with the passphrase they already chose. Rows are matched on
    username or id: re-running adds nothing and changes nothing, which makes the
    command safe to repeat against a live database.
    """
    root = Path(data_dir) if data_dir else Config.DATA_DIR
    totals = {}
    for filename, model, key in LEGACY_SOURCES:
        path = root / filename
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        records = raw.values() if isinstance(raw, dict) else raw
        added = kept = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            marker = record.get(key)
            if not marker:
                continue
            if model.query.filter(getattr(model, key) == marker).first():
                kept += 1
                continue
            db.session.add(model(**_row_fields(model, record)))
            added += 1
        db.session.commit()
        if added or kept:
            totals[filename] = (added, kept)
    return totals