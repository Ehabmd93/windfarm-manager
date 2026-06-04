"""
Shared pytest fixtures for the windfarm-manager test suite.

The app fixture uses SQLAlchemy's StaticPool so that every database
connection — whether from the test setup code or from an HTTP request
processed by the Flask test client — uses the same in-memory SQLite
database.  Without StaticPool, :memory: creates a fresh (empty) database
per connection, which causes HTTP-request handlers to see none of the
objects created by the test setup code.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy.pool import StaticPool


@pytest.fixture(scope="session")
def app():
    os.environ.setdefault("DATABASE_URL",  "sqlite://")
    os.environ.setdefault("SECRET_KEY",    "test-secret-key")
    os.environ.setdefault("FLASK_TESTING", "1")

    from app import app as flask_app, db as _db

    flask_app.config.update(
        TESTING                  = True,
        WTF_CSRF_ENABLED         = False,
        SQLALCHEMY_DATABASE_URI  = "sqlite://",          # shared in-memory
        SQLALCHEMY_ENGINE_OPTIONS = {                    # single connection
            "connect_args": {"check_same_thread": False},
            "poolclass":    StaticPool,
        },
    )
    with flask_app.app_context():
        _db.create_all()

        # Flask-Login caches the current user on g._login_user, which is tied to
        # the application context.  Because this fixture keeps ONE app context alive
        # for the entire test session, g is never reset between test-client requests —
        # every request after the first would return the same cached user.
        # Clearing the cache at the start of each request forces Flask-Login to
        # reload the user from the session cookie, giving each request the identity
        # that was injected via _inject_session (or AnonymousUser if none was).
        @flask_app.before_request
        def _test_clear_login_user_cache():
            from flask import g
            if hasattr(g, "_login_user"):
                delattr(g, "_login_user")

        yield flask_app
        _db.session.remove()


@pytest.fixture()
def db(app):
    from app import db as _db
    return _db


@pytest.fixture()
def client(app):
    return app.test_client()
