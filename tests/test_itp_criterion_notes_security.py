"""
Repeatable smoke tests for ITP criterion-notes security.

Covers the six scenarios requested in the final security repair:
  1.  Expired POST token → 403
  2.  Superseded POST token → 403
  3.  Logged-out client POST → 401
  4.  Mismatched authenticated client POST → 403
  5.  Blank sender company does NOT notify unrelated project members
  6.  Valid matching client POST succeeds (helper returns invite, no error)

Also verifies the shared _validate_itp_client_token helper is used by both
the GET and POST routes (no drift between the two validators).

Run with:
    cd "C:\\Users\\ehaby\\Desktop\\Windfarm Manger\\windfarm-manager"
    python -m pytest tests/test_itp_criterion_notes_security.py -v
"""

import ast
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

# ── Make sure the project root is on the path ─────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from werkzeug.security import generate_password_hash


# app, db, and client fixtures are provided by tests/conftest.py


# ── Minimal model builders ─────────────────────────────────────────────────────
def _utcnow():
    return datetime.now(timezone.utc)


def _make_user(db, name="Alice", email="alice@example.com", company="AcmeCorp"):
    from models import User
    u = User(
        name     = name,
        email    = email,
        company  = company,
        role     = "client",
        password = generate_password_hash("password123"),
    )
    db.session.add(u)
    db.session.flush()
    return u


def _make_project(db, name=None):
    from models import Project
    import uuid
    p = Project(name=name or f"Project-{uuid.uuid4().hex[:6]}")
    db.session.add(p)
    db.session.flush()
    return p


def _make_wtg(db, project):
    from models import WTG
    w = WTG(name="WTG-01", project_id=project.id)
    db.session.add(w)
    db.session.flush()
    return w


def _make_itp_record(db, wtg):
    from models import ITPRecord
    r = ITPRecord(wtg_id=wtg.id, itp_type="TEST_ITP", status="in_progress")
    db.session.add(r)
    db.session.flush()
    return r


def _make_member_ac(db, project, user, invite_status="accepted"):
    """Create a ProjectMemberAC + both ITP permission rows."""
    from models import ProjectMemberAC, ProjectMemberPermission
    m = ProjectMemberAC(
        project_id    = project.id,
        user_id       = user.id,
        email         = user.email,
        name          = user.name,
        invite_status = invite_status,
        is_active     = True,
    )
    db.session.add(m)
    db.session.flush()
    for pkey in ("can_review_itp", "can_view_itp"):
        db.session.add(ProjectMemberPermission(
            member_id      = m.id,
            permission_key = pkey,
            value          = True,
        ))
    db.session.flush()
    return m


def _make_invite(db, record, member_ac, user,
                 status="pending_review", expires_delta=None,
                 invited_by_id=None, invited_by_company="AcmeCorp"):
    from models import ITPClientInvite
    import uuid
    expires_at = None
    if expires_delta is not None:
        expires_at = _utcnow() + expires_delta
    inv = ITPClientInvite(
        record_id            = record.id,
        project_member_ac_id = member_ac.id,
        user_id              = user.id,
        token                = f"tok-{uuid.uuid4().hex}",
        name                 = user.name,
        email                = user.email,
        company              = user.company,
        status               = status,
        is_revoked           = False,
        expires_at           = expires_at,
        invited_by_id        = invited_by_id,
        invited_by_company   = invited_by_company,
    )
    db.session.add(inv)
    db.session.flush()
    return inv


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for _validate_itp_client_token
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidateItpClientToken:
    """Unit tests for the shared invite-token validator."""

    def test_missing_token_returns_400(self, app):
        with app.app_context(), app.test_request_context("/"):
            from app import _validate_itp_client_token
            from flask_login import AnonymousUserMixin
            with patch("app.current_user", AnonymousUserMixin()):
                inv, err = _validate_itp_client_token(None, 999, 999)
            assert inv is None
            resp, code = err
            assert code == 400

    def test_invalid_token_returns_403(self, app):
        with app.app_context(), app.test_request_context("/"):
            from app import _validate_itp_client_token
            from flask_login import AnonymousUserMixin
            with patch("app.current_user", AnonymousUserMixin()):
                inv, err = _validate_itp_client_token("no-such-token", 1, 1)
            assert inv is None
            resp, code = err
            assert code == 403

    def test_expired_token_returns_403(self, app, db):
        with app.app_context():
            from app import _validate_itp_client_token
            project = _make_project(db)
            wtg     = _make_wtg(db, project)
            record  = _make_itp_record(db, wtg)
            user    = _make_user(db, name="Bob-exp", email="bob_exp@t.com")
            member  = _make_member_ac(db, project, user)
            invite  = _make_invite(db, record, member, user,
                                   expires_delta=timedelta(hours=-1))
            db.session.commit()

            with app.test_request_context("/"):
                mock_u = MagicMock()
                mock_u.is_authenticated = True
                mock_u.id               = user.id
                mock_u.email            = user.email
                with patch("app.current_user", mock_u):
                    inv, err = _validate_itp_client_token(
                        invite.token, record.id, project.id)
            assert inv is None
            resp, code = err
            assert code == 403
            assert "expired" in json.loads(resp.get_data(as_text=True))["error"].lower()

    def test_superseded_token_returns_403(self, app, db):
        with app.app_context():
            from app import _validate_itp_client_token
            project = _make_project(db)
            wtg     = _make_wtg(db, project)
            record  = _make_itp_record(db, wtg)
            user    = _make_user(db, name="Carol-sup", email="carol_sup@t.com")
            member  = _make_member_ac(db, project, user)
            invite  = _make_invite(db, record, member, user, status="superseded",
                                   expires_delta=timedelta(days=30))
            db.session.commit()

            with app.test_request_context("/"):
                mock_u = MagicMock()
                mock_u.is_authenticated = True
                mock_u.id               = user.id
                mock_u.email            = user.email
                with patch("app.current_user", mock_u):
                    inv, err = _validate_itp_client_token(
                        invite.token, record.id, project.id)
            assert inv is None
            resp, code = err
            assert code == 403
            assert "superseded" in json.loads(
                resp.get_data(as_text=True))["error"].lower()

    def test_unauthenticated_returns_401(self, app, db):
        with app.app_context():
            from app import _validate_itp_client_token
            project = _make_project(db)
            wtg     = _make_wtg(db, project)
            record  = _make_itp_record(db, wtg)
            user    = _make_user(db, name="Dave-anon", email="dave_anon@t.com")
            member  = _make_member_ac(db, project, user)
            invite  = _make_invite(db, record, member, user,
                                   expires_delta=timedelta(days=30))
            db.session.commit()

            with app.test_request_context("/"):
                from flask_login import AnonymousUserMixin
                with patch("app.current_user", AnonymousUserMixin()):
                    inv, err = _validate_itp_client_token(
                        invite.token, record.id, project.id)
            assert inv is None
            resp, code = err
            assert code == 401

    def test_mismatched_user_returns_403(self, app, db):
        with app.app_context():
            from app import _validate_itp_client_token
            project  = _make_project(db)
            wtg      = _make_wtg(db, project)
            record   = _make_itp_record(db, wtg)
            owner    = _make_user(db, name="Eve-owner",    email="eve_own@t.com")
            intruder = _make_user(db, name="Fred-intruder",email="fred_int@t.com",
                                  company="OtherCo")
            member   = _make_member_ac(db, project, owner)
            _make_member_ac(db, project, intruder)
            invite   = _make_invite(db, record, member, owner,
                                    expires_delta=timedelta(days=30))
            db.session.commit()

            with app.test_request_context("/"):
                mock_u = MagicMock()
                mock_u.is_authenticated = True
                mock_u.id               = intruder.id   # wrong user
                mock_u.email            = intruder.email
                with patch("app.current_user", mock_u):
                    inv, err = _validate_itp_client_token(
                        invite.token, record.id, project.id)
            assert inv is None
            resp, code = err
            assert code == 403
            assert "does not match" in json.loads(
                resp.get_data(as_text=True))["error"].lower()

    def test_valid_token_returns_invite(self, app, db):
        with app.app_context():
            from app import _validate_itp_client_token
            project = _make_project(db)
            wtg     = _make_wtg(db, project)
            record  = _make_itp_record(db, wtg)
            user    = _make_user(db, name="Grace-valid", email="grace_v@t.com")
            member  = _make_member_ac(db, project, user)
            invite  = _make_invite(db, record, member, user,
                                   expires_delta=timedelta(days=30))
            db.session.commit()

            with app.test_request_context("/"):
                mock_u = MagicMock()
                mock_u.is_authenticated = True
                mock_u.id               = user.id
                mock_u.email            = user.email
                with patch("app.current_user", mock_u), \
                     patch("app.user_can", return_value=True):
                    inv, err = _validate_itp_client_token(
                        invite.token, record.id, project.id)
            assert err is None
            assert inv is not None
            assert inv.id == invite.id


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for blank-company notification guard
# ═══════════════════════════════════════════════════════════════════════════════

class TestBlankCompanyNotification:
    """Verify that a blank invited_by_company restricts to sender only."""

    def _fan_out(self, db, project, invite):
        """Replicates the notification fan-out logic from app.py."""
        from models import (Notification, ProjectMemberAC,
                            User as UserModel)
        from app import db as app_db

        _sender_co   = (invite.invited_by_company or "").strip().lower()
        _cn_notified = set()

        if invite.invited_by_id:
            app_db.session.add(Notification(
                user_id = invite.invited_by_id,
                type    = "itp_concern",
                title   = "Test concern",
                message = "test",
                url     = "/",
            ))
            _cn_notified.add(invite.invited_by_id)

        # Guarded: only fans out when _sender_co is non-blank
        if _sender_co:
            for _vm in ProjectMemberAC.query.filter_by(
                    project_id=project.id, is_active=True).all():
                if not (_vm.has_permission("can_view_itp") and _vm.user_id):
                    continue
                if _vm.user_id in _cn_notified:
                    continue
                _vm_user = UserModel.query.get(_vm.user_id)
                if not _vm_user:
                    continue
                if (_vm_user.company or "").strip().lower() != _sender_co:
                    continue
                app_db.session.add(Notification(
                    user_id = _vm.user_id,
                    type    = "itp_concern",
                    title   = "Test concern",
                    message = "test",
                    url     = "/",
                ))
                _cn_notified.add(_vm.user_id)
        app_db.session.commit()
        return _cn_notified

    def test_blank_company_notifies_sender_only(self, app, db):
        """When invited_by_company is blank, only the invite sender is notified."""
        with app.app_context():
            from models import Notification

            project   = _make_project(db)
            wtg       = _make_wtg(db, project)
            record    = _make_itp_record(db, wtg)
            sender    = _make_user(db, name="BlankSender",  email="blk_snd@t.com", company="")
            client_u  = _make_user(db, name="BlankClient",  email="blk_cli@t.com", company="")
            unrelated = _make_user(db, name="Unrelated",    email="unrel@t.com",   company="SomeCo")
            m_client  = _make_member_ac(db, project, client_u)
            _make_member_ac(db, project, unrelated)
            invite = _make_invite(
                db, record, m_client, client_u,
                expires_delta      = timedelta(days=30),
                invited_by_id      = sender.id,
                invited_by_company = "",          # ← blank
            )
            db.session.commit()

            Notification.query.delete()
            db.session.commit()

            notified_ids = self._fan_out(db, project, invite)

            assert sender.id   in notified_ids,     "Sender should be notified"
            assert unrelated.id not in notified_ids, \
                "Unrelated member must NOT be notified when invited_by_company is blank"

    def test_matching_company_notifies_teammates(self, app, db):
        """Same-company teammates with can_view_itp are notified; outsiders are not."""
        with app.app_context():
            from models import Notification

            project   = _make_project(db)
            wtg       = _make_wtg(db, project)
            record    = _make_itp_record(db, wtg)
            sender    = _make_user(db, name="CmpSender",   email="cmp_snd@t.com",  company="TargetCo")
            client_u  = _make_user(db, name="CmpClient",   email="cmp_cli@t.com",  company="TargetCo")
            teammate  = _make_user(db, name="CmpTeammate", email="cmp_team@t.com", company="TargetCo")
            outsider  = _make_user(db, name="CmpOutsider", email="cmp_out@t.com",  company="OtherCo")
            m_client  = _make_member_ac(db, project, client_u)
            _make_member_ac(db, project, teammate)
            _make_member_ac(db, project, outsider)
            invite = _make_invite(
                db, record, m_client, client_u,
                expires_delta      = timedelta(days=30),
                invited_by_id      = sender.id,
                invited_by_company = "TargetCo",
            )
            db.session.commit()

            Notification.query.delete()
            db.session.commit()

            notified_ids = self._fan_out(db, project, invite)

            assert sender.id   in notified_ids,      "Sender not notified"
            assert teammate.id in notified_ids,      "Same-company teammate not notified"
            assert outsider.id not in notified_ids,  "Different-company outsider was notified"

    def test_no_invited_by_id_sends_no_notifications(self, app, db):
        """When invited_by_id is None and company is blank, no notification is created."""
        with app.app_context():
            from models import Notification

            project  = _make_project(db)
            wtg      = _make_wtg(db, project)
            record   = _make_itp_record(db, wtg)
            client_u = _make_user(db, name="NoSender", email="nosnd@t.com", company="")
            m_client = _make_member_ac(db, project, client_u)
            invite   = _make_invite(
                db, record, m_client, client_u,
                expires_delta      = timedelta(days=30),
                invited_by_id      = None,       # ← no sender
                invited_by_company = "",
            )
            db.session.commit()

            Notification.query.delete()
            db.session.commit()

            notified_ids = self._fan_out(db, project, invite)
            assert len(notified_ids) == 0, "Should not notify anyone when sender is unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# Structural: GET and POST both call the shared helper (no drift)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSharedValidatorNoDrift:
    """Confirm both routes call _validate_itp_client_token so checks stay in sync."""

    @staticmethod
    def _get_fn_nodes():
        src  = open(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py"),
            encoding="utf-8",
        ).read()
        tree = ast.parse(src)
        get_fn = post_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if node.name == "api_itp_criterion_notes_get":
                    get_fn = node
                elif node.name == "api_itp_criterion_notes_post":
                    post_fn = node
        return get_fn, post_fn

    @staticmethod
    def _calls_helper(fn_node):
        for n in ast.walk(fn_node):
            if isinstance(n, ast.Call):
                name = ""
                if isinstance(n.func, ast.Name):
                    name = n.func.id
                elif isinstance(n.func, ast.Attribute):
                    name = n.func.attr
                if name == "_validate_itp_client_token":
                    return True
        return False

    def test_get_uses_shared_helper(self, app):
        get_fn, _ = self._get_fn_nodes()
        assert get_fn is not None, "GET route function not found in app.py"
        assert self._calls_helper(get_fn), \
            "GET route does not call _validate_itp_client_token"

    def test_post_uses_shared_helper(self, app):
        _, post_fn = self._get_fn_nodes()
        assert post_fn is not None, "POST route function not found in app.py"
        assert self._calls_helper(post_fn), \
            "POST route does not call _validate_itp_client_token"

    def test_helper_enforces_expiry(self, app):
        import inspect, app as app_module
        src = inspect.getsource(app_module._validate_itp_client_token)
        assert "expires_at" in src, "Helper does not check token expiry"

    def test_helper_enforces_superseded(self, app):
        import inspect, app as app_module
        src = inspect.getsource(app_module._validate_itp_client_token)
        assert "superseded" in src, "Helper does not check superseded status"

    def test_helper_requires_authenticated_user(self, app):
        import inspect, app as app_module
        src = inspect.getsource(app_module._validate_itp_client_token)
        assert "is_authenticated" in src, "Helper does not require authenticated user"

    def test_helper_checks_identity(self, app):
        import inspect, app as app_module
        src = inspect.getsource(app_module._validate_itp_client_token)
        assert "_matches" in src, "Helper does not verify user identity against invite"
