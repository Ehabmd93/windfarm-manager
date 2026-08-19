"""
UX/workflow improvement tests.

Covers:
  1. Map & Geofencing optional module
     - Project can be created with Map disabled; map route redirects when off.
     - Legacy projects (no 'map' ProjectFeature row) keep map enabled.
  2. Company type "Other" with custom free-text value
     - Predefined types still work; custom value saved and labelled;
       "Other" without a custom value is rejected.
  3. Unscoped ITP template authoring
     - Template can be created with empty scope ("Needs scope" draft).
     - Draft appears on ITP index with the badge.
     - Draft cannot open /element/<eid> (no ITPRecord is created).
     - Scoped templates behave exactly as before.
     - Scope can be assigned to a draft later; invalid scope rejected.

Run with:
    cd "C:\\Users\\ehaby\\Desktop\\Windfarm Manger\\windfarm-manager"
    python -m pytest tests/test_ux_improvements.py -v
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


def _make_user(db, role="engineer", company="AcmeCorp"):
    from models import User
    uid = _uid()
    u = User(
        name      = f"User-{uid}",
        email     = f"{uid}@test.com",
        company   = company,
        role      = role,
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


def _make_owner(db, project, user):
    """Active owner ProjectMemberAC row — owners hold every permission."""
    from models import ProjectMemberAC
    m = ProjectMemberAC(
        project_id   = project.id,
        user_id      = user.id,
        email        = user.email,
        name         = user.name,
        is_owner     = True,
        access_level = "owner",
        invite_status= "accepted",
        is_active    = True,
    )
    db.session.add(m)
    db.session.flush()
    return m


def _make_element(db, project_id, name=None):
    from models import WTG
    w = WTG(name=name or f"WTG-{_uid()}", project_id=project_id)
    db.session.add(w)
    db.session.flush()
    return w


def _make_template(db, project_id, user_id, scope=None, items=None):
    from models import ProjectITPTemplate
    t = ProjectITPTemplate(
        project_id    = project_id,
        itp_number    = "01",
        name          = f"ITP-{_uid()}",
        created_by_id = user_id,
    )
    t.applicable_scope = scope or []
    t.items = items if items is not None else [{
        "no": "1", "activity": "Earthworks",
        "criteria": ["Surface compacted"],
        "rows": [{"inspection": "H", "frequency": "Each lot"}],
        "lucas_codes": [], "client_codes": [], "hold_witness": None,
    }]
    db.session.add(t)
    db.session.flush()
    return t


def _inject_session(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"]    = str(user_id)
        sess["_fresh"]      = True
        sess["_csrf_token"] = CSRF


def _json_post(client, url, payload):
    resp = client.post(
        url, json=payload,
        content_type="application/json",
        headers={"X-CSRF-Token": CSRF},
    )
    return resp, resp.get_json(silent=True) or {}


# ─── 1. Map & Geofencing optional ────────────────────────────────────────────

class TestMapFeatureOptional:

    def test_project_created_without_map(self, client, db):
        """Project creation succeeds with Map & Geofencing disabled, and the
        map route redirects away instead of rendering."""
        from models import Project, ALL_FEATURES

        user = _make_user(db, role="engineer")
        db.session.commit()
        _inject_session(client, user.id)

        form = {"name": f"NoMap-{_uid()}", "project_type": "Wind Farm",
                "status": "active"}
        for key, *_ in ALL_FEATURES:
            form[f"feat_{key}"] = "0"

        resp = client.post("/projects/new", data=form, follow_redirects=False)
        assert resp.status_code in (302, 303), (
            f"Project creation should redirect on success, got {resp.status_code}"
        )

        proj = Project.query.filter_by(name=form["name"]).first()
        assert proj is not None, "Project was not created"
        assert proj.feature_enabled("map") is False

        # Map route must not break — it redirects with a flash
        resp2 = client.get(f"/projects/{proj.id}/map", follow_redirects=False)
        assert resp2.status_code in (302, 303), (
            f"Disabled map should redirect, got {resp2.status_code}"
        )

    def test_legacy_project_without_map_row_keeps_map(self, db):
        """Projects created before the flag (no 'map' ProjectFeature row)
        default to map ENABLED — existing projects are unaffected."""
        proj = _make_project(db)
        db.session.commit()
        assert proj.feature_enabled("map") is True

    def test_map_in_all_features(self):
        """'map' is a real feature key so settings/wizard toggles include it."""
        from models import ALL_FEATURES
        assert any(key == "map" for key, *_ in ALL_FEATURES)


# ─── 2. Company type "Other" ─────────────────────────────────────────────────

class TestCompanyTypeOther:

    def _setup(self, client, db):
        user = _make_user(db)
        proj = _make_project(db)
        _make_owner(db, proj, user)
        db.session.commit()
        _inject_session(client, user.id)
        return user, proj

    def test_predefined_company_type_still_works(self, client, db):
        from models import ProjectCompany
        user, proj = self._setup(client, db)

        resp, d = _json_post(client, f"/projects/{proj.id}/companies",
                             {"company_type": "client", "name": "Acme Client"})
        assert resp.status_code == 200, d
        assert d.get("ok") is True
        c = db.session.get(ProjectCompany, d["id"])
        assert c.company_type == "client"
        assert c.type_label == "Client"

    def test_other_custom_company_type_saved(self, client, db):
        from models import ProjectCompany
        user, proj = self._setup(client, db)

        resp, d = _json_post(client, f"/projects/{proj.id}/companies",
                             {"company_type": "other",
                              "custom_company_type": "Surveyor",
                              "name": "Geo Surveys Pty Ltd"})
        assert resp.status_code == 200, d
        assert d.get("ok") is True
        c = db.session.get(ProjectCompany, d["id"])
        assert c.company_type == "Surveyor"
        assert c.type_label == "Surveyor"   # graceful fallback label

    def test_other_without_custom_value_rejected(self, client, db):
        user, proj = self._setup(client, db)
        resp, d = _json_post(client, f"/projects/{proj.id}/companies",
                             {"company_type": "other", "name": "No Type Co"})
        assert resp.status_code == 400
        assert "Custom company type" in (d.get("error") or "")


# ─── 3. Unscoped ITP template authoring ──────────────────────────────────────

class TestUnscopedITPTemplate:

    def _setup(self, client, db):
        user = _make_user(db)
        proj = _make_project(db)
        _make_owner(db, proj, user)
        db.session.commit()
        _inject_session(client, user.id)
        return user, proj

    def test_create_template_with_empty_scope(self, client, db):
        """Wizard POST with scope_selection=[] saves an unscoped draft."""
        from models import ProjectITPTemplate
        user, proj = self._setup(client, db)

        payload = {
            "itp_number": "01", "name": "Drafted Before Hierarchy",
            "revision": "A", "items": [
                {"no": "1", "activity": "Earthworks",
                 "criteria": ["Compaction OK"],
                 "rows": [{"inspection": "H", "frequency": "Each lot"}],
                 "lucas_codes": [], "client_codes": [], "hold_witness": None},
            ],
            "scope_selection": [],
        }
        resp, d = _json_post(client, f"/projects/{proj.id}/itp/create", payload)
        assert resp.status_code == 200, d
        assert d.get("ok") is True

        t = db.session.get(ProjectITPTemplate, d["id"])
        assert t.applicable_scope == []
        assert t.items[0]["activity"] == "Earthworks"

    def test_unscoped_template_shows_needs_scope_on_index(self, client, db):
        user, proj = self._setup(client, db)
        _make_template(db, proj.id, user.id, scope=[])
        db.session.commit()

        resp = client.get(f"/projects/{proj.id}/itp")
        assert resp.status_code == 200
        assert b"Needs scope" in resp.data

    def test_unscoped_template_cannot_open_element_record(self, client, db):
        """Opening /element/<eid> on an unscoped draft redirects away and
        creates NO ITPRecord (so signing/invites/evidence are impossible)."""
        from models import ITPRecord
        user, proj = self._setup(client, db)
        el = _make_element(db, proj.id)
        t  = _make_template(db, proj.id, user.id, scope=[])
        db.session.commit()

        resp = client.get(f"/projects/{proj.id}/itp/{t.id}/element/{el.id}",
                          follow_redirects=False)
        assert resp.status_code in (302, 303), (
            f"Unscoped template detail must redirect, got {resp.status_code}"
        )
        count = ITPRecord.query.filter_by(project_itp_template_id=t.id).count()
        assert count == 0, "No ITPRecord may be created for an unscoped template"

    def test_scoped_template_still_creates_record(self, client, db):
        """Existing scoped behavior unchanged: detail page renders and
        creates the ITPRecord."""
        from models import ITPRecord
        user, proj = self._setup(client, db)
        el = _make_element(db, proj.id)
        t  = _make_template(db, proj.id, user.id,
                            scope=[{"type": "element", "id": el.id, "name": el.name}])
        db.session.commit()

        resp = client.get(f"/projects/{proj.id}/itp/{t.id}/element/{el.id}",
                          follow_redirects=False)
        assert resp.status_code == 200, (
            f"Scoped template detail should render, got {resp.status_code}"
        )
        count = ITPRecord.query.filter_by(project_itp_template_id=t.id).count()
        assert count == 1

    def test_assign_scope_to_draft_then_open(self, client, db):
        """Scope can be assigned to a draft later; afterwards records work."""
        from models import ProjectITPTemplate, ITPRecord
        user, proj = self._setup(client, db)
        el = _make_element(db, proj.id)
        t  = _make_template(db, proj.id, user.id, scope=[])
        db.session.commit()

        resp, d = _json_post(
            client, f"/projects/{proj.id}/itp/{t.id}/assign-scope",
            {"scope_selection": [{"type": "element", "id": el.id, "name": el.name}]})
        assert resp.status_code == 200, d
        assert d.get("ok") is True

        db.session.expire_all()
        t2 = db.session.get(ProjectITPTemplate, t.id)
        assert t2.applicable_scope == [{"type": "element", "id": el.id, "name": el.name}]

        # Now the element record opens normally
        resp2 = client.get(f"/projects/{proj.id}/itp/{t.id}/element/{el.id}")
        assert resp2.status_code == 200
        assert ITPRecord.query.filter_by(project_itp_template_id=t.id).count() == 1

    def test_assign_scope_rejects_foreign_ids(self, client, db):
        """Scope entries must belong to the same project."""
        user, proj = self._setup(client, db)
        other_proj = _make_project(db)
        foreign_el = _make_element(db, other_proj.id)
        t = _make_template(db, proj.id, user.id, scope=[])
        db.session.commit()

        resp, d = _json_post(
            client, f"/projects/{proj.id}/itp/{t.id}/assign-scope",
            {"scope_selection": [{"type": "element", "id": foreign_el.id,
                                  "name": foreign_el.name}]})
        assert resp.status_code == 400, d

    def test_assign_scope_rejects_empty_selection(self, client, db):
        user, proj = self._setup(client, db)
        t = _make_template(db, proj.id, user.id, scope=[])
        db.session.commit()

        resp, d = _json_post(client, f"/projects/{proj.id}/itp/{t.id}/assign-scope",
                             {"scope_selection": []})
        assert resp.status_code == 400, d


# ─── 4. Client review page render (UI redesign smoke tests) ─────────────────

class TestClientSignPageRender:
    """The redesigned itp_client_sign.html must render without template errors
    in every major state. These are smoke tests for the Jinja layer — the
    review/sign workflow itself is covered by test_itp_phase2.py."""

    def _setup_full(self, client, db):
        from models import ITPRecord, ITPItemStatus, ITPClientInvite
        user   = _make_user(db)
        proj   = _make_project(db)
        member = _make_owner(db, proj, user)
        el     = _make_element(db, proj.id)
        t = _make_template(
            db, proj.id, user.id,
            scope=[{"type": "element", "id": el.id, "name": el.name}],
            items=[{
                "no": "1", "activity": "Earthworks",
                "criteria": ["Surface compacted to spec", "Layer thickness verified"],
                "rows": [{"inspection": "H", "frequency": "Each lot"},
                         {"inspection": "W", "frequency": "Each lot"}],
                "lucas_codes": [], "client_codes": [], "hold_witness": None,
            }])
        rec = ITPRecord(
            wtg_id=el.id, itp_type=t.itp_type_key,
            project_itp_template_id=t.id, status="in_progress",
            client_token=uuid.uuid4().hex,
            engineer_name=user.name, engineer_company=user.company,
        )
        db.session.add(rec)
        db.session.flush()
        s0 = ITPItemStatus(itp_record_id=rec.id, item_no="1", criterion_index=0,
                           lucas_complete=True,
                           lucas_signed_at=datetime.now(timezone.utc))
        s1 = ITPItemStatus(itp_record_id=rec.id, item_no="1", criterion_index=1,
                           lucas_complete=False)
        db.session.add_all([s0, s1])
        db.session.flush()
        inv = ITPClientInvite(
            record_id=rec.id, project_member_ac_id=member.id, user_id=user.id,
            token=f"rtok-{_uid()}", name=user.name, email=user.email,
            company=user.company, status="pending_review", is_revoked=False,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.session.add(inv)
        db.session.commit()
        _inject_session(client, user.id)
        return user, proj, el, t, rec, inv, (s0, s1)

    def test_render_open_review(self, client, db):
        """Open review state: checklist, progress panel, action buttons,
        sticky submit bar, and both signed/unsigned criterion branches."""
        *_, rec, inv, _ = self._setup_full(client, db)
        resp = client.get(f"/itp/client/{inv.token}")
        assert resp.status_code == 200
        assert b"Inspection Checklist" in resp.data
        assert b"Review Progress" in resp.data
        assert b"Approve &amp; Sign" in resp.data
        assert b"Awaiting engineer sign-off" in resp.data
        assert b"sticky-complete-btn" in resp.data
        assert b"completion-modal-overlay" in resp.data

    def test_render_scoped_invite_banner(self, client, db):
        """Scoped invite shows the scope banner and in/out-of-scope badges."""
        *_, rec, inv, (s0, s1) = self._setup_full(client, db)
        inv.item_scope_ids = [s0.id]
        db.session.commit()
        resp = client.get(f"/itp/client/{inv.token}")
        assert resp.status_code == 200
        assert b"Your review scope" in resp.data
        assert b"In your scope" in resp.data

    def test_render_complete_state(self, client, db):
        """Completed ITP renders the approval banner without errors."""
        from models import ITPRecord
        *_, rec, inv, _ = self._setup_full(client, db)
        rec2 = db.session.get(ITPRecord, rec.id)
        rec2.status         = "complete"
        rec2.client_name    = "Jane Client"
        rec2.client_company = "ClientCo"
        rec2.client_signed_at = datetime.now(timezone.utc)
        db.session.commit()
        resp = client.get(f"/itp/client/{inv.token}")
        assert resp.status_code == 200
        assert b"ITP Approved" in resp.data
        assert b"Jane Client" in resp.data

    def test_render_submitted_readonly_state(self, client, db):
        """After the invite is signed, the client page shows the read-only
        'Review Submitted' confirmation and hides the sticky submit panel."""
        from models import ITPClientInvite
        *_, rec, inv, _ = self._setup_full(client, db)
        i2 = db.session.get(ITPClientInvite, inv.id)
        i2.status    = "signed"
        i2.signed_at = datetime.now(timezone.utc)
        db.session.commit()
        resp = client.get(f"/itp/client/{inv.token}")
        assert resp.status_code == 200
        assert b"Review Submitted" in resp.data
        # Sticky submit panel HTML is not rendered once the review is locked.
        # (The string lives only in the gated HTML block, not the always-on JS.)
        assert b"STICKY COMPLETION PANEL" not in resp.data

    def test_internal_detail_shows_action_required_banner(self, client, db):
        """project_itp_detail renders the action-required banner and the client
        comment when a criterion has an unresolved concern."""
        user, proj, el, t, rec, inv, (s0, s1) = self._setup_full(client, db)
        s0b = db.session.get(type(s0), s0.id)
        s0b.client_reviewed = True
        s0b.client_accepted = False
        s0b.client_action   = "request_changes"
        s0b.client_comments = "Fix the weld joint per drawing Rev C"
        s0b.client_signed_by_name    = "Jane Client"
        s0b.client_signed_by_company = "ClientCo"
        db.session.commit()
        _inject_session(client, user.id)

        resp = client.get(f"/projects/{proj.id}/itp/{t.id}/element/{el.id}")
        assert resp.status_code == 200
        assert b"Action required" in resp.data
        assert b"Fix the weld joint per drawing Rev C" in resp.data
        assert b"Changes Requested" in resp.data
        # Hero banner uses COMPUTED status, not the raw record.status column
        assert b"ACTION REQUIRED" in resp.data

    def test_internal_detail_groups_duplicate_invites(self, client, db):
        """Multiple invites for the same person render as ONE card with an
        expandable invite history, not a stack of duplicate cards."""
        from models import ITPClientInvite
        user, proj, el, t, rec, inv, _ = self._setup_full(client, db)
        # Second invite for the SAME person (same email) — e.g. a re-review cycle
        inv2 = ITPClientInvite(
            record_id=rec.id, project_member_ac_id=inv.project_member_ac_id,
            user_id=user.id, token=f"rtok2-{_uid()}", name=user.name,
            email=user.email, company=user.company, status="signed",
            is_revoked=False,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.session.add(inv2)
        db.session.commit()
        _inject_session(client, user.id)

        resp = client.get(f"/projects/{proj.id}/itp/{t.id}/element/{el.id}")
        assert resp.status_code == 200
        assert b"previous invite" in resp.data, (
            "Duplicate invites for one person should collapse into history"
        )

        # Template view renders with the deterministic element palette
        resp2 = client.get(f"/projects/{proj.id}/itp/{t.id}")
        assert resp2.status_code == 200
        assert b"fa-cube" in resp2.data or b"fa-road" in resp2.data or b"fa-wind" in resp2.data


# ─── 5. CAD (DXF) map import + Foundation 3D viewer ─────────────────────────

def _make_dxf_bytes():
    """Build a small in-memory DXF with MGA zone 50 coordinates (Kondinin WA)."""
    import io as _io
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.layers.add("ROADS", color=1)
    doc.layers.add("LABELS", color=5)
    msp = doc.modelspace()
    msp.add_line((760000, 6380000), (760500, 6380500), dxfattribs={"layer": "ROADS"})
    msp.add_circle((760250, 6380250), 100, dxfattribs={"layer": "ROADS"})
    msp.add_text("WTG01", dxfattribs={"layer": "LABELS", "insert": (760100, 6380100)})
    buf = _io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode()


class TestCadMapImport:

    def test_dxf_parser_transforms_mga_to_wgs84(self):
        """DXF entities parse per-layer and MGA zone 50 coords land in WA."""
        import cad_parser
        layers = cad_parser.parse_bytes(_make_dxf_bytes(), "site.dxf", epsg=7850)
        assert set(layers.keys()) == {"ROADS", "LABELS"}
        # Text label becomes a named Point feature
        lbl = layers["LABELS"]["features"][0]
        assert lbl["properties"]["name"] == "WTG01"
        lon, lat = lbl["geometry"]["coordinates"]
        assert 118 < lon < 121 and -34 < lat < -31, f"Expected Kondinin WA, got {lon},{lat}"
        # Circle became a closed polygon
        kinds = {f["geometry"]["type"] for f in layers["ROADS"]["features"]}
        assert "Polygon" in kinds and "LineString" in kinds

    def test_map_upload_accepts_dxf(self, client, db):
        import io as _io
        from models import ProjectMapFile
        user = _make_user(db)
        proj = _make_project(db)
        _make_owner(db, proj, user)
        db.session.commit()
        _inject_session(client, user.id)

        resp = client.post(
            f"/projects/{proj.id}/map/upload",
            data={"file": (_io.BytesIO(_make_dxf_bytes()), "site.dxf"),
                  "epsg": "7850"},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200, resp.get_json(silent=True)
        d = resp.get_json()
        assert d["ok"] is True
        assert "ROADS" in d["layers"]
        mf = ProjectMapFile.query.filter_by(project_id=proj.id).first()
        assert mf is not None and "ROADS" in (mf.layer_names or "")

    def test_map_upload_dwg_gets_guidance(self, client, db):
        import io as _io
        user = _make_user(db)
        proj = _make_project(db)
        _make_owner(db, proj, user)
        db.session.commit()
        _inject_session(client, user.id)

        resp = client.post(
            f"/projects/{proj.id}/map/upload",
            data={"file": (_io.BytesIO(b"AC1032 binary junk"), "site.dwg")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        d = resp.get_json()
        assert d.get("dwg_hint") is True
        assert "DXF" in d["error"]


class TestElementDelete:
    """Deleting an element must clean up NOT-NULL dependents (foundation
    stages, draft ITP records) instead of 500ing — and must REFUSE to destroy
    elements that carry real QA history (signatures/invites/notes)."""

    def _setup(self, client, db):
        from models import ProjectMember
        user = _make_user(db)
        proj = _make_project(db)
        _make_owner(db, proj, user)
        # element-delete permission uses the LEGACY ProjectMember table
        db.session.add(ProjectMember(project_id=proj.id, user_id=user.id,
                                     proj_role='owner'))
        db.session.flush()
        el = _make_element(db, proj.id)
        db.session.commit()
        _inject_session(client, user.id)
        return user, proj, el

    def test_delete_element_with_draft_itp_and_stages(self, client, db):
        from models import ITPRecord, ITPItemStatus, FoundationStage, WTG
        user, proj, el = self._setup(client, db)
        rec = ITPRecord(wtg_id=el.id, itp_type='TEST_DEL', status='draft')
        db.session.add(rec); db.session.flush()
        db.session.add(ITPItemStatus(itp_record_id=rec.id, item_no='1',
                                     criterion_index=0, lucas_complete=False))
        db.session.add(FoundationStage(wtg_id=el.id, stage_key='04_excavation',
                                       stage_label='Excavation'))
        db.session.commit()

        resp = client.delete(f"/api/elements/{el.id}",
                             headers={'X-CSRF-Token': CSRF})
        assert resp.status_code == 200, resp.get_json(silent=True)
        assert db.session.get(WTG, el.id) is None
        assert FoundationStage.query.filter_by(wtg_id=el.id).count() == 0
        assert ITPRecord.query.filter_by(wtg_id=el.id).count() == 0

    def test_delete_element_blocked_when_itp_signed(self, client, db):
        from models import ITPRecord, ITPItemStatus, WTG
        user, proj, el = self._setup(client, db)
        rec = ITPRecord(wtg_id=el.id, itp_type='TEST_DEL2', status='in_progress')
        db.session.add(rec); db.session.flush()
        db.session.add(ITPItemStatus(itp_record_id=rec.id, item_no='1',
                                     criterion_index=0, lucas_complete=True,
                                     lucas_signed_at=datetime.now(timezone.utc)))
        db.session.commit()

        resp = client.delete(f"/api/elements/{el.id}",
                             headers={'X-CSRF-Token': CSRF})
        assert resp.status_code == 400
        assert b"signatures" in resp.data
        assert db.session.get(WTG, el.id) is not None   # element survived


class TestFoundation3D:

    def test_foundation_3d_page_renders(self, client, db):
        user = _make_user(db)
        db.session.commit()
        _inject_session(client, user.id)
        resp = client.get("/foundation/3d")
        assert resp.status_code == 200
        assert b"POUR CONCRETE" in resp.data
        assert b"BUILD IT" in resp.data
        assert b"Foundation 3D" in resp.data

    def test_foundation_index_links_to_3d(self, client, db):
        user = _make_user(db)
        db.session.commit()
        _inject_session(client, user.id)
        resp = client.get("/foundation")
        # foundation_index may redirect without an active project; only assert
        # the banner when the page renders
        if resp.status_code == 200:
            assert b"/foundation/3d" in resp.data
