"""Belay Demeke Family Website — Flask application."""

import os
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlsplit

import click
from flask import (
    Flask, abort, flash, jsonify, redirect, render_template,
    request, Response, send_from_directory, session, url_for,
)
from flask_migrate import Migrate
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
import sqlalchemy as sa
from werkzeug.utils import secure_filename

import storage
from config import ALLOWED_EXT, Config, media_kind
from models import db
from security import (
    admin_required, client_key, current_member, email_problem,
    init_csrf, init_session, login_required, login_throttle, password_problem,
    safe_text, username_problem, verify_password,
)

app = Flask(__name__)
app.config.from_object(Config)

# ------------------------------------------------------------
# Database setup — must come right after app config
# ------------------------------------------------------------
db.init_app(app)
migrate = Migrate(app, db)

init_session(app)
init_csrf(app)

RESET_TOKEN_TTL = 30 * 60          # seconds a reset link stays valid
MAX_THROTTLE_REQUESTS = 5          # per key per hour, for reset links and join requests
THROTTLE_WINDOW = timedelta(hours=1)
_throttle_hits: dict[str, list[datetime]] = {}


# ============================================
# Helpers
# ============================================

@app.context_processor
def inject_globals():
    """Make the signed-in member and a few site constants available to templates."""
    me = current_member()
    return {
        "member": me,
        "is_admin": bool(me and me["role"] == Config.ROLE_ADMIN),
        "notifications": storage.list_notifications(me["username"], limit=6) if me else [],
        "unread_count": storage.unread_count(me["username"]) if me else 0,
        "family_name": Config.FAMILY_NAME,
        "family_years": max(datetime.now().year - Config.FAMILY_SINCE, 1),
        "family_generations": Config.FAMILY_GENERATIONS,
        "current_year": datetime.now().year,
    }


def wants_json() -> bool:
    """True when the call comes from our own fetch() helper rather than a form post."""
    return bool(request.headers.get("X-CSRF-Token"))


@app.template_filter("when")
def when(iso: str) -> str:
    """Turn a stored timestamp into 'just now', '5m ago', 'Oct 09, 2026', …"""
    try:
        stamp = datetime.fromisoformat(iso or "")
    except ValueError:
        return ""
    seconds = (datetime.now(stamp.tzinfo) - stamp).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    if seconds < 172800:
        return "yesterday"
    return storage.display_date(iso)


def own_area(me, anchor: str = "") -> str:
    """A signed-in person's own pages: administrators get the dashboard, members /me."""
    endpoint = "dashboard" if me["role"] == Config.ROLE_ADMIN else "my_space"
    return url_for(endpoint, _anchor=anchor) if anchor else url_for(endpoint)


# Notification links are stored once for everyone, so a member's copy of a dashboard
# link has to land somewhere they can actually open: family content on the landing page,
# account items in their own space.
MEMBER_LINKS = {
    "album": ("index", "gallery"),
    "announcements": ("index", "announcements"),
    "events": ("index", "events"),
    "members": ("index", "family"),
    "profile": ("my_space", "profile"),
    "notifications": ("my_space", "notifications"),
}


@app.template_filter("own_link")
def own_link(link: str) -> str:
    me = current_member()
    if not link or not me or me["role"] == Config.ROLE_ADMIN:
        return link
    parts = urlsplit(link)
    if parts.path != "/dashboard":
        return link
    page, anchor = MEMBER_LINKS.get(parts.fragment, ("my_space", "notifications"))
    return url_for(page, _anchor=anchor)


def safe_next(target: str | None) -> str:
    """Only allow same-site relative redirects after login."""
    if not target:
        return own_area(current_member())
    parsed = urlparse(target)
    if parsed.netloc or parsed.scheme or not target.startswith("/"):
        return own_area(current_member())
    return target


def save_upload(file_storage, allowed: set[str] | None = None) -> tuple[str, str] | None:
    """Persist one uploaded file under a random name. Returns (stored_name, ext)."""
    original = secure_filename(file_storage.filename or "")
    if "." not in original:
        return None
    ext = original.rsplit(".", 1)[1].lower()
    if ext not in (allowed or ALLOWED_EXT):
        return None
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(Config.UPLOAD_DIR / stored_name)
    return stored_name, ext


def delete_upload(stored_name: str) -> None:
    if not stored_name:
        return
    path = Config.UPLOAD_DIR / secure_filename(stored_name)
    if path.is_file():
        path.unlink()


AVATAR_CONTENT_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                        "gif": "image/gif", "webp": "image/webp"}


def read_image_upload(upload):
    """Validate a profile photo and return (bytes, content type), or None."""
    if not upload or not upload.filename:
        return None
    original = secure_filename(upload.filename)
    if "." not in original:
        return None
    content_type = AVATAR_CONTENT_TYPES.get(original.rsplit(".", 1)[1].lower())
    if not content_type:
        return None
    data = upload.read()
    if not data or len(data) > Config.AVATAR_MAX_BYTES:
        return None
    return data, content_type


def add_missing_columns() -> None:
    """Add model columns that an existing table does not have yet.

    create_all() only builds absent tables, so a column added to a model after the
    first deploy would otherwise never reach the database. Only nullable columns
    are handled here; anything stricter wants a real migration.
    """
    inspector = sa.inspect(db.engine)
    for name, table in db.metadata.tables.items():
        if name not in inspector.get_table_names():
            continue
        present = {column["name"] for column in inspector.get_columns(name)}
        for column in table.columns:
            if column.name in present or not column.nullable:
                continue
            db.session.execute(sa.text(
                f'ALTER TABLE {name} ADD COLUMN "{column.name}" '
                f'{column.type.compile(db.engine.dialect)}'))
    db.session.commit()


def _reset_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(app.secret_key, salt="password-reset")


def throttle_wait(key: str) -> int:
    """Seconds before this key is back under MAX_THROTTLE_REQUESTS; 0 when it is clear."""
    now = datetime.utcnow()
    recent = [t for t in _throttle_hits.get(key, []) if t > now - THROTTLE_WINDOW]
    _throttle_hits[key] = recent
    if len(recent) < MAX_THROTTLE_REQUESTS:
        return 0
    return max(1, int((recent[0] + THROTTLE_WINDOW - now).total_seconds()))


def throttle_record(key: str) -> None:
    _throttle_hits.setdefault(key, []).append(datetime.utcnow())


def log_activity(action: str, target_type: str, target: str, detail: str = "",
                 actor: str | None = None) -> None:
    """Record who just did what, for the dashboard activity log.

    ``actor`` falls back to the signed-in member; pass it explicitly for events
    that happen outside a session (failed sign-ins, password resets)."""
    if actor is None:
        me = current_member()
        actor = me["name"] if me else "Unknown"
    storage.add_activity(action, target_type, target, actor=actor, detail=detail)


# ============================================
# Public pages
# ============================================

@app.route("/")
def index():
    return render_template(
        "index.html",
        members=storage.list_members(),
        announcements=storage.list_announcements(limit=3),
        events=storage.list_events(upcoming_only=True)[:4],
        gallery=storage.list_posts(visibility="public", limit=6),
        member_count=len(storage.list_members()),
        memory_count=len(storage.list_posts()),
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user"):
        return redirect(own_area(current_member()))

    if request.method == "POST":
        username = safe_text(request.form.get("username"), 64).lower()
        password = request.form.get("password", "")
        throttle_key = client_key()

        wait = login_throttle.seconds_remaining(throttle_key)
        if wait:
            flash(f"Too many failed attempts. Please try again in {wait} seconds.", "danger")
            return render_template("login.html", username=username, locked_for=wait)

        record = storage.get_user(username)
        if record and verify_password(record.get("password"), password):
            if not record.get("is_active", True):
                flash("This account has been deactivated. Please contact a family admin.", "danger")
                return render_template("login.html", username=username)

            login_throttle.reset(throttle_key)
            session["user"] = {"username": username, "name": record["name"], "role": record["role"]}
            log_activity("signed_in", "session", username, detail=request.remote_addr or "")
            flash(f"Welcome back, {record['name'].split()[0]}!", "success")
            return redirect(safe_next(request.form.get("next") or request.args.get("next")))

        login_throttle.record_failure(throttle_key)
        attempted = storage.get_user(username)
        log_activity("login_failed", "session", username or "unknown",
                     detail=request.remote_addr or "",
                     actor=attempted["name"] if attempted else (username or "Unknown"))
        flash("Invalid username or password.", "danger")

    return render_template("login.html", username="", next=request.args.get("next", ""))


@app.route("/join", methods=["GET", "POST"])
def join():
    """Public access request. Accounts are created by an admin, never here."""
    if session.get("user"):
        return redirect(own_area(current_member()))

    form = {"name": "", "email": "", "phone": "",
            "relationship": "", "location": "", "note": ""}

    if request.method == "POST":
        form = {key: safe_text(request.form.get(key), limit) for key, limit in (
            ("name", 80), ("email", 120), ("phone", 32),
            ("relationship", 60), ("location", 80), ("note", 600),
        )}

        error = None
        if not form["name"]:
            error = "Please enter your full name."
        elif (problem := email_problem(form["email"])):
            error = problem
        elif storage.find_user_by_email(form["email"]):
            error = "That email already has a family account. Try signing in instead."
        elif any(r.get("email", "").lower() == form["email"].lower()
                 for r in storage.list_join_requests()):
            error = "We already have a request from that email. A family admin will be in touch."
        elif throttle_wait(f"join:{client_key()}"):
            error = "Please wait a while before sending another request."

        if error:
            flash(error, "danger")
            return render_template("join.html", form=form)

        storage.add_join_request(**form)
        throttle_record(f"join:{client_key()}")
        flash("Your request has been sent. A family admin will set up your account.", "success")
        return redirect(url_for("login"))

    return render_template("join.html", form=form)


@app.route("/logout")
def logout():
    me = current_member()
    if me:
        log_activity("signed_out", "session", me["username"], detail=request.remote_addr or "")
    session.pop("user", None)
    flash("You have been signed out.", "info")
    return redirect(url_for("index"))


@app.route("/contact", methods=["POST"])
def contact():
    name = safe_text(request.form.get("name"), 80)
    email = safe_text(request.form.get("email"), 120)
    message = safe_text(request.form.get("message"), 2000)

    if not name or not message or email_problem(email):
        flash("Please fill in your name, a valid email address, and a message.", "danger")
        return redirect(url_for("index", _anchor="contact"))

    storage.add_message(name=name, email=email, message=message, read=False)
    storage.notify(
        "message",
        title=f"New message from {name}",
        body=message,
        link=url_for("dashboard", _anchor="messages"),
        actor=name,
        usernames=[m["username"] for m in storage.list_members()
                   if m.get("role") == Config.ROLE_ADMIN],
    )
    flash("Thank you! Your message has been sent to the family.", "success")
    return redirect(url_for("index", _anchor="contact"))


# ============================================
# Password reset
# ============================================

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    ttl_minutes = RESET_TOKEN_TTL // 60

    if request.method == "GET":
        return render_template("forgot_password.html", account="", errors={},
                               locked_for=None, reset_ttl_minutes=ttl_minutes)

    account = safe_text(request.form.get("account"), 120)
    errors = {}
    if not account:
        errors["account"] = "Enter your username or email."

    if errors:
        return render_template("forgot_password.html", account=account, errors=errors,
                               locked_for=None, reset_ttl_minutes=ttl_minutes)

    user = storage.get_user(account.lower())
    if not user and (found := storage.find_user_by_email(account)):
        user = storage.get_user(found[0])

    device_key = f"reset:device:{client_key()}"
    if wait := throttle_wait(device_key):
        return render_template("forgot_password.html", account=account, errors={},
                               locked_for=wait, reset_ttl_minutes=ttl_minutes)

    account_key = f"reset:{user['username']}" if user else None
    if account_key and not throttle_wait(account_key):
        token = _reset_serializer().dumps(user["username"])
        reset_url = url_for("reset_password", token=token, _external=True)
        app.logger.info("Password reset link for %s: %s", user["username"], reset_url)
        throttle_record(account_key)
        log_activity("requested", "password reset", user["username"], actor=user["name"])
    if not user:
        log_activity("requested", "password reset", "unknown account",
                     actor=account, detail=request.remote_addr or "")

    throttle_record(device_key)
    flash("If that account exists, a reset link is on its way.", "info")
    return redirect(url_for("forgot_password"))


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        username = _reset_serializer().loads(token, max_age=RESET_TOKEN_TTL)
    except (BadSignature, SignatureExpired):
        flash("That reset link is invalid or has expired. Please request a new one.", "warning")
        return redirect(url_for("forgot_password"))

    user = storage.get_user(username)
    if not user or not user.get("is_active", True):
        flash("That reset link no longer matches an active account.", "warning")
        return redirect(url_for("forgot_password"))

    session.pop("user", None)

    if request.method == "GET":
        return render_template("reset_password.html", token=token,
                               account=user["username"], errors={})

    password = request.form.get("password") or ""
    errors = {}
    if (problem := password_problem(password, request.form.get("password2") or "")):
        errors["password"] = problem

    if errors:
        return render_template("reset_password.html", token=token,
                               account=user["username"], errors=errors)

    storage.set_password(user["username"], password)
    log_activity("completed", "password reset", user["username"], actor=user["name"])
    flash("Password updated. You can sign in now.", "success")
    return redirect(url_for("login"))


# ============================================
# Member area
# ============================================

@app.route("/me")
@login_required
def my_space():
    """What a regular member gets: their own inbox and their own profile."""
    me = current_member()
    return render_template(
        "my_space.html",
        me=me,
        notifications=storage.list_notifications(me["username"], limit=30),
    )


@app.route("/dashboard")
@admin_required
def dashboard():
    """The administrator panel: family content plus everything that needs the role."""
    me = current_member()
    return render_template(
        "dashboard.html",
        me=me,
        posts=storage.list_posts(),
        members=storage.list_members(),
        inactive_members=storage.list_inactive_members(),
        announcements=storage.list_announcements(),
        events=storage.list_events(),
        messages=storage.list_messages(),
        join_requests=storage.list_join_requests(),
        activities=storage.list_activities(limit=100),
        notifications=storage.list_notifications(me["username"], limit=30),
    )


@app.route("/posts", methods=["POST"])
@admin_required
def create_post():
    me = current_member()
    files = [f for f in request.files.getlist("files") if f and f.filename]
    title = safe_text(request.form.get("title"), 120)
    description = safe_text(request.form.get("desc"), 1000)
    visibility = request.form.get("visibility", "family")

    if not files:
        return jsonify({"error": "Please choose at least one photo or video."}), 400
    if not title:
        return jsonify({"error": "Please give your post a title."}), 400
    if visibility not in {"family", "public"}:
        visibility = "family"

    created, rejected = [], []
    for upload in files:
        saved = save_upload(upload)
        if not saved:
            rejected.append(secure_filename(upload.filename) or "unknown file")
            continue
        stored_name, ext = saved
        created.append(storage.add_post(
            title=title,
            desc=description,
            visibility=visibility,
            filename=stored_name,
            original=secure_filename(upload.filename),
            url=f"/uploads/{stored_name}",
            kind=media_kind(ext),
            type=ext,
            uploaded_by=me["name"],
            uploaded_by_username=me["username"],
            date=storage.display_date(storage.utcnow().isoformat()),
        ))

    if not created:
        return jsonify({"error": f"Unsupported file type: {', '.join(rejected[:3])}"}), 400

    storage.notify(
        "memory",
        actor=me["name"],
        title=f"{me['name']} shared {len(created)} new "
              f"{'memory' if len(created) == 1 else 'memories'}",
        body=title,
        link=url_for("dashboard", _anchor="album"),
        exclude=me["username"],
    )

    payload = {"success": True, "count": len(created)}
    if rejected:
        payload["skipped"] = rejected
    log_activity("created", "memory", title,
                 detail=f"{len(created)} file{'s' if len(created) != 1 else ''}")
    return jsonify(payload)


@app.route("/posts/<post_id>/edit", methods=["POST"])
@admin_required
def edit_post(post_id):
    """Admins can retitle, redescribe or re-visibility any family album memory."""
    post = storage.get_post(post_id)
    if not post:
        return jsonify({"error": "That memory no longer exists."}), 404

    title = safe_text(request.form.get("title"), 120)
    description = safe_text(request.form.get("desc"), 1000)
    visibility = request.form.get("visibility", post.get("visibility", "family"))

    if not title:
        return jsonify({"error": "Please give the memory a title."}), 400
    if visibility not in {"family", "public"}:
        visibility = post.get("visibility", "family")

    changes = {"title": title, "desc": description, "visibility": visibility}
    storage.update_post(post_id, **changes)
    log_activity("edited", "memory", title,
                 detail=f"visibility: {post.get('visibility', 'family')} → {visibility}")
    return jsonify({"success": True, **changes})


@app.route("/posts/<post_id>/delete", methods=["POST"])
@admin_required
def delete_post(post_id):
    post = storage.get_post(post_id)

    if not post:
        return jsonify({"error": "That memory no longer exists."}), 404

    storage.remove_post(post_id)
    delete_upload(post.get("filename", ""))
    log_activity("deleted", "memory", post.get("title", "Untitled memory"))
    return jsonify({"success": True})


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    """Serve stored media. Public posts and profile photos are open; the rest needs a session."""
    safe_name = secure_filename(filename)
    post = next((p for p in storage.list_posts() if p.get("filename") == safe_name), None)
    is_public = safe_name in storage.avatar_files() or bool(
        post and post.get("visibility", "family") == "public")

    if not is_public and not session.get("user"):
        abort(403)
    if not (Config.UPLOAD_DIR / safe_name).is_file():
        abort(404)
    return send_from_directory(Config.UPLOAD_DIR, safe_name)


@app.route("/profile", methods=["POST"])
@login_required
def update_profile():
    me = current_member()
    name = safe_text(request.form.get("name"), 80)
    email = safe_text(request.form.get("email"), 120)
    back = redirect(own_area(me, "profile"))

    if not name:
        flash("Your name cannot be empty.", "danger")
        return back
    if email and (problem := email_problem(email)):
        flash(problem, "danger")
        return back

    taken = storage.find_user_by_email(email)
    if email and taken and taken[0] != me["username"]:
        flash("That email is already used by another family member.", "danger")
        return back

    storage.update_user(
        me["username"],
        name=name,
        email=email,
        phone=safe_text(request.form.get("phone"), 32),
        relationship=safe_text(request.form.get("relationship"), 60),
        location=safe_text(request.form.get("location"), 80),
        bio=safe_text(request.form.get("bio"), 600),
    )
    session["user"]["name"] = name
    flash("Your profile has been updated.", "success")
    return back


@app.route("/profile/avatar", methods=["POST"])
@login_required
def upload_avatar():
    """Members set their own profile photo; the bytes go into the database."""
    me = current_member()
    back = redirect(own_area(me, "profile"))

    picture = read_image_upload(request.files.get("avatar"))
    if not picture:
        flash("Choose a PNG, JPG, GIF or WEBP photo, 5 MB or smaller.", "danger")
        return back

    storage.set_avatar(me["username"], *picture)
    flash("Your profile photo has been updated.", "success")
    return back


@app.route("/password", methods=["POST"])
@login_required
def change_password():
    me = current_member()
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")

    if not verify_password(me.get("password"), current):
        flash("Your current password is incorrect.", "danger")
    elif (error := password_problem(new, confirm)):
        flash(error, "danger")
    else:
        storage.set_password(me["username"], new)
        log_activity("changed", "password", me["username"])
        flash("Your password has been changed.", "success")

    return redirect(own_area(me, "profile"))


# ============================================
# Notifications
# ============================================

@app.route("/notifications/<record_id>/read", methods=["POST"])
@login_required
def read_notification(record_id):
    me = current_member()
    storage.mark_notifications_read(me["username"], record_id)
    if wants_json():
        return jsonify({"success": True})
    return redirect(own_area(me, "notifications"))


@app.route("/notifications/read-all", methods=["POST"])
@login_required
def read_all_notifications():
    me = current_member()
    count = storage.mark_notifications_read(me["username"])
    if wants_json():
        return jsonify({"success": True, "count": count})
    if count:
        flash(f"Marked {count} notification{'s' if count != 1 else ''} as read.", "info")
    if request.form.get("next"):
        return redirect(safe_next(request.form.get("next")))
    return redirect(own_area(me, "notifications"))


@app.route("/notifications/clear", methods=["POST"])
@login_required
def clear_notifications():
    me = current_member()
    removed = storage.clear_notifications(me["username"])
    flash(f"Removed {removed} notification{'s' if removed != 1 else ''}.", "info")
    return redirect(own_area(me, "notifications"))


@app.route("/notifications/preferences", methods=["POST"])
@login_required
def save_notify_preferences():
    """Members decide which kinds of activity land in their inbox."""
    me = current_member()
    enabled = [kind for kind in storage.EDITABLE_NOTIFY_TYPES
               if request.form.get(f"notify_{kind}") == "on"]
    storage.set_notify_preferences(me["username"], enabled)
    flash("Your notification settings have been saved.", "success")
    return redirect(own_area(me, "notifications"))


# ============================================
# Admin area
# ============================================

@app.route("/announcements", methods=["POST"])
@admin_required
def create_announcement():
    title = safe_text(request.form.get("title"), 120)
    body = safe_text(request.form.get("body"), 2000)
    if not title or not body:
        flash("An announcement needs both a title and a message.", "danger")
        return redirect(url_for("dashboard", _anchor="announcements"))

    me = current_member()
    storage.add_announcement(
        title=title, body=body,
        author=me["name"],
        pinned=request.form.get("pinned") == "on",
    )
    storage.notify(
        "announcement", actor=me["name"], title=title, body=body,
        link=url_for("dashboard", _anchor="announcements"), exclude=me["username"],
    )
    log_activity("created", "announcement", title)
    flash("Announcement published.", "success")
    return redirect(url_for("dashboard", _anchor="announcements"))


@app.route("/announcements/<record_id>/delete", methods=["POST"])
@admin_required
def delete_announcement(record_id):
    note = storage.remove_announcement(record_id)
    log_activity("deleted", "announcement", (note or {}).get("title", "Announcement"))
    flash("Announcement removed.", "info")
    return redirect(url_for("dashboard", _anchor="announcements"))


@app.route("/events", methods=["POST"])
@admin_required
def create_event():
    title = safe_text(request.form.get("title"), 120)
    date = safe_text(request.form.get("date"), 20)
    back = redirect(url_for("dashboard", _anchor="events"))

    if not title or not date:
        flash("An event needs a title and a date.", "danger")
        return back
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        flash("Please choose a valid date.", "danger")
        return back

    me = current_member()
    location = safe_text(request.form.get("location"), 120)
    description = safe_text(request.form.get("description"), 600)
    storage.add_event(
        title=title, date=date,
        time=safe_text(request.form.get("time"), 10),
        location=location,
        description=description,
    )
    storage.notify(
        "event", actor=me["name"], title=f"New event: {title}",
        body=" · ".join(p for p in (storage.display_date(date), location, description) if p),
        link=url_for("dashboard", _anchor="events"), exclude=me["username"],
    )
    log_activity("created", "event", title, detail=storage.display_date(date))
    flash("Event added to the family calendar.", "success")
    return back


@app.route("/events/<record_id>/delete", methods=["POST"])
@admin_required
def delete_event(record_id):
    event = storage.remove_event(record_id)
    log_activity("deleted", "event", (event or {}).get("title", "Event"))
    flash("Event removed.", "info")
    return redirect(url_for("dashboard", _anchor="events"))


@app.route("/members/add", methods=["POST"])
@admin_required
def add_member():
    """Admins create accounts directly and hand out the starting password."""
    form = {key: safe_text(request.form.get(key), limit) for key, limit in (
        ("name", 80), ("username", 24), ("email", 120),
        ("phone", 32), ("relationship", 60), ("location", 80),
    )}
    form["username"] = form["username"].lower()
    password = request.form.get("password", "")
    role = request.form.get("role", Config.ROLE_MEMBER)
    back = redirect(url_for("dashboard", _anchor="members"))

    error = None
    if not form["name"]:
        error = "Please enter the member's full name."
    elif (problem := username_problem(form["username"])):
        error = problem
    elif form["email"] and (problem := email_problem(form["email"])):
        error = problem
    elif (problem := password_problem(password)):
        error = problem
    elif role not in (Config.ROLE_ADMIN, Config.ROLE_MEMBER):
        error = "Choose either admin or member."
    elif storage.get_user(form["username"]):
        error = "That username is already taken."
    elif form["email"] and storage.find_user_by_email(form["email"]):
        error = "That email is already registered."

    if error:
        flash(error, "danger")
        return back

    storage.create_user(
        form["username"], password,
        name=form["name"], role=role, email=form["email"], phone=form["phone"],
        relationship=form["relationship"], location=form["location"],
    )
    storage.notify(
        "account", actor=current_member()["name"],
        title="Your family account is ready",
        body=f"Sign in as '{form['username']}' and set a new password under My Profile.",
        link=url_for("dashboard", _anchor="profile"), usernames=[form["username"]],
    )
    log_activity("created", "member", form["name"],
                 detail="admin" if role == Config.ROLE_ADMIN else "member")
    flash(f"{form['name']} can now sign in as '{form['username']}'.", "success")
    return back


@app.route("/join-requests/<record_id>/approve", methods=["POST"])
@admin_required
def approve_join_request(record_id):
    """Turn an access request into a real account."""
    application = storage.get_join_request(record_id)
    if not application or application.get("status") != "pending":
        abort(404)
    back = redirect(url_for("dashboard", _anchor="requests"))

    username = safe_text(request.form.get("username"), 24).lower()
    password = request.form.get("password", "")
    role = request.form.get("role", Config.ROLE_MEMBER)

    error = None
    if (problem := username_problem(username)):
        error = problem
    elif (problem := password_problem(password)):
        error = problem
    elif role not in (Config.ROLE_ADMIN, Config.ROLE_MEMBER):
        error = "Choose either admin or member."
    elif storage.get_user(username):
        error = "That username is already taken."

    if error:
        flash(error, "danger")
        return back

    storage.create_user(
        username, password,
        name=application["name"], role=role,
        email=application.get("email", ""), phone=application.get("phone", ""),
        relationship=application.get("relationship", ""),
        location=application.get("location", ""),
    )
    storage.resolve_join_request(record_id, "approved")
    storage.notify(
        "account", actor=current_member()["name"],
        title="Welcome to the family website",
        body=f"Your request was approved — sign in as '{username}' and set a new password.",
        link=url_for("dashboard", _anchor="profile"), usernames=[username],
    )
    log_activity("approved", "join request", application["name"],
                 detail=f"new account '{username}'")
    flash(f"{application['name']} is now a family member as '{username}'.", "success")
    return back


@app.route("/join-requests/<record_id>/decline", methods=["POST"])
@admin_required
def decline_join_request(record_id):
    application = storage.get_join_request(record_id)
    if not application or application.get("status") != "pending":
        abort(404)

    storage.resolve_join_request(record_id, "declined")
    log_activity("declined", "join request", application["name"])
    flash(f"Declined the request from {application['name']}.", "info")
    return redirect(url_for("dashboard", _anchor="requests"))


@app.route("/members/<username>/edit", methods=["POST"])
@admin_required
def edit_member(username):
    """Admins keep the family directory correct, including details members never set."""
    target = storage.get_user(username)
    if not target:
        abort(404)
    key = target.get("username") or username
    me = current_member()
    back = redirect(url_for("dashboard", _anchor="members"))

    name = safe_text(request.form.get("name"), 80)
    email = safe_text(request.form.get("email"), 120)
    if not name:
        flash("The member's name cannot be empty.", "danger")
        return back
    if email and (problem := email_problem(email)):
        flash(problem, "danger")
        return back
    taken = storage.find_user_by_email(email)
    if email and taken and taken[0].lower() != key.lower():
        flash("That email is already used by another family member.", "danger")
        return back

    upload = request.files.get("avatar")
    picture = read_image_upload(upload)
    if picture is None and upload and upload.filename:
        flash("Profile photos must be a PNG, JPG, GIF or WEBP file, 5 MB or smaller.", "danger")
        return back

    storage.update_user(
        key,
        name=name,
        email=email,
        phone=safe_text(request.form.get("phone"), 32),
        relationship=safe_text(request.form.get("relationship"), 60),
        location=safe_text(request.form.get("location"), 80),
        bio=safe_text(request.form.get("bio"), 600),
    )
    if picture:
        storage.set_avatar(key, *picture)
    if key.lower() == me["username"].lower():
        session["user"]["name"] = name
    else:
        storage.notify(
            "account", actor=me["name"], title="Your profile was updated",
            body=f"{me['name']} refreshed your details in the family directory.",
            link=url_for("dashboard", _anchor="profile"), usernames=[key],
        )
    log_activity("edited", "member", name)
    flash(f"{name}'s details have been saved.", "success")
    return back


@app.route("/members/<username>/role", methods=["POST"])
@admin_required
def change_member_role(username):
    role = request.form.get("role")
    target = storage.get_user(username)
    if not target or role not in (Config.ROLE_ADMIN, Config.ROLE_MEMBER):
        abort(404)
    back = redirect(url_for("dashboard", _anchor="members"))

    if username == current_member()["username"]:
        flash("You cannot change your own role.", "danger")
        return back

    storage.update_user(username, role=role)
    storage.notify(
        "account", actor=current_member()["name"], title="Your role has changed",
        body=f"You are now {'an administrator' if role == Config.ROLE_ADMIN else 'a member'} "
             "of the family website.",
        link=url_for("dashboard"), usernames=[username],
    )
    log_activity("edited", "member", target["name"],
                 detail="role → admin" if role == Config.ROLE_ADMIN else "role → member")
    flash(f"{target['name']} is now {'an admin' if role == Config.ROLE_ADMIN else 'a member'}.", "success")
    return back


@app.route("/members/<username>/toggle", methods=["POST"])
@admin_required
def toggle_member_active(username):
    target = storage.get_user(username)
    if not target:
        abort(404)
    back = redirect(url_for("dashboard", _anchor="members"))

    if username == current_member()["username"]:
        flash("You cannot deactivate your own account.", "danger")
        return back

    now_active = not target.get("is_active", True)
    storage.update_user(username, is_active=now_active)
    if now_active:
        storage.notify(
            "account", actor=current_member()["name"], title="Your account is active again",
            body="You can sign in and reach the family album once more.",
            link=url_for("dashboard"), usernames=[username],
        )
    log_activity("edited", "member", target["name"],
                 detail="reactivated" if now_active else "deactivated")
    flash(f"{target['name']} has been {'reactivated' if now_active else 'deactivated'}.", "info")
    return back


@app.route("/members/<username>/photo")
def member_photo(username):
    """Serve the photo stored in the database for one member.

    Public, because the family directory on the landing page is public. The bytes
    travel with the database, so a redeploy on a host with an empty disk cannot
    lose anyone's face.
    """
    picture = storage.get_avatar(username)
    if not picture:
        abort(404)
    data, content_type, _modified = picture
    response = Response(data, content_type=content_type)
    # The URL carries the upload time as ?v=, so this response never needs rechecking.
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route("/members/<username>/password", methods=["POST"])
@admin_required
def reset_member_password(username):
    """Administrators hand out a new passphrase when a member is locked out."""
    target = storage.get_user(username)
    if not target:
        abort(404)
    back = redirect(url_for("dashboard", _anchor="members"))

    password = request.form.get("password", "")
    if (problem := password_problem(password)):
        flash(problem, "danger")
        return back

    storage.set_password(username, password)
    storage.notify(
        "account", actor=current_member()["name"], title="Your password was reset",
        body=f"{current_member()['name']} set a new password for your account. Sign in with it, "
             "then choose your own under My Profile.",
        link=url_for("my_space"), usernames=[username],
    )
    log_activity("reset", "member", target["name"], detail=f"password for '{username}'")
    flash(f"A new password is set for {target['name']}. Pass it on directly — it was not emailed.",
          "success")
    return back


@app.route("/messages/<record_id>/delete", methods=["POST"])
@admin_required
def delete_message(record_id):
    message = storage.remove_message(record_id)
    log_activity("deleted", "message", (message or {}).get("name", "Contact message"))
    flash("Message deleted.", "info")
    return redirect(url_for("dashboard", _anchor="messages"))


# ============================================
# Errors
# ============================================

_JSON_PREFIXES = ("/posts", "/uploads", "/announcements", "/events", "/members", "/messages")


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
@app.errorhandler(500)
def handle_error(error):
    code = getattr(error, "code", 500)
    if code == 413:
        return jsonify({"error": "That file is too large. The maximum size is 100 MB."}), 413

    messages = {
        400: ("Bad Request", getattr(error, "description", "That request could not be processed.")),
        403: ("Access Denied", "You do not have permission to view this page."),
        404: ("Page Not Found", "We could not find the page you were looking for."),
        500: ("Something Went Wrong", "An unexpected error occurred. Please try again."),
    }
    title, detail = messages.get(code, messages[500])

    if request.path.startswith(_JSON_PREFIXES):
        return jsonify({"error": detail}), code
    return render_template("error.html", code=code, title=title, detail=detail), code


# create_all() only adds tables that are missing, so it is safe on every boot.
# It never alters an existing table: that is what ``flask db migrate`` is for.
with app.app_context():
    db.create_all()
    add_missing_columns()
    storage.seed_defaults()


@app.cli.command("import-json")
@click.option("--data-dir", default=None, metavar="DIR",
              help="Folder holding the legacy *.json files (default: the app folder).")
def import_json(data_dir):
    """Copy the old JSON store into the database. Safe to run twice."""
    totals = storage.import_legacy_json(data_dir)
    if not totals:
        click.echo(f"No legacy *.json files found in {data_dir or Config.DATA_DIR}.")
        return
    for name, (added, skipped) in sorted(totals.items()):
        click.echo(f"{name:16} {added:4} added, {skipped:4} already in the database")


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))