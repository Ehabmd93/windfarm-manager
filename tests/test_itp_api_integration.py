"""
Flask test-client integration tests for ITP API security.

Routes under test:
  POST /api/itp/client/<token>/item/<item_no>/<ci>   (api_client_review_item)
  POST /api/itp/<record_id>/criterion/<item_no>/<ci>/notes  (api_itp_criterion_notes_post)

Scenarios covered:
  1.  Valid accept submission succeeds (200 OK)
  2.  Valid reject (concern) submission succeeds
  3.  Expired invite token → 403
  4.  Superseded invite token → 403
  5.  Logged-out user → 401
  6.  Mismatched authenticated user → 403
  7.  Legacy shared client_token for a project-scoped ITP → 403
  8.  Legacy shared client_token for a NON-project legacy ITP → allowed (400/404 for item)
  9.  Completed review cycle blocks modification → 400
 10. Signed invite blocks further per-item changes → 400
 11. Cross-project invite token for criterion notes → 403
 12. Blank sender company notifies only the sender
 13. Valid criterion-notes POST succeeds
 14. Criterion-notes POST with expired token → 403
 15. Criterion-notes POST without CSRF → 403

Run with:
    cd "C:\\Users\\ehaby\\Desktop\\Windfarm Manger\\windfarm-manager"
    python -m pytest tests/test_itp_api_integration.py -v
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# app, db, and client fixtures are provided by tests/conftest.py


# ─── Session helpers ──────────────────────────────────────────────────────────

CSRF = "test-csrf-token"


def _inject_session(test_client, user_id, csrf=CSRF):
    """Inject Flask-Login identity + CSRF token directly into the test session."""
    with test_client.session_transaction() as sess:
        sess["_user_id"]    = str(user_id)
        sess["_fresh"]      = True
        sess["_csrf_token"] = csrf


def _logout(test_client):
    """Clear user identity from the session while preserving the CSRF token.

    A real logged-out browser still holds a session cookie with a valid CSRF
    token.  If we wipe the token too, CSRF-protected routes reject the request
    *before* the auth check runs, producing a misleading 403 instead of 401.
    """
    with test_client.session_transaction() as sess:
        sess.pop("_user_id", None)
        sess.pop("_fresh",   None)
        if "_csrf_token" not in sess:
            sess["_csrf_token"] = CSRF


# ─── Model builders ───────────────────────────────────────────────────────────

def _uid():
    return uuid.uuid4().hex[:8]


def _make_user(db, company="AcmeCorp"):
    from models import User
    uid = _uid()
    u = User(
        name     = f"User-{uid}",
        email    = f"{uid}@test.com",
        company  = company,
        role     = "client",
        is_active= True,
        password = generate_password_hash("pw"),
    )
    db.session.add(u)
    db.session.flush()
    return u


def _make_project(db):
    from models import Project
    p = Project(name=f"Proj-{_uid()}")
    db.session.add(p)
    db.session.flush()
    return p


def _make_wtg(db, project_id=None):
    from models import WTG
    w = WTG(name=f"WTG-{_uid()}", project_id=project_id)
    db.session.add(w)
    db.session.flush()
    return w


def _make_itp_record(db, wtg, proj_template_id=None, client_token=None):
    from models import ITPRecord
    r = ITPRecord(
        wtg_id                  = wtg.id,
        itp_type                = "TEST_ITP",
        status                  = "in_progress",
        project_itp_template_id = proj_template_id,
        client_token            = client_token,
    )
    db.session.add(r)
    db.session.flush()
    return r


def _make_item_status(db, record, item_no="1", ci=0, signed=True):
    from models import ITPItemStatus
    s = ITPItemStatus(
        itp_record_id   = record.id,
        item_no         = str(item_no),
        criterion_index = ci,
        lucas_complete  = signed,
        lucas_signed_at = datetime.now(timezone.utc) if signed else None,
    )
    db.session.add(s)
    db.session.flush()
    return s


def _make_member_ac(db, project, user, invite_status="accepted"):
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


def _make_invite(db, record, member, user,
                 status="pending_review", expires_delta=timedelta(days=30),
                 invited_by_id=None, invited_by_company="AcmeCorp",
                 review_cycle_id=None):
    from models import ITPClientInvite
    expires_at = None
    if expires_delta is not None:
        expires_at = datetime.now(timezone.utc) + expires_delta
    inv = ITPClientInvite(
        record_id            = record.id,
        project_member_ac_id = member.id,
        user_id              = user.id,
        token                = f"tok-{_uid()}",
        name                 = user.name,
        email                = user.email,
        company              = user.company,
        status               = status,
        is_revoked           = False,
        expires_at           = expires_at,
        invited_by_id        = invited_by_id,
        invited_by_company   = invited_by_company,
        review_cycle_id      = review_cycle_id,
    )
    db.session.add(inv)
    db.session.flush()
    return inv


def _make_review_cycle(db, record, status="open"):
    from models import ITPReviewCycle
    c = ITPReviewCycle(
        record_id    = record.id,
        cycle_number = 1,
        revision     = 0,
        status       = status,
    )
    db.session.add(c)
    db.session.flush()
    return c


def _post_review(client, token, item_no, ci, action="accept",
                 signature="data:image/png;base64,ABC", comment="bad"):
    """POST to api_client_review_item (no CSRF needed)."""
    body = {"action": action}
    if action in ("accept", "approved"):
        body["signature"] = signature
    else:
        body["comment"] = comment
    resp = client.post(
        f"/api/itp/client/{token}/item/{item_no}/{ci}",
        json=body,
        content_type="application/json",
    )
    return resp, resp.get_json(silent=True) or {}


def _post_note(client, record_id, item_no, ci, token,
               note_text="Test note", party="client",
               csrf=CSRF, extra_headers=None):
    """POST to api_itp_criterion_notes_post (CSRF required)."""
    headers = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    resp = client.post(
        f"/api/itp/{record_id}/criterion/{item_no}/{ci}/notes",
        json={"note_text": note_text, "party": party, "invite_token": token},
        headers=headers,
    )
    return resp, resp.get_json(silent=True) or {}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. api_client_review_item — accept + concern
# ═══════════════════════════════════════════════════════════════════════════════

class TestClientReviewItemAuth:

    def _setup(self, db, company="AcmeCorp", invited_by_company="AcmeCorp"):
        """Return (project, wtg, record, item_status, member, invite, user)."""
        project = _make_project(db)
        wtg     = _make_wtg(db, project_id=project.id)
        record  = _make_itp_record(db, wtg)
        user    = _make_user(db, company=company)
        member  = _make_member_ac(db, project, user)
        sender  = _make_user(db, company=invited_by_company)
        item    = _make_item_status(db, record)
        invite  = _make_invite(db, record, member, user,
                               invited_by_id      = sender.id,
                               invited_by_company = invited_by_company)
        db.session.commit()
        return project, wtg, record, item, member, invite, user, sender

    def test_valid_accept(self, app, client, db):
        """Authenticated user with valid invite can approve an item."""
        _, _, _, item, _, invite, user, _ = self._setup(db)
        _inject_session(client, user.id)
        resp, data = _post_review(client, invite.token, item.item_no, item.criterion_index)
        assert resp.status_code == 200, data
        assert data.get("ok") is True
        assert data.get("action") == "approved"

    def test_valid_concern(self, app, client, db):
        """Authenticated user with valid invite can raise a concern."""
        _, _, _, item, _, invite, user, _ = self._setup(db)
        _inject_session(client, user.id)
        resp, data = _post_review(client, invite.token, item.item_no, item.criterion_index,
                                  action="rejected", comment="This is wrong.")
        assert resp.status_code == 200, data
        assert data.get("ok") is True
        assert data.get("action") == "rejected"

    def test_expired_token_rejected(self, app, client, db):
        """Expired invite token returns 403 with 'expired' in the error."""
        project = _make_project(db)
        wtg     = _make_wtg(db, project_id=project.id)
        record  = _make_itp_record(db, wtg)
        user    = _make_user(db)
        member  = _make_member_ac(db, project, user)
        item    = _make_item_status(db, record)
        invite  = _make_invite(db, record, member, user,
                               expires_delta=timedelta(hours=-1))   # already expired
        db.session.commit()
        _inject_session(client, user.id)
        resp, data = _post_review(client, invite.token, item.item_no, item.criterion_index)
        assert resp.status_code == 403, data
        assert "expired" in (data.get("error") or "").lower()

    def test_superseded_token_rejected(self, app, client, db):
        """Superseded invite token returns 403."""
        project = _make_project(db)
        wtg     = _make_wtg(db, project_id=project.id)
        record  = _make_itp_record(db, wtg)
        user    = _make_user(db)
        member  = _make_member_ac(db, project, user)
        item    = _make_item_status(db, record)
        invite  = _make_invite(db, record, member, user, status="superseded")
        db.session.commit()
        _inject_session(client, user.id)
        resp, data = _post_review(client, invite.token, item.item_no, item.criterion_index)
        assert resp.status_code == 403, data
        assert "superseded" in (data.get("error") or "").lower()

    def test_logged_out_user_rejected(self, app, client, db):
        """Unauthenticated request returns 401."""
        project = _make_project(db)
        wtg     = _make_wtg(db, project_id=project.id)
        record  = _make_itp_record(db, wtg)
        user    = _make_user(db)
        member  = _make_member_ac(db, project, user)
        item    = _make_item_status(db, record)
        invite  = _make_invite(db, record, member, user)
        db.session.commit()
        _logout(client)   # ensure no session
        resp, data = _post_review(client, invite.token, item.item_no, item.criterion_index)
        assert resp.status_code == 401, data

    def test_wrong_user_rejected(self, app, client, db):
        """A user whose identity doesn't match the invite receives 403."""
        project  = _make_project(db)
        wtg      = _make_wtg(db, project_id=project.id)
        record   = _make_itp_record(db, wtg)
        owner    = _make_user(db)
        intruder = _make_user(db, company="OtherCo")
        member   = _make_member_ac(db, project, owner)
        _make_member_ac(db, project, intruder)
        item     = _make_item_status(db, record)
        invite   = _make_invite(db, record, member, owner)
        db.session.commit()
        _inject_session(client, intruder.id)   # authenticated as the wrong user
        resp, data = _post_review(client, invite.token, item.item_no, item.criterion_index)
        assert resp.status_code == 403, data
        assert "does not match" in (data.get("error") or "").lower()

    def test_legacy_token_for_project_record_rejected(self, app, client, db):
        """A legacy shared client_token on a project-scoped ITP returns 403."""
        project     = _make_project(db)
        wtg         = _make_wtg(db, project_id=project.id)  # has project_id
        legacy_tok  = f"legacy-{_uid()}"
        record      = _make_itp_record(db, wtg, client_token=legacy_tok)
        user        = _make_user(db)
        db.session.commit()
        _inject_session(client, user.id)
        resp, data = _post_review(client, legacy_tok, "1", 0)
        assert resp.status_code == 403, data
        assert "per-invite token" in (data.get("error") or "").lower()

    def test_legacy_token_for_non_project_record_allowed_through_to_item_check(
            self, app, client, db):
        """Legacy shared token on a non-project ITP passes auth; blocked
        at the item-not-found stage since we don't create an ITPItemStatus here."""
        legacy_tok = f"legacy-{_uid()}"
        wtg    = _make_wtg(db, project_id=None)   # no project → legacy path allowed
        record = _make_itp_record(db, wtg, client_token=legacy_tok)
        user   = _make_user(db)
        db.session.commit()
        _inject_session(client, user.id)
        # No ITPItemStatus created → expects 404 (item not found), not 403
        resp, data = _post_review(client, legacy_tok, "1", 0)
        # Auth passed; route blocked at "Item not found" (404)
        assert resp.status_code == 404, data

    def test_completed_cycle_blocks_modification(self, app, client, db):
        """A review cycle with status='completed' prevents further per-item changes."""
        project = _make_project(db)
        wtg     = _make_wtg(db, project_id=project.id)
        record  = _make_itp_record(db, wtg)
        user    = _make_user(db)
        member  = _make_member_ac(db, project, user)
        item    = _make_item_status(db, record)
        cycle   = _make_review_cycle(db, record, status="completed")
        invite  = _make_invite(db, record, member, user, review_cycle_id=cycle.id)
        db.session.commit()
        _inject_session(client, user.id)
        resp, data = _post_review(client, invite.token, item.item_no, item.criterion_index)
        assert resp.status_code == 400, data
        assert "completed" in (data.get("error") or "").lower()

    def test_signed_invite_blocks_modification(self, app, client, db):
        """An invite with status='signed' (final submission done) blocks further changes."""
        project = _make_project(db)
        wtg     = _make_wtg(db, project_id=project.id)
        record  = _make_itp_record(db, wtg)
        user    = _make_user(db)
        member  = _make_member_ac(db, project, user)
        item    = _make_item_status(db, record)
        invite  = _make_invite(db, record, member, user, status="signed")
        db.session.commit()
        _inject_session(client, user.id)
        resp, data = _post_review(client, invite.token, item.item_no, item.criterion_index)
        assert resp.status_code == 400, data
        assert "submitted" in (data.get("error") or "").lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. api_itp_criterion_notes_post — integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestCriterionNotesPostIntegration:

    def _setup(self, db):
        project = _make_project(db)
        wtg     = _make_wtg(db, project_id=project.id)
        record  = _make_itp_record(db, wtg)
        user    = _make_user(db)
        member  = _make_member_ac(db, project, user)
        item    = _make_item_status(db, record)
        invite  = _make_invite(db, record, member, user)
        db.session.commit()
        return project, wtg, record, item, member, invite, user

    def test_valid_note_succeeds(self, app, client, db):
        """Authenticated user with valid invite can post a client note."""
        _, _, record, item, _, invite, user = self._setup(db)
        _inject_session(client, user.id)
        resp, data = _post_note(
            client, record.id, item.item_no, item.criterion_index, invite.token)
        assert resp.status_code == 200, data
        assert data.get("ok") is True
        assert data["note"]["note_text"] == "Test note"

    def test_expired_token_rejected(self, app, client, db):
        """Expired token returns 403."""
        project = _make_project(db)
        wtg     = _make_wtg(db, project_id=project.id)
        record  = _make_itp_record(db, wtg)
        user    = _make_user(db)
        member  = _make_member_ac(db, project, user)
        item    = _make_item_status(db, record)
        invite  = _make_invite(db, record, member, user,
                               expires_delta=timedelta(hours=-2))
        db.session.commit()
        _inject_session(client, user.id)
        resp, data = _post_note(
            client, record.id, item.item_no, item.criterion_index, invite.token)
        assert resp.status_code == 403, data
        assert "expired" in (data.get("error") or "").lower()

    def test_superseded_token_rejected(self, app, client, db):
        """Superseded token returns 403."""
        project = _make_project(db)
        wtg     = _make_wtg(db, project_id=project.id)
        record  = _make_itp_record(db, wtg)
        user    = _make_user(db)
        member  = _make_member_ac(db, project, user)
        item    = _make_item_status(db, record)
        invite  = _make_invite(db, record, member, user, status="superseded")
        db.session.commit()
        _inject_session(client, user.id)
        resp, data = _post_note(
            client, record.id, item.item_no, item.criterion_index, invite.token)
        assert resp.status_code == 403, data
        assert "superseded" in (data.get("error") or "").lower()

    def test_logged_out_user_rejected(self, app, client, db):
        """Unauthenticated request returns 401."""
        _, _, record, item, _, invite, _ = self._setup(db)
        _logout(client)
        resp, data = _post_note(
            client, record.id, item.item_no, item.criterion_index, invite.token)
        assert resp.status_code == 401, data

    def test_mismatched_user_rejected(self, app, client, db):
        """User authenticated as someone other than the invite owner returns 403."""
        project  = _make_project(db)
        wtg      = _make_wtg(db, project_id=project.id)
        record   = _make_itp_record(db, wtg)
        owner    = _make_user(db)
        intruder = _make_user(db, company="EvilCo")
        member   = _make_member_ac(db, project, owner)
        _make_member_ac(db, project, intruder)
        item     = _make_item_status(db, record)
        invite   = _make_invite(db, record, member, owner)
        db.session.commit()
        _inject_session(client, intruder.id)
        resp, data = _post_note(
            client, record.id, item.item_no, item.criterion_index, invite.token)
        assert resp.status_code == 403, data
        assert "does not match" in (data.get("error") or "").lower()

    def test_cross_project_token_rejected(self, app, client, db):
        """Using an invite token from project A to post a note on project B's
        record returns 403."""
        proj_a = _make_project(db)
        proj_b = _make_project(db)
        wtg_a  = _make_wtg(db, project_id=proj_a.id)
        wtg_b  = _make_wtg(db, project_id=proj_b.id)
        rec_a  = _make_itp_record(db, wtg_a)
        rec_b  = _make_itp_record(db, wtg_b)
        user   = _make_user(db)
        mem_a  = _make_member_ac(db, proj_a, user)
        _make_item_status(db, rec_b)
        invite = _make_invite(db, rec_a, mem_a, user)  # invite for record A
        db.session.commit()
        _inject_session(client, user.id)
        # Try to post a note on record B using record A's token
        resp, data = _post_note(
            client, rec_b.id, "1", 0, invite.token)
        assert resp.status_code == 403, data

    def test_missing_csrf_rejected(self, app, client, db):
        """Request without X-CSRF-Token header returns 403."""
        _, _, record, item, _, invite, user = self._setup(db)
        _inject_session(client, user.id, csrf="different-csrf")
        # send wrong/missing csrf header
        resp = client.post(
            f"/api/itp/{record.id}/criterion/{item.item_no}/{item.criterion_index}/notes",
            json={"note_text": "hi", "party": "client", "invite_token": invite.token},
            headers={"X-CSRF-Token": "WRONG", "Content-Type": "application/json"},
        )
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Notification blank-company guard — integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestNotificationBlankCompanyIntegration:

    def test_blank_company_concern_notifies_sender_only(self, app, client, db):
        """When invited_by_company is blank, only the sender is notified
        after api_client_review_item processes a concern action."""
        from models import Notification

        project   = _make_project(db)
        wtg       = _make_wtg(db, project_id=project.id)
        record    = _make_itp_record(db, wtg)
        sender    = _make_user(db, company="")     # blank company
        reviewer  = _make_user(db, company="")
        bystander = _make_user(db, company="AnyCompany")
        member    = _make_member_ac(db, project, reviewer)
        _make_member_ac(db, project, bystander)
        item      = _make_item_status(db, record)
        invite    = _make_invite(
            db, record, member, reviewer,
            invited_by_id      = sender.id,
            invited_by_company = "",           # ← blank
        )
        db.session.commit()

        # Capture notification count before
        before = Notification.query.count()

        _inject_session(client, reviewer.id)
        resp, data = _post_review(
            client, invite.token, item.item_no, item.criterion_index,
            action="rejected", comment="Reject reason.")
        assert resp.status_code == 200, data

        notifs = Notification.query.filter(
            Notification.id > before,
            Notification.type == "itp_concern",
        ).all()
        notified_ids = {n.user_id for n in notifs}

        assert sender.id    in notified_ids, "Sender must be notified"
        assert bystander.id not in notified_ids, \
            "Bystander must NOT be notified when invited_by_company is blank"

    def test_named_company_concern_notifies_teammates(self, app, client, db):
        """When invited_by_company is set, same-company teammates receive
        notifications; different-company members do not."""
        from models import Notification

        project  = _make_project(db)
        wtg      = _make_wtg(db, project_id=project.id)
        record   = _make_itp_record(db, wtg)
        sender   = _make_user(db, company="Alpha")
        reviewer = _make_user(db, company="Alpha")
        teammate = _make_user(db, company="Alpha")
        outsider = _make_user(db, company="Beta")
        member   = _make_member_ac(db, project, reviewer)
        _make_member_ac(db, project, teammate)
        _make_member_ac(db, project, outsider)
        item     = _make_item_status(db, record)
        invite   = _make_invite(
            db, record, member, reviewer,
            invited_by_id      = sender.id,
            invited_by_company = "Alpha",
        )
        db.session.commit()

        before = Notification.query.count()

        _inject_session(client, reviewer.id)
        resp, data = _post_review(
            client, invite.token, item.item_no, item.criterion_index,
            action="rejected", comment="Needs rework.")
        assert resp.status_code == 200, data

        notifs = Notification.query.filter(
            Notification.id > before,
            Notification.type == "itp_concern",
        ).all()
        notified_ids = {n.user_id for n in notifs}

        assert sender.id   in notified_ids,      "Sender not notified"
        assert teammate.id in notified_ids,      "Same-company teammate not notified"
        assert outsider.id not in notified_ids,  "Different-company outsider was notified"
