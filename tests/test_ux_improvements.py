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
