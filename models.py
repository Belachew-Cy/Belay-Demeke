"""Database models for the Belay Demeke Family website."""

from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import deferred

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    password = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default="member")
    email = db.Column(db.String(120))
    phone = db.Column(db.String(32))
    relationship = db.Column(db.String(60))
    location = db.Column(db.String(80))
    bio = db.Column(db.Text)
    # Display URL only: a pasted link, or /members/<username>/photo once a real
    # file is stored below. Every template reads this column and never the bytes.
    avatar = db.Column(db.String(256))
    # Profile photos live in the database so a redeploy cannot lose them.
    # deferred() keeps the bytes out of every directory query.
    avatar_image = deferred(db.Column(db.LargeBinary))
    avatar_type = db.Column(db.String(40))
    avatar_updated_at = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    notify = db.Column(db.JSON, default=dict)


class Post(db.Model):
    __tablename__ = "posts"
    id = db.Column(db.String(32), primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    filename = db.Column(db.String(256))
    original = db.Column(db.String(256))
    url = db.Column(db.String(256))
    kind = db.Column(db.String(20))
    type = db.Column(db.String(20))
    visibility = db.Column(db.String(20), default="family")
    uploaded_by = db.Column(db.String(80))
    # Owned by the account that shared it; ON DELETE SET NULL keeps the media
    # if that account is ever removed.
    uploaded_by_username = db.Column(
        db.String(64), db.ForeignKey("users.username", ondelete="SET NULL"))
    date = db.Column(db.String(40))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Message(db.Model):
    __tablename__ = "messages"
    id = db.Column(db.String(32), primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    message = db.Column(db.Text, nullable=False)
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Announcement(db.Model):
    __tablename__ = "announcements"
    id = db.Column(db.String(32), primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    body = db.Column(db.Text, nullable=False)
    author = db.Column(db.String(80))
    pinned = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Event(db.Model):
    __tablename__ = "events"
    id = db.Column(db.String(32), primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    date = db.Column(db.String(20))
    time = db.Column(db.String(10))
    location = db.Column(db.String(120))
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class JoinRequest(db.Model):
    __tablename__ = "join_requests"
    id = db.Column(db.String(32), primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(32))
    relationship = db.Column(db.String(60))
    location = db.Column(db.String(80))
    note = db.Column(db.Text)
    status = db.Column(db.String(20), default="pending")
    resolved_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.String(32), primary_key=True)
    # Recipients are addressed by their lower-cased sign-in name and each note
    # has to outlive the account it was sent to, so this is text plus an index
    # rather than a foreign key.
    username = db.Column(db.String(64), index=True)
    kind = db.Column(db.String(30))
    title = db.Column(db.String(200))
    body = db.Column(db.Text)
    link = db.Column(db.String(256))
    actor = db.Column(db.String(80))
    read_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Activity(db.Model):
    __tablename__ = "activity_log"
    id = db.Column(db.String(32), primary_key=True)
    action = db.Column(db.String(40))
    target_type = db.Column(db.String(40))
    target = db.Column(db.String(200))
    actor = db.Column(db.String(80))
    detail = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)