"""JSON-backed persistence for the family website.

The original app stored data in loose JSON files read/written inline in the route
handlers. That is kept here, but wrapped so writes are atomic and safe under the
threaded Flask dev server and gunicorn workers.
"""

import copy
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import Config
from security import hash_password

_lock = threading.RLock()

USER_DEFAULTS = {
    "name": "",
    "role": Config.ROLE_MEMBER,
    "email": "",
    "phone": "",
    "relationship": "",
    "location": "",
    "bio": "",
    "avatar": "",
    "is_active": True,
}

# What a member can be notified about. "account" is a security notice and stays on.
NOTIFY_TYPES = ("announcement", "event", "memory", "message", "account")
EDITABLE_NOTIFY_TYPES = ("announcement", "event", "memory", "message")
NOTIFY_DEFAULTS = {kind: True for kind in NOTIFY_TYPES}
NOTIFY_KEEP_PER_USER = 40


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


def display_date(iso_timestamp: str) -> str:
    """Render a stored ISO timestamp as e.g. 'Oct 09, 2026'."""
    if not iso_timestamp:
        return ""
    try:
        return datetime.fromisoformat(iso_timestamp).astimezone().strftime("%b %d, %Y")
    except ValueError:
        return iso_timestamp


class JsonStore:
    """A single JSON document on disk, accessed under a process-wide lock."""

    def __init__(self, path: Path, default):
        self.path = Path(path)
        self.default = default
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read_unlocked(self):
        if not self.path.is_file():
            return copy.deepcopy(self.default)
        try:
            with self.path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return copy.deepcopy(self.default)
        return data if type(data) is type(self.default) else copy.deepcopy(self.default)

    def _write_unlocked(self, data) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)

    def load(self):
        with _lock:
            return self._read_unlocked()

    def save(self, data) -> None:
        with _lock:
            self._write_unlocked(data)

    def mutate(self, fn):
        """Read-modify-write as one atomic operation. ``fn`` receives the data."""
        with _lock:
            data = self._read_unlocked()
            result = fn(data)
            self._write_unlocked(data)
            return result


users = JsonStore(Config.USERS_FILE, {})
posts = JsonStore(Config.POSTS_FILE, [])
announcements = JsonStore(Config.ANNOUNCEMENTS_FILE, [])
events = JsonStore(Config.EVENTS_FILE, [])
messages = JsonStore(Config.MESSAGES_FILE, [])
join_requests = JsonStore(Config.JOIN_REQUESTS_FILE, [])
notifications = JsonStore(Config.NOTIFICATIONS_FILE, [])
activity = JsonStore(Config.ACTIVITY_FILE, [])


# ---------------------------------------------------------------- users

def _normalise_user(username: str, record: dict) -> dict:
    """Fill in fields added after the original users.json was created."""
    merged = dict(USER_DEFAULTS)
    merged.update(record or {})
    merged["username"] = username
    merged.setdefault("joined_at", utcnow().isoformat())
    merged["role"] = merged.get("role") if merged.get("role") in ("admin", "member") else Config.ROLE_MEMBER
    # Rebuilt rather than shared so no two accounts ever point at one preferences dict.
    prefs = dict(NOTIFY_DEFAULTS)
    prefs.update({k: bool(v) for k, v in (record or {}).get("notify", {}).items() if k in prefs})
    prefs["account"] = True
    merged["notify"] = prefs
    return merged


def migrate_users() -> None:
    """Add missing profile fields to pre-existing accounts without touching hashes."""
    seed_lookup = {username: (relationship, bio) for username, _n, _r, relationship, bio in FAMILY_SEED}

    def apply(data: dict) -> None:
        for username, record in list(data.items()):
            if not isinstance(record, dict):
                continue
            merged = _normalise_user(username, record)
            # Backfill the founding members' bios, but never overwrite a user's own edits.
            if username in seed_lookup:
                relationship, bio = seed_lookup[username]
                merged["relationship"] = merged["relationship"] or relationship
                merged["bio"] = merged["bio"] or bio
            if merged != record:
                data[username] = merged

    users.mutate(apply)


def get_user(username: str):
    """Look up an account. Older records were keyed capitalised, so match case-insensitively."""
    key = (username or "").strip().lower()
    if not key:
        return None
    data = users.load()
    if key in data:
        return data[key]
    return next((record for stored, record in data.items() if stored.strip().lower() == key), None)


def find_user_by_email(email: str):
    if not email:
        return None
    target = email.strip().lower()
    for username, record in users.load().items():
        if (record.get("email") or "").strip().lower() == target:
            return username, record
    return None


def list_members() -> list:
    """All active members, admins first then alphabetical."""
    records = [_normalise_user(u, r) for u, r in users.load().items() if r.get("is_active", True)]
    records.sort(key=lambda r: (r["role"] != Config.ROLE_ADMIN, r["name"].lower()))
    return records


def list_inactive_members() -> list:
    """Deactivated accounts. Hidden from the directory but still editable by an admin."""
    records = [_normalise_user(u, r) for u, r in users.load().items() if not r.get("is_active", True)]
    records.sort(key=lambda r: r["name"].lower())
    return records


def create_user(username: str, password: str, **profile) -> dict:
    username = username.strip().lower()

    def apply(data: dict) -> dict:
        record = _normalise_user(username, {**USER_DEFAULTS, **profile})
        record["password"] = hash_password(password)
        record["joined_at"] = utcnow().isoformat()
        data[username] = record
        return record

    return users.mutate(apply)


def update_user(username: str, **changes) -> dict:
    def apply(data: dict) -> dict:
        record = data.get(username)
        if record is None:
            return None
        record.update({k: v for k, v in changes.items() if v is not None})
        data[username] = _normalise_user(username, record)
        return data[username]

    return users.mutate(apply)


def set_password(username: str, password: str) -> None:
    update_user(username, password=hash_password(password))


def delete_user(username: str) -> None:
    def apply(data: dict) -> None:
        data.pop(username, None)

    users.mutate(apply)


def avatar_files() -> set:
    """Upload filenames shown as profile photos.

    Profile photos appear in the public family directory, so the media route serves
    exactly these files to anonymous visitors; every other upload needs a session.
    """
    names = set()
    for record in users.load().values():
        avatar = (record.get("avatar") or "") if isinstance(record, dict) else ""
        if avatar.startswith("/uploads/"):
            names.add(avatar.rsplit("/", 1)[1])
    return names


# ---------------------------------------------------------------- posts

def add_post(**fields) -> dict:
    post = {"id": new_id(), "created_at": utcnow().isoformat(), **fields}

    def apply(data: list) -> None:
        data.append(post)

    posts.mutate(apply)
    return post


def get_post(post_id: str):
    return next((p for p in posts.load() if p.get("id") == post_id), None)


def list_posts(visibility: str | None = None, limit: int | None = None) -> list:
    """Newest first, optionally filtered to one visibility level."""
    items = sorted(posts.load(), key=lambda p: p.get("created_at", ""), reverse=True)
    if visibility:
        items = [p for p in items if p.get("visibility", "family") == visibility]
    return items[:limit] if limit else items


def update_post(post_id: str, **changes):
    def apply(data: list):
        target = next((p for p in data if p.get("id") == post_id), None)
        if target is not None:
            target.update(changes)
        return target

    return posts.mutate(apply)


def remove_post(post_id: str):
    target = get_post(post_id)

    def apply(data: list) -> None:
        data[:] = [p for p in data if p.get("id") != post_id]

    posts.mutate(apply)
    return target


# ---------------------------------------------------------------- records

def _append(store: JsonStore, **fields) -> dict:
    record = {"id": new_id(), "created_at": utcnow().isoformat(), **fields}

    def apply(data: list) -> None:
        data.append(record)

    store.mutate(apply)
    return record


def _remove_by_id(store: JsonStore, record_id: str):
    target = next((r for r in store.load() if r.get("id") == record_id), None)

    def apply(data: list) -> None:
        data[:] = [r for r in data if r.get("id") != record_id]

    store.mutate(apply)
    return target


def add_announcement(**fields) -> dict:
    return _append(announcements, **fields)


def list_announcements(limit: int | None = None) -> list:
    items = sorted(announcements.load(), key=lambda a: a.get("created_at", ""), reverse=True)
    return items[:limit] if limit else items


def remove_announcement(record_id: str):
    return _remove_by_id(announcements, record_id)


def add_event(**fields) -> dict:
    return _append(events, **fields)


def list_events(upcoming_only: bool = False) -> list:
    items = events.load()
    if upcoming_only:
        today = utcnow().date().isoformat()
        items = [e for e in items if (e.get("date") or "") >= today]
    return sorted(items, key=lambda e: e.get("date") or "9999")


def remove_event(record_id: str):
    return _remove_by_id(events, record_id)


def add_message(**fields) -> dict:
    return _append(messages, **fields)


def list_messages() -> list:
    return sorted(messages.load(), key=lambda m: m.get("created_at", ""), reverse=True)


def remove_message(record_id: str):
    return _remove_by_id(messages, record_id)


# ---------------------------------------------------------------- join requests

def add_join_request(**fields) -> dict:
    """A public access request. It is not an account until an admin approves it."""
    return _append(join_requests, status="pending", **fields)


def list_join_requests() -> list:
    """Still-untouched requests, oldest first so the queue reads in arrival order."""
    pending = [r for r in join_requests.load() if r.get("status") == "pending"]
    return sorted(pending, key=lambda r: r.get("created_at") or "")


def get_join_request(record_id: str):
    return next((r for r in join_requests.load() if r.get("id") == record_id), None)


def resolve_join_request(record_id: str, status: str):
    """Mark a request approved or declined. Records stay for the audit trail."""
    def apply(data: list) -> dict:
        target = next((r for r in data if r.get("id") == record_id), None)
        if target is not None:
            target["status"] = status
            target["resolved_at"] = utcnow().isoformat()
        return target

    return join_requests.mutate(apply)


# ---------------------------------------------------------------- notifications

def _lower(value: str) -> str:
    return str(value or "").strip().lower()


def notify(kind: str, title: str, body: str = "", link: str = "",
           actor: str = "", usernames=None, exclude=None) -> int:
    """Queue one inbox entry per recipient, skipping anyone who muted this kind.

    ``usernames=None`` means every active member. ``exclude`` is normally the actor,
    who should never be notified about their own post.
    """
    wanted = None if usernames is None else {_lower(u) for u in usernames}
    skip = {_lower(u) for u in ([exclude] if isinstance(exclude, str) else (exclude or []))}

    entries = []
    for stored, record in users.load().items():
        key = _lower(stored)
        if key in skip or (wanted is not None and key not in wanted):
            continue
        if not (record or {}).get("is_active", True):
            continue
        if not _normalise_user(stored, record)["notify"].get(kind, True):
            continue
        entries.append({
            "id": new_id(), "username": key, "kind": kind,
            "title": title, "body": body, "link": link, "actor": actor,
            "created_at": utcnow().isoformat(), "read_at": None,
        })

    if not entries:
        return 0

    def apply(data: list) -> None:
        data.extend(entries)
        # Oldest first per inbox so one busy member can't crowd out the rest.
        seen: dict[str, int] = {}
        keep = []
        for entry in sorted(data, key=lambda n: n.get("created_at", ""), reverse=True):
            owner = entry.get("username", "")
            seen[owner] = seen.get(owner, 0) + 1
            if seen[owner] <= NOTIFY_KEEP_PER_USER:
                keep.append(entry)
        data[:] = keep

    notifications.mutate(apply)
    return len(entries)


def list_notifications(username: str, limit: int | None = None,
                       unread_only: bool = False) -> list:
    key = _lower(username)
    items = sorted((n for n in notifications.load() if n.get("username") == key),
                   key=lambda n: n.get("created_at", ""), reverse=True)
    if unread_only:
        items = [n for n in items if not n.get("read_at")]
    return items[:limit] if limit else items


def unread_count(username: str) -> int:
    return len(list_notifications(username, unread_only=True))


def mark_notifications_read(username: str, record_id: str | None = None) -> int:
    key = _lower(username)
    stamp = utcnow().isoformat()

    def apply(data: list) -> int:
        touched = 0
        for entry in data:
            if entry.get("username") != key or entry.get("read_at"):
                continue
            if record_id and entry.get("id") != record_id:
                continue
            entry["read_at"] = stamp
            touched += 1
        return touched

    return notifications.mutate(apply)


def clear_notifications(username: str) -> int:
    """Empty one member's inbox, e.g. after they delete their account."""
    key = _lower(username)

    def apply(data: list) -> int:
        keep = [n for n in data if n.get("username") != key]
        removed = len(data) - len(keep)
        data[:] = keep
        return removed

    return notifications.mutate(apply)


def set_notify_preferences(username: str, enabled) -> dict:
    prefs = dict(NOTIFY_DEFAULTS)
    chosen = {_lower(kind) for kind in enabled}
    for kind in EDITABLE_NOTIFY_TYPES:
        prefs[kind] = kind in chosen
    prefs["account"] = True
    return update_user(username, notify=prefs)


# ---------------------------------------------------------------- activity log

def add_activity(action: str, target_type: str, target: str, actor: str,
                 detail: str = "") -> dict:
    """Record one admin action for the dashboard activity log."""
    record = {"id": new_id(), "action": action, "target_type": target_type,
              "target": target, "actor": actor, "detail": detail,
              "created_at": utcnow().isoformat()}

    def apply(data: list) -> None:
        data.append(record)
        data[:] = sorted(data, key=lambda r: r.get("created_at", ""))[-Config.ACTIVITY_KEEP:]

    activity.mutate(apply)
    return record


def list_activities(limit: int | None = None) -> list:
    items = sorted(activity.load(), key=lambda r: r.get("created_at", ""), reverse=True)
    return items[:limit] if limit else items


# ---------------------------------------------------------------- seeding

FAMILY_SEED = [
    ("belay", "Belay Demeke", Config.ROLE_ADMIN, "Father & Patriarch",
     "The heart of our home. A man of wisdom, patience, and quiet strength."),
    ("almaz", "Almaz Demeke", Config.ROLE_MEMBER, "Mother & Matriarch",
     "The warmth of our family. Her cooking and kindness bring everyone together."),
    ("dawit", "Dawit Belay", Config.ROLE_MEMBER, "Eldest Son",
     "The dependable big brother. Always ready to lend a hand or share advice."),
    ("hanna", "Hanna Belay", Config.ROLE_MEMBER, "Daughter",
     "Creative soul of the family. Our photographer, baker, and storyteller."),
    ("yonas", "Yonas Belay", Config.ROLE_MEMBER, "Youngest Son",
     "Full of energy and dreams. Keeps us all laughing and on our toes."),
]


def seed_defaults() -> None:
    """Create the founding accounts and starter content on a fresh install only."""
    if not users.load():
        for username, name, role, relationship, bio in FAMILY_SEED:
            create_user(
                username,
                os.environ.get(f"SEED_PASSWORD_{username.upper()}", "family2024"),
                name=name, role=role, relationship=relationship, bio=bio,
            )

    if not announcements.load():
        add_announcement(
            title="Welcome to our family website",
            body="This is our private space to share photos, videos, announcements and "
                 "upcoming gatherings. Register with your real name so we all know who you are.",
            author="Belay Demeke",
            pinned=True,
        )

    if not events.load():
        year = utcnow().year
        add_event(title="Christmas Eve Dinner", date=f"{year}-12-24", time="18:00",
                  location="Family Home", description="The whole family around one table.")
        add_event(title="New Year's Brunch", date=f"{year + 1}-01-01", time="11:00",
                  location="Family Home", description="Slow morning, big breakfast, better company.")
