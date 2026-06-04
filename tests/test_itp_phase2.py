"""
Phase 2A/2B integration tests.

Covers:
  A1. Approving a criterion stores client_signed_by_* + client_invite_id
  A2. Raising a concern stores identity fields
  A3. Resetting a criterion clears all identity fields
  B1. get_reviewable_criteria_for_invitee returns empty when all criteria
      are already in an active invite for the same email
  B2. get_reviewable_criteria_for_invitee returns new criteria when a new
      criterion is signed after the existing invite was created
  B3. Already-approved criteria are excluded from scope
  B4. Legacy invite with no item_scope_json is treated as covering all
      signed criteria → nothing new returned until more criteria signed
  B5. item_scope_ids property round-trips correctly
  B6. Invitee with no email has no active-invite filter applied

Run with:
    cd "C:\\Users\\ehaby\\Desktop\\Windfarm Manger\\windfarm-manager"
    python -m pytest tests/test_itp_phase2.py -v
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# app, db, client fixtures provided by conftest.py

CSRF = "test-csrf-token"


# ─── helpers ─────────────────────────────────────────────────────────────────

def _uid():
    return uuid.uuid4().hex[:8]


def _make_user(db, company="AcmeCorp"):
    from models import User
    uid = _uid()
    u = User(
        name      = f"User-{uid}",
        email     = f"{uid}@test.com",
        company   = company,
        role      = "client",
        is_active = True,
        password  = generate_password_hash("pw"),
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


def _make_wtg(db, project_id):
    from models import WTG
    w = WTG(name=f"WTG-{_uid()}", project_id=project_id)
    db.session.add(w)
    db.session.flush()
    return w


def _make_itp_record(db, wtg, proj_template_id=None):
    from models import ITPRecord
    r = ITPRecord(
        wtg_id                  = wtg.id,
        itp_type                = "TEST_ITP",
        status                  = "in_progress",
        project_itp_template_id = proj_template_id,
        client_token            = uuid.uuid4().hex,
    )
    db.session.add(r)
    db.session.flush()
    return r


def _make_item_status(db, record, item_no="1", ci=0, signed=True,
                      client_reviewed=False, client_accepted=None):
    from models import ITPItemStatus
    s = ITPItemStatus(
        itp_record_id   = record.id,
        item_no         = str(item_no),
        criterion_index = ci,
        lucas_complete  = signed,
        lucas_signed_at = datetime.now(timezone.utc) if signed else None,
        client_reviewed = client_reviewed,
        client_accepted = client_accepted,
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
                 review_cycle_id=None, item_scope_ids=None):
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
        review_cycle_id      = review_cycle_id,
    )
    if item_scope_ids is not None:
        inv.item_scope_ids = item_scope_ids
    db.session.add(inv)
    db.session.flush()
    return inv


def _inject_session(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"]    = str(user_id)
        sess["_fresh"]      = True
        sess["_csrf_token"] = CSRF


def _post_review(client, token, item_no, ci, action="accept",
                 signature="data:image/png;base64,ABC", comment="needs work"):
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


# ─── Phase 2A: per-criterion identity storage ─────────────────────────────────

class TestPhase2AIdentityStorage:

    def test_approve_stores_per_criterion_identity(self, client, db):
        """Approving a criterion populates all identity + invite fields."""
        from models import ITPItemStatus

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)
        status = _make_item_status(db, record)
        user   = _make_user(db)
        member = _make_member_ac(db, proj, user)
        invite = _make_invite(db, record, member, user)
        db.session.commit()

        _inject_session(client, user.id)
        resp, data = _post_review(client, invite.token, "1", 0, action="accept")

        assert resp.status_code == 200, data
        db.session.expire_all()
        s = db.session.get(ITPItemStatus, status.id)

        assert s.client_signed_by_id      == user.id
        assert s.client_signed_by_name    == user.name
        assert s.client_signed_by_company == user.company
        assert s.client_invite_id         == invite.id
        assert s.client_review_cycle_id   is None   # no cycle on this invite

    def test_concern_stores_per_criterion_identity(self, client, db):
        """Raising a concern also stores reviewer identity."""
        from models import ITPItemStatus

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)
        status = _make_item_status(db, record, ci=0)
        user   = _make_user(db)
        member = _make_member_ac(db, proj, user)
        invite = _make_invite(db, record, member, user)
        db.session.commit()

        _inject_session(client, user.id)
        resp, data = _post_review(
            client, invite.token, "1", 0,
            action="request_changes", comment="Please revise.",
        )

        assert resp.status_code == 200, data
        db.session.expire_all()
        s = db.session.get(ITPItemStatus, status.id)

        assert s.client_signed_by_id   == user.id
        assert s.client_signed_by_name == user.name
        assert s.client_invite_id      == invite.id

    def test_reset_clears_per_criterion_identity(self, client, db):
        """Reset action clears all identity fields."""
        from models import ITPItemStatus

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)
        status = _make_item_status(db, record, ci=0)
        # Add a second criterion still pending so the ITP is NOT fully complete
        # after approving ci=0 (prevents the ITP-locked guard from blocking reset)
        _make_item_status(db, record, ci=1, signed=False)
        user   = _make_user(db)
        member = _make_member_ac(db, proj, user)
        invite = _make_invite(db, record, member, user)
        db.session.commit()

        _inject_session(client, user.id)

        # First approve
        _post_review(client, invite.token, "1", 0, action="accept")

        # Then reset
        resp, data = _post_review(client, invite.token, "1", 0, action="reset")
        assert resp.status_code == 200, data

        db.session.expire_all()
        s = db.session.get(ITPItemStatus, status.id)

        assert s.client_signed_by_id      is None
        assert s.client_signed_by_name    is None
        assert s.client_signed_by_company is None
        assert s.client_invite_id         is None
        assert s.client_review_cycle_id   is None

    def test_identity_includes_review_cycle_when_set(self, client, db):
        """client_review_cycle_id is stored when the invite has a cycle."""
        from models import ITPItemStatus, ITPReviewCycle

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)
        status = _make_item_status(db, record, ci=0)
        user   = _make_user(db)
        member = _make_member_ac(db, proj, user)

        cycle = ITPReviewCycle(
            record_id    = record.id,
            cycle_number = 1,
            revision     = 0,
            status       = "open",
        )
        db.session.add(cycle)
        db.session.flush()

        invite = _make_invite(db, record, member, user, review_cycle_id=cycle.id)
        db.session.commit()

        _inject_session(client, user.id)
        resp, data = _post_review(client, invite.token, "1", 0, action="accept")
        assert resp.status_code == 200, data

        db.session.expire_all()
        s = db.session.get(ITPItemStatus, status.id)
        assert s.client_review_cycle_id == cycle.id


# ─── Phase 2B: item scope + get_reviewable_criteria_for_invitee ───────────────

class TestPhase2BGetReviewableCriteria:

    def test_scope_empty_when_all_in_active_invite(self, db):
        """No new criteria returned when all signed criteria are in an active invite."""
        from app import get_reviewable_criteria_for_invitee

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)
        s1     = _make_item_status(db, record, item_no="1", ci=0, signed=True)
        s2     = _make_item_status(db, record, item_no="1", ci=1, signed=True)
        user   = _make_user(db)
        member = _make_member_ac(db, proj, user)
        # Active invite covering both criteria
        _make_invite(db, record, member, user,
                     status="pending_review",
                     item_scope_ids=[s1.id, s2.id])
        db.session.commit()

        result = get_reviewable_criteria_for_invitee(record, user.email)
        assert result == [], (
            f"Expected empty scope, got {[x.id for x in result]}"
        )

    def test_new_criteria_returned_after_additional_signing(self, db):
        """New signed criterion not in existing invite scope IS returned."""
        from app import get_reviewable_criteria_for_invitee

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)
        s1     = _make_item_status(db, record, item_no="1", ci=0, signed=True)
        s2     = _make_item_status(db, record, item_no="1", ci=1, signed=True)
        # s3 is newly signed AFTER the existing invite was sent
        s3     = _make_item_status(db, record, item_no="1", ci=2, signed=True)
        user   = _make_user(db)
        member = _make_member_ac(db, proj, user)
        # Existing invite only covers s1 and s2
        _make_invite(db, record, member, user,
                     status="pending_review",
                     item_scope_ids=[s1.id, s2.id])
        db.session.commit()

        result = get_reviewable_criteria_for_invitee(record, user.email)
        result_ids = [s.id for s in result]
        assert s3.id in result_ids, "Newly signed criterion should be in scope"
        assert s1.id not in result_ids, "s1 already in active invite — should not be in new scope"
        assert s2.id not in result_ids, "s2 already in active invite — should not be in new scope"

    def test_already_approved_excluded_from_scope(self, db):
        """Globally approved criteria are never included in new scope."""
        from app import get_reviewable_criteria_for_invitee

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)
        # s1 approved, s2 not
        s1 = _make_item_status(db, record, item_no="1", ci=0, signed=True,
                               client_reviewed=True, client_accepted=True)
        s2 = _make_item_status(db, record, item_no="1", ci=1, signed=True)
        user   = _make_user(db)
        member = _make_member_ac(db, proj, user)
        db.session.commit()

        result = get_reviewable_criteria_for_invitee(record, user.email)
        result_ids = [s.id for s in result]
        assert s1.id not in result_ids, "Approved criterion must be excluded"
        assert s2.id in result_ids,     "Unapproved signed criterion must be included"

    def test_legacy_invite_without_scope_blocks_reinvite(self, db):
        """Legacy invite with no item_scope_json covers all signed criteria."""
        from app import get_reviewable_criteria_for_invitee

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)
        s1     = _make_item_status(db, record, item_no="1", ci=0, signed=True)
        s2     = _make_item_status(db, record, item_no="1", ci=1, signed=True)
        user   = _make_user(db)
        member = _make_member_ac(db, proj, user)
        # Legacy invite: item_scope_json is NULL
        _make_invite(db, record, member, user,
                     status="pending_review",
                     item_scope_ids=None)   # None → item_scope_json stays NULL
        db.session.commit()

        result = get_reviewable_criteria_for_invitee(record, user.email)
        assert result == [], (
            "Legacy invite (no scope) should block re-invite for all current criteria"
        )

    def test_no_email_returns_all_signed_non_approved(self, db):
        """When email is blank, all signed non-approved criteria are returned."""
        from app import get_reviewable_criteria_for_invitee

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)
        s1 = _make_item_status(db, record, item_no="1", ci=0, signed=True,
                               client_reviewed=True, client_accepted=True)
        s2 = _make_item_status(db, record, item_no="1", ci=1, signed=True)
        db.session.commit()

        result = get_reviewable_criteria_for_invitee(record, "")
        result_ids = [s.id for s in result]
        assert s1.id not in result_ids, "Approved criterion excluded even with blank email"
        assert s2.id in result_ids,     "Unapproved signed criterion included"

    def test_unsigned_criteria_never_in_scope(self, db):
        """Criteria not yet engineer-signed are never included."""
        from app import get_reviewable_criteria_for_invitee

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)
        _make_item_status(db, record, item_no="1", ci=0, signed=False)  # not signed
        s2 = _make_item_status(db, record, item_no="1", ci=1, signed=True)
        user   = _make_user(db)
        db.session.commit()

        result = get_reviewable_criteria_for_invitee(record, user.email)
        result_ids = [s.id for s in result]
        assert s2.id in result_ids
        assert len(result_ids) == 1, "Only signed criterion should appear"

    def test_revoked_invite_does_not_block_scope(self, db):
        """A revoked invite does not count towards 'active' scope."""
        from app import get_reviewable_criteria_for_invitee
        from models import ITPClientInvite

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)
        s1     = _make_item_status(db, record, item_no="1", ci=0, signed=True)
        user   = _make_user(db)
        member = _make_member_ac(db, proj, user)
        invite = _make_invite(db, record, member, user,
                              status="pending_review",
                              item_scope_ids=[s1.id])
        # Revoke it
        invite.is_revoked = True
        db.session.commit()

        result = get_reviewable_criteria_for_invitee(record, user.email)
        assert s1.id in [s.id for s in result], (
            "Revoked invite should not block re-invite for the same criteria"
        )

    def test_signed_invite_does_not_block_scope(self, db):
        """A 'signed' (completed) invite does not block new invites for same criteria."""
        from app import get_reviewable_criteria_for_invitee

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)
        s1     = _make_item_status(db, record, item_no="1", ci=0, signed=True)
        user   = _make_user(db)
        member = _make_member_ac(db, proj, user)
        # Signed (completed) invite — status='signed' is not in the pending set
        _make_invite(db, record, member, user,
                     status="signed",
                     item_scope_ids=[s1.id])
        db.session.commit()

        result = get_reviewable_criteria_for_invitee(record, user.email)
        # s1 is not approved (client_accepted is None), so it should be in scope
        assert s1.id in [s.id for s in result], (
            "Completed (signed) invite should not block new invite for unapproved criteria"
        )


# ─── Phase 2B: item_scope_ids property ──────────────────────────────────────

class TestItemScopeIdsProperty:

    def test_item_scope_ids_round_trip(self, db):
        """item_scope_ids getter/setter serializes to JSON correctly."""
        from models import ITPClientInvite

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)
        user   = _make_user(db)
        member = _make_member_ac(db, proj, user)
        invite = _make_invite(db, record, member, user)
        ids = [1, 2, 3]
        invite.item_scope_ids = ids
        db.session.commit()

        db.session.expire_all()
        from models import ITPClientInvite as IC
        fresh = db.session.get(IC, invite.id)
        assert fresh.item_scope_ids == ids

    def test_item_scope_ids_empty_when_null(self, db):
        """item_scope_ids returns [] when item_scope_json is NULL."""
        from models import ITPClientInvite

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)
        user   = _make_user(db)
        member = _make_member_ac(db, proj, user)
        invite = _make_invite(db, record, member, user)   # item_scope_ids = None
        db.session.commit()

        assert invite.item_scope_ids == []

    def test_item_scope_ids_empty_when_empty_list(self, db):
        """item_scope_ids round-trips empty list."""
        from models import ITPClientInvite

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)
        user   = _make_user(db)
        member = _make_member_ac(db, proj, user)
        invite = _make_invite(db, record, member, user, item_scope_ids=[])
        db.session.commit()

        db.session.expire_all()
        from models import ITPClientInvite as IC
        fresh = db.session.get(IC, invite.id)
        assert fresh.item_scope_ids == []


# ─── Phase 2C: Progressive review submission ────────────────────────────────

SIG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def _add_sign_perm(db, member):
    """Add can_sign_client_itp permission to a ProjectMemberAC row."""
    from models import ProjectMemberPermission
    db.session.add(ProjectMemberPermission(
        member_id      = member.id,
        permission_key = "can_sign_client_itp",
        value          = True,
    ))
    db.session.flush()


class TestProgressiveReviewSubmission:
    """Tests for the two-tier client submission flow:
    - 'Submit Current Review' when only scoped criteria are all reviewed.
    - 'Final Acknowledgement' only when the entire ITP is complete.
    """

    def test_scoped_invite_can_submit_when_scope_complete_but_itp_not(self, client, db):
        """A scoped invite whose criteria are all reviewed+accepted can be submitted
        even when other ITP criteria are still unsigned (whole ITP not complete).

        Acceptance criterion 1: scoped invite with all scoped items reviewed but
        whole ITP incomplete can submit current review.
        """
        from models import ITPRecord, ITPClientInvite

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)

        # s1, s2 are in scope — both signed, reviewed, and accepted
        s1 = _make_item_status(db, record, item_no="1", ci=0, signed=True,
                               client_reviewed=True, client_accepted=True)
        s2 = _make_item_status(db, record, item_no="1", ci=1, signed=True,
                               client_reviewed=True, client_accepted=True)
        # s3, s4 are NOT in scope and still unsigned → whole ITP is NOT complete
        _make_item_status(db, record, item_no="2", ci=0, signed=False)
        _make_item_status(db, record, item_no="2", ci=1, signed=False)

        user   = _make_user(db)
        member = _make_member_ac(db, proj, user)
        _add_sign_perm(db, member)
        invite = _make_invite(db, record, member, user,
                              status="pending_review",
                              item_scope_ids=[s1.id, s2.id])
        db.session.commit()

        _inject_session(client, user.id)
        resp = client.post(
            f"/itp/client/{invite.token}",
            data={
                "action":           "client_sign",
                "client_signature": SIG,
                "submission_type":  "current_scope",
            },
            follow_redirects=False,
        )
        # Route should redirect on success (not 400/403/500)
        assert resp.status_code in (302, 303), (
            f"Expected redirect on success, got {resp.status_code}"
        )

        db.session.expire_all()
        inv = db.session.get(ITPClientInvite, invite.id)
        assert inv.status == "signed", (
            f"Invite should be marked signed after successful submission, got {inv.status!r}"
        )

    def test_current_review_does_not_set_itp_complete(self, client, db):
        """Submitting a current-scope review does NOT mark the ITP as complete
        when there are still unsigned or unreviewed criteria outside the scope.

        Acceptance criterion 2: current review submission does not set whole ITP
        to complete.
        """
        from models import ITPRecord, ITPClientInvite

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)

        # Scope criteria: signed, reviewed, accepted
        s1 = _make_item_status(db, record, item_no="1", ci=0, signed=True,
                               client_reviewed=True, client_accepted=True)
        s2 = _make_item_status(db, record, item_no="1", ci=1, signed=True,
                               client_reviewed=True, client_accepted=True)
        # Out-of-scope criteria: not yet signed → ITP cannot be complete
        _make_item_status(db, record, item_no="2", ci=0, signed=False)
        _make_item_status(db, record, item_no="2", ci=1, signed=False)

        user   = _make_user(db)
        member = _make_member_ac(db, proj, user)
        _add_sign_perm(db, member)
        invite = _make_invite(db, record, member, user,
                              status="pending_review",
                              item_scope_ids=[s1.id, s2.id])
        db.session.commit()

        _inject_session(client, user.id)
        client.post(
            f"/itp/client/{invite.token}",
            data={
                "action":           "client_sign",
                "client_signature": SIG,
                "submission_type":  "current_scope",
            },
            follow_redirects=False,
        )

        db.session.expire_all()
        rec = db.session.get(ITPRecord, record.id)
        assert rec.status != "complete", (
            f"ITP should NOT be complete after a partial-scope submission, "
            f"got status={rec.status!r}"
        )

    def test_api_ready_for_final_only_when_all_criteria_complete(self, client, db):
        """The review-item API returns ready_for_final=False when unsigned criteria
        remain, and ready_for_final=True only when all criteria are signed+reviewed+accepted.

        Acceptance criterion 3: final acknowledgement only enabled when all criteria
        are signed and approved.
        """
        from models import ITPItemStatus

        proj   = _make_project(db)
        wtg    = _make_wtg(db, proj.id)
        record = _make_itp_record(db, wtg)

        # 2 criteria total; start with only 1 signed and reviewed
        s1 = _make_item_status(db, record, item_no="1", ci=0, signed=True)
        s2 = _make_item_status(db, record, item_no="1", ci=1, signed=False)

        user   = _make_user(db)
        member = _make_member_ac(db, proj, user)
        invite = _make_invite(db, record, member, user)
        db.session.commit()

        _inject_session(client, user.id)

        # Accept criterion 0 — s2 still unsigned, so ready_for_final must be False
        resp, data = _post_review(client, invite.token, "1", 0, action="accept")
        assert resp.status_code == 200, data
        assert data.get("ready_for_final") is False, (
            f"ready_for_final should be False while s2 is unsigned, got {data!r}"
        )
        assert data.get("current_scope_ready") is True, (
            f"current_scope_ready should be True once s1 is accepted (no explicit scope = all signed), "
            f"got {data!r}"
        )

        # Now sign s2 and accept it → whole ITP complete → ready_for_final = True
        db.session.expire_all()
        s2_fresh = db.session.get(ITPItemStatus, s2.id)
        s2_fresh.lucas_complete  = True
        s2_fresh.lucas_signed_at = datetime.now(timezone.utc)
        db.session.commit()

        resp2, data2 = _post_review(client, invite.token, "1", 1, action="accept")
        assert resp2.status_code == 200, data2
        assert data2.get("ready_for_final") is True, (
            f"ready_for_final should be True when all criteria are signed+accepted, "
            f"got {data2!r}"
        )
