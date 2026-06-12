import os, json
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timezone

db = SQLAlchemy()

# ─────────────────────────────────────────────
# USERS
# ─────────────────────────────────────────────
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(100), nullable=False)
    email        = db.Column(db.String(150), unique=True, nullable=False)
    password     = db.Column(db.String(256), nullable=False)
    role         = db.Column(db.String(30), nullable=False, default='engineer')
    position     = db.Column(db.String(100), default='')   # job title e.g. "QA Engineer"
    company      = db.Column(db.String(100), default='')
    avatar_color = db.Column(db.String(20),  default='#4f46e5')
    created_at   = db.Column(db.DateTime,    default=lambda: datetime.now(timezone.utc))

    # ── Phase 1: identity / security fields ──────────────────────────────────
    # is_active shadows Flask-Login's UserMixin.is_active property so that the
    # database value controls whether the account can log in.  The run_migrations()
    # backfill ensures every existing row has TRUE so no one is locked out.
    is_active           = db.Column(db.Boolean,  default=True)
    email_verified      = db.Column(db.Boolean,  default=False)
    email_verified_at   = db.Column(db.DateTime, nullable=True)
    last_login_at       = db.Column(db.DateTime, nullable=True)
    password_changed_at = db.Column(db.DateTime, nullable=True)
    failed_login_count  = db.Column(db.Integer,  default=0)
    locked_until        = db.Column(db.DateTime, nullable=True)

    def can_enter_data(self):
        return self.role in ('engineer', 'admin')

    def can_view_only(self):
        return self.role in ('supervisor', 'manager', 'client')

    def is_manager_or_above(self):
        return self.role in ('manager', 'supervisor', 'admin')


# Access levels available when adding someone to a project
PROJECT_ACCESS_LEVELS = [
    ('admin',    'Admin',          'fa-shield-halved',       '#ef4444', 'Manage team, settings & all data'),
    ('lead',     'Lead',           'fa-hard-hat',            '#4f46e5', 'Enter & approve all data, manage setup'),
    ('engineer', 'Engineer',       'fa-screwdriver-wrench',  '#22c55e', 'Enter field data and test records'),
    ('viewer',   'Viewer',         'fa-eye',                 '#64748b', 'View and export — no editing'),
    ('client',   'Client',         'fa-handshake',           '#0891b2', 'View approved reports and sign-offs only'),
]

# ═══════════════════════════════════════════════════════════════════
# ACCESS CONTROL CENTRE — Phase AC-1
# New constants + models coexist alongside old PROJECT_ACCESS_LEVELS
# and ProjectMember. Old names not renamed — app.py imports unchanged.
# ═══════════════════════════════════════════════════════════════════

AC_ACCESS_LEVELS = [
    ('owner',           'Owner',           'fa-crown',              '#f59e0b', 'Full authority. Protected by the last-owner rule.'),
    ('admin',           'Admin',           'fa-shield-halved',      '#ef4444', 'Project-level administrator. All management permissions on by default.'),
    ('project_manager', 'Project Manager', 'fa-user-tie',           '#7c3aed', 'Manages project, team access, and all data. Control Centre on by default.'),
    ('qa_manager',      'QA Manager',      'fa-clipboard-check',    '#4f46e5', 'Manages quality records, ITPs, testing, and sign-offs.'),
    ('engineer',        'Engineer',        'fa-screwdriver-wrench', '#22c55e', 'Field data entry, ITP signing, testing, and document uploads.'),
    ('supervisor',      'Supervisor',      'fa-hard-hat',           '#f59e0b', 'View-only with document upload access.'),
    ('subcontractor',   'Subcontractor',   'fa-tools',              '#7c3aed', 'Limited data entry and evidence submission.'),
    ('client',          'Client',          'fa-handshake',          '#0891b2', 'View, export, and ITP client review via token link.'),
    ('viewer',          'Viewer',          'fa-eye',                '#64748b', 'Read-only. No data entry.'),
]

PERMISSION_GROUPS = [
    ('access_management', 'Access Management', 'fa-shield-halved', '#ef4444', [
        ('can_manage_access',  'Manage Access'),
        ('can_invite_members', 'Invite Members'),
        ('can_remove_members', 'Remove Members'),
        ('can_view_audit_log', 'View Audit Log'),
    ]),
    ('project_setup', 'Project Setup', 'fa-gear', '#7c3aed', [
        ('can_manage_project_settings', 'Edit Project Settings'),
        ('can_manage_hierarchy',        'Manage Hierarchy'),
        ('can_manage_companies',        'Manage Companies'),
        ('can_manage_map',              'Manage Map'),
    ]),
    ('itps', 'ITPs', 'fa-clipboard-list', '#4f46e5', [
        ('can_create_itp',      'Create ITP'),
        ('can_sign_itp',        'Sign ITP Criteria'),
        ('can_attach_itp_docs', 'Attach ITP Documents'),
        ('can_send_itp_invite', 'Send Client Invite'),
        ('can_reopen_itp',      'Reopen ITP'),
        ('can_delete_itp',      'Delete ITP'),
        ('can_view_itp',        'View ITP'),
        ('can_review_itp',      'Review ITP Items'),
        ('can_sign_client_itp', 'Sign ITP as Client'),
    ]),
    ('field_data', 'Field Data', 'fa-pen-to-square', '#22c55e', [
        ('can_record_tests',           'Record Tests'),
        ('can_upload_test_photos',     'Upload Test Photos'),
        ('can_record_proof_rolls',     'Record Proof Rolls'),
        ('can_update_foundation',      'Update Foundation'),
        ('can_upload_foundation_docs', 'Upload Foundation Docs'),
    ]),
    ('documents', 'Documents', 'fa-folder-open', '#f59e0b', [
        ('can_upload_documents', 'Upload Documents'),
        ('can_delete_documents', 'Delete Documents'),
        ('can_manage_folders',   'Manage Folders'),
    ]),
    ('analytics', 'Analytics', 'fa-chart-line', '#22d3ee', [
        ('can_manage_widgets', 'Manage Widgets'),
    ]),
]

PERMISSION_KEYS   = [key for _, _, _, _, perms in PERMISSION_GROUPS for key, _ in perms]
PERMISSION_LABELS = {key: label for _, _, _, _, perms in PERMISSION_GROUPS for key, label in perms}
_ALL_AC_PERM_KEYS = frozenset(PERMISSION_KEYS)

DEFAULT_PERMISSIONS = {
    'owner': _ALL_AC_PERM_KEYS,
    'admin': _ALL_AC_PERM_KEYS,
    'project_manager': frozenset({
        'can_manage_access', 'can_invite_members', 'can_remove_members', 'can_view_audit_log',
        'can_manage_hierarchy', 'can_manage_companies', 'can_manage_map',
        'can_create_itp', 'can_sign_itp', 'can_attach_itp_docs',
        'can_send_itp_invite', 'can_reopen_itp', 'can_delete_itp',
        'can_view_itp',
        'can_record_tests', 'can_upload_test_photos', 'can_record_proof_rolls',
        'can_update_foundation', 'can_upload_foundation_docs',
        'can_upload_documents', 'can_delete_documents', 'can_manage_folders',
        'can_manage_widgets',
    }),
    'qa_manager': frozenset({
        'can_view_audit_log', 'can_manage_hierarchy',
        'can_create_itp', 'can_sign_itp', 'can_attach_itp_docs',
        'can_send_itp_invite', 'can_reopen_itp',
        'can_view_itp',
        'can_record_tests', 'can_upload_test_photos', 'can_record_proof_rolls',
        'can_update_foundation', 'can_upload_foundation_docs',
        'can_upload_documents', 'can_delete_documents', 'can_manage_folders',
        'can_manage_widgets',
    }),
    'engineer': frozenset({
        'can_sign_itp', 'can_attach_itp_docs',
        'can_view_itp',
        'can_record_tests', 'can_upload_test_photos', 'can_record_proof_rolls',
        'can_update_foundation', 'can_upload_foundation_docs',
        'can_upload_documents', 'can_manage_widgets',
    }),
    'supervisor':    frozenset({'can_upload_documents', 'can_view_itp'}),
    'subcontractor': frozenset({
        'can_attach_itp_docs',
        'can_view_itp',
        'can_record_tests', 'can_upload_test_photos', 'can_record_proof_rolls',
        'can_upload_documents',
    }),
    'client': frozenset({'can_view_itp', 'can_review_itp', 'can_sign_client_itp'}),
    'viewer': frozenset({'can_view_itp'}),
}

LOCKED_PERMISSIONS = {
    level_key: (_ALL_AC_PERM_KEYS if level_key == 'owner' else frozenset())
    for level_key, *_ in AC_ACCESS_LEVELS
}

# ─────────────────────────────────────────────
# PROJECTS  (multi-project support)
# ─────────────────────────────────────────────
PROJECT_TYPES = [
    'Wind Farm', 'Solar Farm', 'Civil Construction', 'Road Works',
    'Earthworks', 'Mining', 'Infrastructure', 'Other',
]
PROJECT_STATUSES = [
    ('planning',   'Planning',   '#f59e0b'),
    ('active',     'Active',     '#22c55e'),
    ('on_hold',    'On Hold',    '#f97316'),
    ('completed',  'Completed',  '#6366f1'),
    ('archived',   'Archived',   '#94a3b8'),
]
ALL_FEATURES = [
    ('map',              'Map & Geofencing',   'fa-map-location-dot',  '#34d399'),
    ('proof_rolling',    'Proof Rolling',      'fa-file-circle-check', '#22c55e'),
    ('geo_testing',      'Geo Testing Records','fa-microscope',        '#6ee7b7'),
    ('itp',              'ITPs',               'fa-clipboard-list',    '#a78bfa'),
    ('foundation',       'Foundation Tracker', 'fa-layer-group',       '#fb923c'),
    ('progress_tracker', 'Analytics',          'fa-chart-line',        '#22d3ee'),
    ('documents',        'Documents',          'fa-folder-open',       '#f59e0b'),
    ('daily_report',     'Daily Report',       'fa-clipboard-user',    '#38bdf8'),
    ('site_capture',     'Daily Site Capture', 'fa-camera-retro',      '#c084fc'),
    ('roster',           'Roster',             'fa-users',             '#e879f9'),
]

class Project(db.Model):
    __tablename__ = 'projects'
    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(200), nullable=False)
    project_type  = db.Column(db.String(100), default='Wind Farm')
    location      = db.Column(db.String(300))
    postcode      = db.Column(db.String(20))
    status        = db.Column(db.String(30), default='active')
    client_name   = db.Column(db.String(200))
    contract_ref  = db.Column(db.String(100))
    start_date    = db.Column(db.Date)
    end_date      = db.Column(db.Date)
    color         = db.Column(db.String(20), default='#0f2942')
    description   = db.Column(db.Text)
    created_by    = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    is_active     = db.Column(db.Boolean, default=True)

    members  = db.relationship('ProjectMember',  backref='project', lazy=True, cascade='all,delete')
    features = db.relationship('ProjectFeature', backref='project', lazy=True, cascade='all,delete')
    wtgs     = db.relationship('WTG', backref='project', lazy=True)

    @property
    def status_label(self):
        return next((s[1] for s in PROJECT_STATUSES if s[0]==self.status), self.status.title())

    @property
    def status_color(self):
        return next((s[2] for s in PROJECT_STATUSES if s[0]==self.status), '#94a3b8')

    def feature_enabled(self, key):
        feat = next((f for f in self.features if f.feature_key == key), None)
        return feat.enabled if feat else True  # default on

    @property
    def enabled_features(self):
        return {f.feature_key: f.enabled for f in self.features}

    @property
    def completion_pct(self):
        if not self.wtgs:
            return 0
        return round(sum(w.completion_pct for w in self.wtgs) / len(self.wtgs))


# Legacy compatibility only. New people/access flows use ProjectMemberAC.
# This table is kept for backward-compatible queries (e.g. old report generators
# that still join project_members). Do NOT add new UI flows on top of this table.
class ProjectMember(db.Model):
    __tablename__ = 'project_members'
    id          = db.Column(db.Integer, primary_key=True)
    project_id  = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'),    nullable=False)
    proj_role   = db.Column(db.String(30), default='member')  # lead | member | viewer
    added_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user        = db.relationship('User', backref='project_memberships', lazy='joined')


class ProjectFeature(db.Model):
    __tablename__ = 'project_features'
    id          = db.Column(db.Integer, primary_key=True)
    project_id  = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    feature_key = db.Column(db.String(50), nullable=False)
    enabled     = db.Column(db.Boolean, default=True)


class ProjectMapFile(db.Model):
    """One KML/KMZ map file uploaded per project."""
    __tablename__ = 'project_map_files'
    id           = db.Column(db.Integer, primary_key=True)
    project_id   = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False, unique=True)
    filename     = db.Column(db.String(300))
    geojson_data = db.Column(db.Text)   # full parsed GeoJSON as JSON string
    layer_names  = db.Column(db.Text)   # JSON list of layer names found in file
    uploaded_by  = db.Column(db.Integer, db.ForeignKey('users.id'))
    uploaded_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# ─────────────────────────────────────────────
# ELEMENT GROUPS  (engineer-defined groupings)
# ─────────────────────────────────────────────
ELEMENT_TYPES = [
    ('wtg',          'Wind Turbine (WTG)'),
    ('access_track', 'Access Track'),
    ('hardstand',    'Hardstand'),
    ('crane_pad',    'Crane Pad'),
    ('substation',   'Substation'),
    ('other',        'Other'),
]

class WTGGroup(db.Model):
    __tablename__ = 'wtg_groups'
    id         = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    name       = db.Column(db.String(100), nullable=False)
    color      = db.Column(db.String(20), default='#0f2942')
    sort_order = db.Column(db.Integer, default=0)

    elements      = db.relationship('WTG', backref='group', lazy=True)
    work_packages = db.relationship('WorkPackage', backref='group', lazy=True)

# ─────────────────────────────────────────────
# WORK PACKAGES  (level 3: assigned to a Group)
# ─────────────────────────────────────────────
class WorkPackage(db.Model):
    __tablename__ = 'work_packages'
    id         = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    group_id   = db.Column(db.Integer, db.ForeignKey('wtg_groups.id'), nullable=True)
    name       = db.Column(db.String(100), nullable=False)
    color      = db.Column(db.String(20), default='#7c3aed')
    icon       = db.Column(db.String(40), default='layer-group')
    sort_order = db.Column(db.Integer, default=0)

    elements   = db.relationship('WTG', backref='work_package', lazy=True)

# ─────────────────────────────────────────────
# WIND TURBINE GENERATORS  (elements)
# ─────────────────────────────────────────────
class WTG(db.Model):
    __tablename__ = 'wtgs'
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(50), nullable=False)
    easting         = db.Column(db.Float, nullable=True)
    northing        = db.Column(db.Float, nullable=True)
    status          = db.Column(db.String(20), default='in_progress')
    project_id      = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    group_id        = db.Column(db.Integer, db.ForeignKey('wtg_groups.id'), nullable=True)
    work_package_id = db.Column(db.Integer, db.ForeignKey('work_packages.id'), nullable=True)
    element_type    = db.Column(db.String(30), default='wtg')

    areas        = db.relationship('Area', backref='wtg', lazy=True, cascade='all,delete')

    @property
    def element_type_label(self):
        return next((lbl for key, lbl in ELEMENT_TYPES if key == self.element_type), self.element_type.replace('_', ' ').title())

    @property
    def completion_pct(self):
        total = sum(len(a.required_tests) for a in self.areas)
        done  = sum(sum(1 for t in a.required_tests if t.is_complete) for a in self.areas)
        return round((done / total * 100) if total else 0)

# ─────────────────────────────────────────────
# AREAS
# ─────────────────────────────────────────────
class Area(db.Model):
    __tablename__ = 'areas'
    id           = db.Column(db.Integer, primary_key=True)
    wtg_id       = db.Column(db.Integer, db.ForeignKey('wtgs.id'), nullable=False)
    area_type    = db.Column(db.String(30), nullable=False)
    label        = db.Column(db.String(50), nullable=False)

    required_tests = db.relationship('QATest',     backref='area', lazy=True, cascade='all,delete')
    activities     = db.relationship('Activity',   backref='area', lazy=True, cascade='all,delete')

    @property
    def completion_pct(self):
        total = len(self.required_tests)
        done  = sum(1 for t in self.required_tests if t.is_complete)
        return round((done / total * 100) if total else 0)

    @property
    def status_color(self):
        pct = self.completion_pct
        if pct == 0:   return 'red'
        if pct == 100: return 'green'
        return 'yellow'

# ─────────────────────────────────────────────
# ACTIVITIES  (level 6: assigned to an Area)
# Core scheduling/tracking unit — all future tools are built on this
# ─────────────────────────────────────────────
ACTIVITY_TYPES = [
    ('proof_roll',     'Proof Roll'),
    ('dcp',            'DCP Test'),
    ('compaction',     'Compaction Test'),
    ('plate_load',     'Plate Load Test'),
    ('geo_inspection', 'Geo Inspection'),
    ('earthworks',     'Earthworks'),
    ('concrete',       'Concrete Works'),
    ('survey',         'Survey'),
    ('cable',          'Cable Installation'),
    ('general',        'General'),
]

class Activity(db.Model):
    __tablename__ = 'activities'
    id            = db.Column(db.Integer, primary_key=True)
    area_id       = db.Column(db.Integer, db.ForeignKey('areas.id'), nullable=False)
    name          = db.Column(db.String(100), nullable=False)
    activity_type = db.Column(db.String(50), default='general')
    status        = db.Column(db.String(20), default='not_started')  # not_started|in_progress|complete
    sort_order    = db.Column(db.Integer, default=0)
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def status_color(self):
        return {'not_started':'#e2e8f0','in_progress':'#fde047','complete':'#86efac'}.get(self.status,'#e2e8f0')

    @property
    def status_label(self):
        return {'not_started':'Not Started','in_progress':'In Progress','complete':'Complete'}.get(self.status, self.status.title())

    @property
    def type_label(self):
        return next((lbl for k, lbl in ACTIVITY_TYPES if k == self.activity_type), self.activity_type.replace('_',' ').title())


# ─────────────────────────────────────────────
# QA TESTS
# ─────────────────────────────────────────────
class QATest(db.Model):
    __tablename__ = 'qa_tests'
    id          = db.Column(db.Integer, primary_key=True)
    area_id     = db.Column(db.Integer, db.ForeignKey('areas.id'), nullable=False)
    test_type   = db.Column(db.String(50), nullable=False)
    layer       = db.Column(db.String(30), nullable=True)
    is_complete = db.Column(db.Boolean, default=False)
    completed_at= db.Column(db.DateTime, nullable=True)
    completed_by= db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    records     = db.relationship('TestRecord',      backref='qa_test', lazy=True, cascade='all,delete')
    proof_rolls = db.relationship('ProofRollRecord', backref='qa_test', lazy=True, cascade='all,delete')
    photos      = db.relationship('TestPhoto',       backref='qa_test', lazy=True, cascade='all,delete')

    @property
    def display_name(self):
        names = {
            'dcp':                    'DCP Test',
            'subgrade_compaction':    'Subgrade Compaction',
            'subbase_compaction':     'Subbase Compaction',
            'basecourse_compaction':  'Basecourse Compaction',
            'proof_roll_foundation':  'Proof Roll – Foundation',
            'proof_roll_subgrade':    'Proof Roll – Subgrade',
            'proof_roll_subbase':     'Proof Roll – Subbase',
            'proof_roll_basecourse':  'Proof Roll – Basecourse',
            'plate_load_test':        'Plate Load Test',
        }
        return names.get(self.test_type, self.test_type.replace('_', ' ').title())

# ─────────────────────────────────────────────
# TEST RECORDS
# ─────────────────────────────────────────────
class TestRecord(db.Model):
    __tablename__ = 'test_records'
    id              = db.Column(db.Integer, primary_key=True)
    qa_test_id      = db.Column(db.Integer, db.ForeignKey('qa_tests.id'), nullable=False)
    test_date       = db.Column(db.Date, nullable=False)
    lot_number      = db.Column(db.String(50))
    lab_ref         = db.Column(db.String(100))
    result          = db.Column(db.String(20))
    result_value    = db.Column(db.Float, nullable=True)
    result_unit     = db.Column(db.String(20), nullable=True)
    spec_value      = db.Column(db.Float, nullable=True)
    comments        = db.Column(db.Text)
    attachments     = db.Column(db.Text)
    entered_by      = db.Column(db.Integer, db.ForeignKey('users.id'))
    entered_at      = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    signature_data  = db.Column(db.Text)

# ─────────────────────────────────────────────
# PROOF ROLL RECORDS
# ─────────────────────────────────────────────
class ProofRollRecord(db.Model):
    __tablename__ = 'proof_roll_records'
    id              = db.Column(db.Integer, primary_key=True)
    qa_test_id      = db.Column(db.Integer, db.ForeignKey('qa_tests.id'), nullable=False)
    location        = db.Column(db.String(200))
    date            = db.Column(db.Date, nullable=False)
    pavement_area   = db.Column(db.String(100))
    pavement_material = db.Column(db.String(100))
    material_layer  = db.Column(db.String(100))
    lot_number      = db.Column(db.String(50))
    area_sketch     = db.Column(db.Text)
    tandem_tonnes_per_wheel = db.Column(db.Float)
    tandem_passes           = db.Column(db.Integer)
    vibrating_mass_tonnes   = db.Column(db.Float)
    vibrating_passes        = db.Column(db.Integer)
    other_equipment         = db.Column(db.String(100))
    other_value             = db.Column(db.String(50))
    other_passes            = db.Column(db.Integer)
    comments        = db.Column(db.Text)
    rectification_method    = db.Column(db.Text)
    rectification_date      = db.Column(db.Date, nullable=True)
    passed                  = db.Column(db.String(5))
    entered_by      = db.Column(db.Integer, db.ForeignKey('users.id'))
    entered_at      = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    signatories     = db.relationship('ProofRollSignatory',  backref='proof_roll',     lazy=True, cascade='all,delete')
    equipment_rows  = db.relationship('ProofRollEquipment',  backref='proof_roll_rec', lazy=True, cascade='all,delete')
    pr_photos       = db.relationship('ProofRollPhoto',      backref='proof_roll_rec',      lazy=True, cascade='all,delete')
    rect_photos     = db.relationship('ProofRollRectPhoto',  backref='proof_roll_rect_rec', lazy=True, cascade='all,delete')

class ProofRollSignatory(db.Model):
    __tablename__ = 'proof_roll_signatories'
    id              = db.Column(db.Integer, primary_key=True)
    proof_roll_id   = db.Column(db.Integer, db.ForeignKey('proof_roll_records.id'), nullable=False)
    name            = db.Column(db.String(100))
    company         = db.Column(db.String(100))
    signature_data  = db.Column(db.Text)
    signed_date     = db.Column(db.Date)
    role            = db.Column(db.String(50))

class ProofRollEquipment(db.Model):
    """One row of equipment used during a proof roll (replaces fixed tandem/vibrating fields)."""
    __tablename__ = 'proof_roll_equipment'
    id              = db.Column(db.Integer, primary_key=True)
    proof_roll_id   = db.Column(db.Integer, db.ForeignKey('proof_roll_records.id'), nullable=False)
    equipment_name  = db.Column(db.String(150))
    mass_tonnes     = db.Column(db.String(50))
    value           = db.Column(db.String(50))
    passes          = db.Column(db.String(20))
    sort_order      = db.Column(db.Integer, default=0)

class ProofRollPhoto(db.Model):
    """Site photos taken during a proof roll — stored as compressed base64."""
    __tablename__ = 'proof_roll_photos'
    id              = db.Column(db.Integer, primary_key=True)
    proof_roll_id   = db.Column(db.Integer, db.ForeignKey('proof_roll_records.id'), nullable=False)
    image_data      = db.Column(db.Text)          # base64 data URI (compressed JPEG)
    caption         = db.Column(db.String(200), default='')
    taken_at        = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    uploaded_by     = db.Column(db.Integer, db.ForeignKey('users.id'))

class ProofRollRectPhoto(db.Model):
    """Rectification photos — evidence of works done to fix failed areas."""
    __tablename__ = 'proof_roll_rect_photos'
    id              = db.Column(db.Integer, primary_key=True)
    proof_roll_id   = db.Column(db.Integer, db.ForeignKey('proof_roll_records.id'), nullable=False)
    image_data      = db.Column(db.Text)          # base64 data URI (compressed JPEG)
    caption         = db.Column(db.String(200), default='')
    taken_at        = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    uploaded_by     = db.Column(db.Integer, db.ForeignKey('users.id'))

class TempPhotoUpload(db.Model):
    """Staging table — photos uploaded before the proof roll form is saved.
    One record per photo, linked by owner.  Migrated to ProofRollPhoto /
    ProofRollRectPhoto on form submit then deleted immediately."""
    __tablename__ = 'temp_photo_uploads'
    id           = db.Column(db.Integer, primary_key=True)
    photo_type   = db.Column(db.String(10), default='site')   # 'site' | 'rect'
    image_data   = db.Column(db.Text, nullable=False)
    taken_at     = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    uploaded_by  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

# ─────────────────────────────────────────────
# TEST PHOTOS
# ─────────────────────────────────────────────
class TestPhoto(db.Model):
    __tablename__ = 'test_photos'
    id          = db.Column(db.Integer, primary_key=True)
    qa_test_id  = db.Column(db.Integer, db.ForeignKey('qa_tests.id'), nullable=False)
    file_path   = db.Column(db.String(300), nullable=False)
    thumb_path  = db.Column(db.String(300), nullable=True)
    caption     = db.Column(db.String(200), default='')
    taken_date  = db.Column(db.Date, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    uploaded_by = db.Column(db.Integer, db.ForeignKey('users.id'))

    @property
    def url(self):
        return f'/static/{self.file_path}'

    @property
    def filename(self):
        return os.path.basename(self.file_path) if self.file_path else ''

# ─────────────────────────────────────────────
# ITP RECORDS  (one per WTG per ITP type)
# ─────────────────────────────────────────────
# ═══════════════════════════════════════════════
# PROJECT ITP TEMPLATES  (new-style, per-project)
# ═══════════════════════════════════════════════
class ProjectITPTemplate(db.Model):
    """Project-specific ITP definition — replaces hardcoded ITP_DEFINITIONS for new projects."""
    __tablename__ = 'project_itp_templates'
    id              = db.Column(db.Integer, primary_key=True)
    project_id      = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    itp_number      = db.Column(db.String(20), default='01')
    name            = db.Column(db.String(200), nullable=False)
    revision        = db.Column(db.String(10),  default='A')
    date            = db.Column(db.String(20),  default='')
    works           = db.Column(db.String(200), default='')
    spec            = db.Column(db.String(200), default='')
    scope           = db.Column(db.Text,        default='')
    prepared_by     = db.Column(db.String(100), default='')
    approved_by     = db.Column(db.String(100), default='')
    # JSON list of item dicts: [{no, activity, criteria, rows, lucas_codes, client_codes, hold_witness}]
    items_json      = db.Column(db.Text, default='[]')
    # JSON list of scope selections: [{type: group|wp|element|area, id, name}]
    applicable_scope_json = db.Column(db.Text, default='[]')
    is_active       = db.Column(db.Boolean, default=True)
    created_at      = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    created_by_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    project         = db.relationship('Project', backref='itp_templates', lazy=True)

    # ── JSON accessors ──
    @property
    def items(self):
        try: return json.loads(self.items_json or '[]')
        except: return []

    @items.setter
    def items(self, val):
        self.items_json = json.dumps(val)

    @property
    def applicable_scope(self):
        try: return json.loads(self.applicable_scope_json or '[]')
        except: return []

    @applicable_scope.setter
    def applicable_scope(self, val):
        self.applicable_scope_json = json.dumps(val)

    def to_dict(self):
        """Return same structure as ITP_DEFINITIONS values so itp_detail.html works unchanged."""
        return {
            'itp_number':   self.itp_number,
            'name':         self.name,
            'revision':     self.revision,
            'date':         self.date or '',
            'works':        self.works or '',
            'spec':         self.spec or '',
            'scope':        self.scope or '',
            'prepared_by':  self.prepared_by or '',
            'approved_by':  self.approved_by or '',
            'items':        self.items,
        }

    @property
    def itp_type_key(self):
        """Unique key for ITPRecord.itp_type column."""
        return f'PROJ_{self.id}'


class ITPRecord(db.Model):
    __tablename__ = 'itp_records'
    id              = db.Column(db.Integer, primary_key=True)
    wtg_id          = db.Column(db.Integer, db.ForeignKey('wtgs.id'), nullable=False)
    itp_type        = db.Column(db.String(20), nullable=False)   # ITP02 | ITP03 | PROJ_<tid>
    project_itp_template_id = db.Column(db.Integer, db.ForeignKey('project_itp_templates.id'), nullable=True)
    lot_number      = db.Column(db.String(50))
    location        = db.Column(db.String(200))
    created_at      = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    created_by      = db.Column(db.Integer, db.ForeignKey('users.id'))

    # Engineer sign-off
    engineer_name       = db.Column(db.String(100))
    engineer_company    = db.Column(db.String(100), default='')
    engineer_signature  = db.Column(db.Text)           # base64
    engineer_signed_at  = db.Column(db.DateTime, nullable=True)

    # Status lifecycle:
    # draft → in_progress → client_invited → client_reviewing →
    #   client_commented → complete → reopened → superseded
    status = db.Column(db.String(30), default='draft')

    # Reopen / revision tracking
    revision        = db.Column(db.Integer, default=0)
    reopened_at     = db.Column(db.DateTime, nullable=True)
    reopened_by_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    reopen_reason   = db.Column(db.Text, nullable=True)

    # Client invite
    client_name         = db.Column(db.String(100))
    client_email        = db.Column(db.String(150))
    client_company      = db.Column(db.String(100), default='')
    client_token        = db.Column(db.String(64), unique=True, nullable=True)
    client_invited_at   = db.Column(db.DateTime, nullable=True)
    client_signature    = db.Column(db.Text)
    client_signed_at    = db.Column(db.DateTime, nullable=True)

    wtg          = db.relationship('WTG',         backref='itp_records', lazy=True)
    item_statuses = db.relationship('ITPItemStatus', backref='itp_record', lazy=True, cascade='all,delete')
    criterion_notes = db.relationship('ITPCriterionNote', backref='itp_record_obj',
                                      foreign_keys='ITPCriterionNote.itp_record_id', lazy=True)

    @property
    def engineer_signed(self):
        return self.engineer_signed_at is not None

    @property
    def client_signed(self):
        return self.client_signed_at is not None

    @property
    def items_complete_count(self):
        return sum(1 for i in self.item_statuses if i.lucas_complete)

    @property
    def items_total_count(self):
        return len(self.item_statuses)

# ─────────────────────────────────────────────
# ITP ITEM STATUS  (one row per criterion / bullet point)
# ─────────────────────────────────────────────
class ITPItemStatus(db.Model):
    __tablename__ = 'itp_item_statuses'
    id               = db.Column(db.Integer, primary_key=True)
    itp_record_id    = db.Column(db.Integer, db.ForeignKey('itp_records.id'), nullable=False)
    item_no          = db.Column(db.String(10), nullable=False)   # '1','2','3'… (activity)
    criterion_index  = db.Column(db.Integer,    default=0)        # index within activity
    activity         = db.Column(db.String(200))
    criterion_text   = db.Column(db.Text)                         # the bullet-point text
    inspection_code  = db.Column(db.String(20), default='')
    frequency        = db.Column(db.String(100), default='')

    # Engineer sign-off (per criterion)
    lucas_complete   = db.Column(db.Boolean, default=False)
    lucas_signed_at  = db.Column(db.DateTime, nullable=True)   # date + time
    lucas_comments   = db.Column(db.Text)
    lucas_signature  = db.Column(db.Text)   # base64 PNG

    # Engineer identity at signing time (populated when criterion is signed)
    signed_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    signed_by_name    = db.Column(db.String(100), nullable=True)
    signed_by_company = db.Column(db.String(100), nullable=True)

    # Client sign-off (per criterion)
    client_complete  = db.Column(db.Boolean, default=False)
    client_signed_at = db.Column(db.DateTime, nullable=True)   # date + time
    client_comments  = db.Column(db.Text)
    client_signature = db.Column(db.Text)   # base64 PNG

    # Per-item client review (new — per-item Accept / Raise Concern)
    client_reviewed  = db.Column(db.Boolean, default=False)  # has client ticked this item
    client_accepted  = db.Column(db.Boolean, nullable=True)  # True=accepted, False=concern raised, None=not reviewed
    # Active actions: approved|rejected|request_changes|request_clarification
    # Legacy value: not_accepted (display as "Not Accepted (Legacy)" in read-only views)
    client_action    = db.Column(db.String(50), nullable=True)

    # Per-criterion client reviewer identity (Phase 2A)
    # Populated when a client reviews this criterion; cleared on reset.
    client_signed_by_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    client_signed_by_name    = db.Column(db.String(100), nullable=True)
    client_signed_by_company = db.Column(db.String(100), nullable=True)
    client_invite_id         = db.Column(db.Integer, db.ForeignKey('itp_client_invites.id'), nullable=True)
    client_review_cycle_id   = db.Column(db.Integer, db.ForeignKey('itp_review_cycles.id'), nullable=True)

    # Attached documents / photos per criterion
    documents        = db.relationship('ITPItemDocument', backref='item_status',
                                       cascade='all, delete-orphan', lazy='select')

    # Discussion notes (append-only)
    notes            = db.relationship('ITPCriterionNote', backref='item_status',
                                       cascade='all, delete-orphan', lazy='select')

    @property
    def lucas_date(self):
        """Backward-compat shortcut — returns just the date portion."""
        return self.lucas_signed_at.date() if self.lucas_signed_at else None

    @property
    def client_date(self):
        return self.client_signed_at.date() if self.client_signed_at else None


class ITPItemDocument(db.Model):
    """Document / photo attached to a single ITP criterion row."""
    __tablename__ = 'itp_item_documents'
    id              = db.Column(db.Integer, primary_key=True)
    item_status_id  = db.Column(db.Integer, db.ForeignKey('itp_item_statuses.id'), nullable=False)
    itp_record_id   = db.Column(db.Integer, db.ForeignKey('itp_records.id'), nullable=False)
    original_name   = db.Column(db.String(255))
    filename        = db.Column(db.String(255))
    url             = db.Column(db.String(500))
    doc_type        = db.Column(db.String(20), default='file')   # photo | pdf | file
    uploaded_by     = db.Column(db.Integer, db.ForeignKey('users.id'))
    uploaded_at     = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class ITPCriterionNote(db.Model):
    """Append-only discussion note on a single ITP criterion.

    Notes are permanent records — never deleted or modified after creation.
    Use parent_note_id for threaded replies.
    party: 'internal' (project member) | 'client' (client reviewer)
    """
    __tablename__ = 'itp_criterion_notes'
    id              = db.Column(db.Integer, primary_key=True)
    itp_record_id   = db.Column(db.Integer, db.ForeignKey('itp_records.id'), nullable=False)
    item_status_id  = db.Column(db.Integer, db.ForeignKey('itp_item_statuses.id'), nullable=True)
    item_no         = db.Column(db.String(10), nullable=False)   # denormalised for easy lookup
    criterion_index = db.Column(db.Integer,    nullable=False, default=0)
    # Author
    author_user_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    author_name     = db.Column(db.String(100), nullable=False)
    author_company  = db.Column(db.String(100), default='')
    party           = db.Column(db.String(20),  nullable=False, default='internal')  # internal | client
    # Content
    note_text       = db.Column(db.Text, nullable=False)
    created_at      = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    # Context
    review_cycle_id = db.Column(db.Integer, db.ForeignKey('itp_review_cycles.id'), nullable=True)
    related_action  = db.Column(db.String(50), nullable=True)   # approved|rejected|request_changes|…
    parent_note_id  = db.Column(db.Integer, db.ForeignKey('itp_criterion_notes.id'), nullable=True)

    author       = db.relationship('User', foreign_keys=[author_user_id], lazy=True)
    review_cycle = db.relationship('ITPReviewCycle', foreign_keys=[review_cycle_id], lazy=True)
    replies      = db.relationship('ITPCriterionNote',
                                   foreign_keys=[parent_note_id],
                                   backref=db.backref('parent', remote_side='ITPCriterionNote.id'),
                                   lazy=True)


# ═══════════════════════════════════════════════
# FOUNDATION TRACKING  (10 stages per WTG)
# Matches Procore folder structure
# ═══════════════════════════════════════════════
FOUNDATION_STAGES = [
    ('excavation',            'Excavation'),
    ('geo_inspection',        'Geo Inspection'),
    ('conduits_trenching',    'Conduits Trenching'),
    ('conduits_installation', 'Conduits Installation'),
    ('blinding',              'Blinding'),
    ('anchor_cage',           'Anchor Cage Installation'),
    ('reo_work',              'Reo Work'),
    ('formwork',              'Formwork'),
    ('prepour_inspection',    'Pre-Pour Inspection'),
    ('concrete_pour',         'Concrete Pour'),
    ('crack_repairs',         'Cracks Repairs'),
    ('post_pour_survey',      'Post Pour Survey'),
]

class FoundationStageTemplate(db.Model):
    """Master list of foundation stages — edit here once, applies to all WTGs."""
    __tablename__ = 'foundation_stage_templates'
    id         = db.Column(db.Integer, primary_key=True)
    stage_key  = db.Column(db.String(40), unique=True, nullable=False)
    stage_label= db.Column(db.String(120), nullable=False)
    sort_order = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {'id': self.id, 'stage_key': self.stage_key,
                'stage_label': self.stage_label, 'sort_order': self.sort_order}


class FoundationStage(db.Model):
    """One row per WTG per stage — tracks each foundation milestone."""
    __tablename__ = 'foundation_stages'
    id            = db.Column(db.Integer, primary_key=True)
    wtg_id        = db.Column(db.Integer, db.ForeignKey('wtgs.id'), nullable=False)
    stage_key     = db.Column(db.String(40), nullable=False)   # e.g. '04_excavation'
    stage_label   = db.Column(db.String(120))
    status        = db.Column(db.String(20), default='not_started')  # not_started | in_progress | complete | na
    date_completed= db.Column(db.Date, nullable=True)
    lot_number    = db.Column(db.String(50))
    reference_no  = db.Column(db.String(80))                   # TRN / report number
    notes         = db.Column(db.Text)
    entered_by    = db.Column(db.Integer, db.ForeignKey('users.id'))
    updated_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                              onupdate=lambda: datetime.now(timezone.utc))
    # JSON blob for stage-specific key results (flexible per stage)
    result_json   = db.Column(db.Text, default='{}')

    wtg           = db.relationship('WTG', backref='foundation_stages')
    documents     = db.relationship('FoundationDocument', backref='stage',
                                    cascade='all, delete-orphan', lazy='select')

    @property
    def status_color(self):
        return {'not_started':'#e2e8f0','in_progress':'#fde047',
                'complete':'#86efac','na':'#cbd5e1'}.get(self.status,'#e2e8f0')

    @property
    def result_data(self):
        import json
        try:    return json.loads(self.result_json or '{}')
        except: return {}


class FoundationDocument(db.Model):
    """Documents / photos attached to a foundation stage."""
    __tablename__ = 'foundation_documents'
    id            = db.Column(db.Integer, primary_key=True)
    stage_id      = db.Column(db.Integer, db.ForeignKey('foundation_stages.id'), nullable=False)
    file_path     = db.Column(db.String(300))
    original_name = db.Column(db.String(200))
    doc_type      = db.Column(db.String(30), default='document')  # document | photo
    uploaded_by   = db.Column(db.Integer, db.ForeignKey('users.id'))
    uploaded_at   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    caption       = db.Column(db.String(300))

    @property
    def url(self):
        return f'/static/{self.file_path}'

    @property
    def filename(self):
        return os.path.basename(self.file_path) if self.file_path else ''


# ═══════════════════════════════════════════════
# CUSTOM TRACKING FIELDS  (engineer-configurable)
# ═══════════════════════════════════════════════
class CustomTrackingField(db.Model):
    """Engineer-defined fields that appear on test record forms."""
    __tablename__ = 'custom_tracking_fields'
    id            = db.Column(db.Integer, primary_key=True)
    scope         = db.Column(db.String(50))   # test_type key OR 'foundation_<stage_key>'
    field_key     = db.Column(db.String(60), nullable=False)
    label         = db.Column(db.String(120), nullable=False)
    field_type    = db.Column(db.String(20), default='text')  # text|number|date|select|yesno
    unit          = db.Column(db.String(30))                  # e.g. t/m³, %, blows/100mm
    default_value = db.Column(db.String(200))                 # pre-fill hint
    spec_min      = db.Column(db.Float, nullable=True)        # pass threshold (min)
    spec_max      = db.Column(db.Float, nullable=True)        # pass threshold (max)
    options       = db.Column(db.Text)                        # comma-sep for select type
    required      = db.Column(db.Boolean, default=False)
    sort_order    = db.Column(db.Integer, default=0)
    created_by    = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# ═══════════════════════════════════════════════
# PROGRESS TRACKER WIDGETS  (dashboard)
# ═══════════════════════════════════════════════
# ═══════════════════════════════════════════════
# DOCUMENTS  (QA document library)
# ═══════════════════════════════════════════════
DOCUMENT_CATEGORIES = [
    ('geo_testing',     'Geo Testing',       'fa-microscope',          '#6ee7b7'),
    ('proof_rolling',   'Proof Rolling',     'fa-file-circle-check',   '#22c55e'),
    ('itp',             'ITP / Inspection',  'fa-clipboard-list',      '#a78bfa'),
    ('foundation',      'Foundation',        'fa-layer-group',         '#fb923c'),
    ('lab_report',      'Lab Report',        'fa-flask',               '#38bdf8'),
    ('certificate',     'Certificate',       'fa-certificate',         '#fbbf24'),
    ('drawing',         'Drawing / Plan',    'fa-pen-ruler',           '#f472b6'),
    ('correspondence',  'Correspondence',    'fa-envelope',            '#94a3b8'),
    ('general',         'General',           'fa-file',                '#64748b'),
]

DOCUMENT_LINK_TYPES = [
    ('wtg',          'WTG',               'fa-tower-broadcast'),
    ('qa_test',      'QA Test',           'fa-vial'),
    ('proof_roll',   'Proof Roll Record', 'fa-file-circle-check'),
    ('itp_record',   'ITP Record',        'fa-clipboard-list'),
    ('project',      'Project (General)', 'fa-folder'),
]
# Dict version for fast template lookups (key → (label, icon))
DOCUMENT_LINK_DICT = {k: (lbl, icon) for k, lbl, icon in DOCUMENT_LINK_TYPES}

class DocumentFolder(db.Model):
    """Hierarchical folder tree for the document library."""
    __tablename__ = 'document_folders'
    id         = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    parent_id  = db.Column(db.Integer, db.ForeignKey('document_folders.id'), nullable=True)
    name       = db.Column(db.String(200), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    children  = db.relationship('DocumentFolder',
                                 backref=db.backref('parent', remote_side='DocumentFolder.id'),
                                 lazy=True, cascade='all,delete')
    documents = db.relationship('Document', backref='folder', lazy=True,
                                 foreign_keys='Document.folder_id')
    creator   = db.relationship('User', foreign_keys=[created_by])

    @property
    def doc_count_recursive(self):
        """Count docs in this folder and all sub-folders (active only)."""
        count = sum(1 for d in self.documents if d.is_active)
        for child in self.children:
            count += child.doc_count_recursive
        return count


class Document(db.Model):
    __tablename__ = 'documents'
    id                = db.Column(db.Integer, primary_key=True)
    project_id        = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    folder_id         = db.Column(db.Integer, db.ForeignKey('document_folders.id'), nullable=True)
    title             = db.Column(db.String(300), nullable=False)
    description       = db.Column(db.Text)
    original_filename = db.Column(db.String(300), nullable=False)
    file_ext          = db.Column(db.String(10), nullable=False)   # pdf, docx, xlsx …
    file_size         = db.Column(db.Integer, default=0)           # raw bytes
    file_data         = db.Column(db.Text, nullable=True)           # base64 (legacy/fallback)
    file_key          = db.Column(db.String(500), nullable=True)   # R2 object key (new uploads)
    category          = db.Column(db.String(50), default='general')
    tags              = db.Column(db.String(500))                  # comma-separated
    uploaded_by       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    uploaded_at       = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    is_active         = db.Column(db.Boolean, default=True)

    @property
    def stored_in_r2(self) -> bool:
        return bool(self.file_key)

    links    = db.relationship('DocumentLink', backref='document', lazy=True, cascade='all,delete')
    uploader = db.relationship('User', foreign_keys=[uploaded_by])

    @property
    def file_size_display(self):
        s = self.file_size or 0
        if s < 1024:          return f'{s} B'
        if s < 1024 * 1024:   return f'{s/1024:.1f} KB'
        return f'{s/1024/1024:.1f} MB'

    @property
    def icon_class(self):
        return {
            'pdf':'fa-file-pdf','docx':'fa-file-word','doc':'fa-file-word',
            'xlsx':'fa-file-excel','xls':'fa-file-excel',
            'jpg':'fa-file-image','jpeg':'fa-file-image','png':'fa-file-image','gif':'fa-file-image',
            'txt':'fa-file-lines','csv':'fa-file-csv',
        }.get(self.file_ext.lower(), 'fa-file')

    @property
    def icon_color(self):
        return {
            'pdf':'#ef4444','docx':'#2563eb','doc':'#2563eb',
            'xlsx':'#16a34a','xls':'#16a34a',
            'jpg':'#8b5cf6','jpeg':'#8b5cf6','png':'#8b5cf6','gif':'#8b5cf6',
            'txt':'#64748b','csv':'#0891b2',
        }.get(self.file_ext.lower(), '#64748b')

    @property
    def category_label(self):
        return next((c[1] for c in DOCUMENT_CATEGORIES if c[0]==self.category), self.category.title())

    @property
    def category_color(self):
        return next((c[3] for c in DOCUMENT_CATEGORIES if c[0]==self.category), '#64748b')

    @property
    def can_preview(self):
        return self.file_ext.lower() in ('pdf','jpg','jpeg','png','gif')

    @property
    def mime_type(self):
        return {
            'pdf':'application/pdf',
            'docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'doc':'application/msword',
            'xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'xls':'application/vnd.ms-excel',
            'jpg':'image/jpeg','jpeg':'image/jpeg',
            'png':'image/png','gif':'image/gif',
            'txt':'text/plain','csv':'text/csv',
        }.get(self.file_ext.lower(), 'application/octet-stream')


class DocumentLink(db.Model):
    __tablename__ = 'document_links'
    id          = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('documents.id'), nullable=False)
    link_type   = db.Column(db.String(30), nullable=False)  # wtg|qa_test|proof_roll|itp_record|project
    link_id     = db.Column(db.Integer,    nullable=False)
    note        = db.Column(db.String(300))
    linked_by   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    linked_at   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    linker      = db.relationship('User', foreign_keys=[linked_by])


# ═══════════════════════════════════════════════
# IN-APP NOTIFICATIONS
# ═══════════════════════════════════════════════
class Notification(db.Model):
    __tablename__ = 'notifications'
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    type       = db.Column(db.String(30), default='info')
    # type values: info | success | warning | itp_signed | itp_invited
    title      = db.Column(db.String(200), nullable=False)
    message    = db.Column(db.Text, default='')
    url        = db.Column(db.String(500), default='')
    is_read    = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user       = db.relationship('User', backref='notifications', lazy=True)


# ═══════════════════════════════════════════════
# ITP CLIENT SIGNATORIES (multiple per record)
# ═══════════════════════════════════════════════
class ITPClientInvite(db.Model):
    __tablename__ = 'itp_client_invites'
    id          = db.Column(db.Integer, primary_key=True)
    record_id   = db.Column(db.Integer, db.ForeignKey('itp_records.id'), nullable=False)
    name        = db.Column(db.String(100), nullable=False)
    company     = db.Column(db.String(100), default='')
    email       = db.Column(db.String(150), default='')
    token       = db.Column(db.String(100), unique=True, nullable=False)
    invited_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at  = db.Column(db.DateTime, nullable=True)
    is_revoked  = db.Column(db.Boolean, default=False)
    revoked_at  = db.Column(db.DateTime, nullable=True)

    # Multi-signatory tracking fields
    user_id              = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    # status: pending_project_access | pending_review | viewed | signed | revoked | expired
    # (backward-compat: treat old 'pending' as 'pending_review' in code)
    status               = db.Column(db.String(30), default='pending_review')
    viewed_at            = db.Column(db.DateTime, nullable=True)
    signed_at            = db.Column(db.DateTime, nullable=True)
    signer_name          = db.Column(db.String(100), nullable=True)  # locked name at sign time
    signer_company       = db.Column(db.String(100), nullable=True)
    notification_sent_at = db.Column(db.DateTime, nullable=True)

    project_member_ac_id = db.Column(db.Integer, db.ForeignKey('project_members_ac.id'), nullable=True)
    invited_by_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    invited_by_name      = db.Column(db.String(100), nullable=True)
    invited_by_company   = db.Column(db.String(100), nullable=True)

    # Review cycle linkage (Area 4 — ITP review cycles)
    review_cycle_id = db.Column(db.Integer, db.ForeignKey('itp_review_cycles.id'), nullable=True)

    # Phase 2B — scoped criteria for this invite (JSON list of ITPItemStatus IDs)
    item_scope_json = db.Column(db.Text, nullable=True)

    @property
    def item_scope_ids(self):
        """List of ITPItemStatus IDs scoped to this invite (empty = all signed criteria)."""
        try:    return json.loads(self.item_scope_json or '[]')
        except: return []

    @item_scope_ids.setter
    def item_scope_ids(self, val):
        self.item_scope_json = json.dumps(val)

    record             = db.relationship('ITPRecord', backref='client_invites', lazy=True)
    user               = db.relationship('User', foreign_keys=[user_id], lazy=True)
    project_member_ac  = db.relationship('ProjectMemberAC', foreign_keys=[project_member_ac_id], lazy=True)
    invited_by_user    = db.relationship('User', foreign_keys=[invited_by_id], lazy=True)


# ═══════════════════════════════════════════════
# ITP REVIEW CYCLES  (Area 4 — explicit review cycles)
# ═══════════════════════════════════════════════
class ITPReviewCycle(db.Model):
    __tablename__ = 'itp_review_cycles'
    id                     = db.Column(db.Integer, primary_key=True)
    record_id              = db.Column(db.Integer, db.ForeignKey('itp_records.id'), nullable=False)
    cycle_number           = db.Column(db.Integer, nullable=False, default=1)
    revision               = db.Column(db.Integer, nullable=False, default=0)
    # status: open | awaiting_review | completed | reopened | superseded
    status                 = db.Column(db.String(20), nullable=False, default='open')
    opened_at              = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    opened_by_id           = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    completed_at           = db.Column(db.DateTime, nullable=True)
    completed_by_invite_id = db.Column(db.Integer, db.ForeignKey('itp_client_invites.id'), nullable=True)
    # Snapshot of ITPItemStatus ids assigned to this cycle (JSON list)
    assigned_criterion_ids = db.Column(db.Text, nullable=True)

    record  = db.relationship('ITPRecord', backref='review_cycles', lazy=True)
    invites = db.relationship(
        'ITPClientInvite',
        backref='review_cycle',
        foreign_keys='ITPClientInvite.review_cycle_id',
        lazy=True,
    )

    @property
    def assigned_ids(self):
        try:    return json.loads(self.assigned_criterion_ids or '[]')
        except: return []

    @assigned_ids.setter
    def assigned_ids(self, val):
        self.assigned_criterion_ids = json.dumps(val)


# ═══════════════════════════════════════════════
# ═══════════════════════════════════════════════
# PROJECT COMPANIES & TEAM STRUCTURE
# ═══════════════════════════════════════════════
COMPANY_TYPES = [
    ('main_contractor', 'Main Contractor', 'fa-building',        '#2563eb'),
    ('subcontractor',   'Subcontractor',   'fa-hard-hat',        '#7c3aed'),
    ('client',          'Client',          'fa-handshake',       '#0891b2'),
    ('consultant',      'Consultant',      'fa-user-tie',        '#f59e0b'),
    ('testing_lab',     'Testing Lab',     'fa-microscope',      '#22c55e'),
]

PROJECT_ROLES = [
    ('project_admin',          'Project Admin',       'fa-shield-halved',      '#ef4444'),
    ('project_manager',        'Project Manager',     'fa-user-tie',           '#7c3aed'),
    ('qa_manager',             'QA Manager',          'fa-clipboard-check',    '#4f46e5'),
    ('site_engineer',          'Site Engineer',       'fa-screwdriver-wrench', '#22c55e'),
    ('supervisor',             'Supervisor',          'fa-hard-hat',           '#f59e0b'),
    ('subcontractor_submitter','Subcontractor Rep',   'fa-file-circle-plus',   '#7c3aed'),
    ('client_reviewer',        'Client Reviewer',     'fa-eye',                '#0891b2'),
    ('client_approver',        'Client Approver',     'fa-pen-nib',            '#2563eb'),
    ('consultant',             'Consultant',          'fa-user-tie',           '#f97316'),
    ('testing_lab',            'Testing Lab',         'fa-microscope',         '#6ee7b7'),
    ('auditor',                'Auditor',             'fa-magnifying-glass',   '#94a3b8'),
]

# ITP review actions (4 active actions — not_accepted removed as available action)
ITP_REVIEW_ACTIONS = [
    ('approved',              'Approved',             'fa-check-circle',       '#16a34a', '#dcfce7'),
    ('rejected',              'Rejected',             'fa-times-circle',       '#dc2626', '#fee2e2'),
    ('request_changes',       'Request Changes',      'fa-pencil',             '#d97706', '#fef3c7'),
    ('request_clarification', 'Request Clarification','fa-question-circle',    '#0891b2', '#e0f2fe'),
]


class ProjectCompany(db.Model):
    """A company involved in a project (client, contractor, sub, consultant, lab)."""
    __tablename__ = 'project_companies'
    id            = db.Column(db.Integer, primary_key=True)
    project_id    = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    company_type  = db.Column(db.String(30), nullable=False)  # main_contractor|subcontractor|client|consultant|testing_lab
    name          = db.Column(db.String(200), nullable=False)
    short_name    = db.Column(db.String(50),  default='')
    contact_name  = db.Column(db.String(100), default='')
    contact_email = db.Column(db.String(150), default='')
    contact_phone = db.Column(db.String(50),  default='')
    notes         = db.Column(db.Text,        default='')
    added_at      = db.Column(db.DateTime,    default=lambda: datetime.now(timezone.utc))
    added_by      = db.Column(db.Integer,     db.ForeignKey('users.id'), nullable=True)

    project   = db.relationship('Project',          backref='companies',  lazy=True)
    members   = db.relationship('ProjectTeamMember', backref='company',   lazy=True,
                                 foreign_keys='ProjectTeamMember.company_id')

    @property
    def type_label(self):
        return next((lbl for k, lbl, *_ in COMPANY_TYPES if k == self.company_type),
                    self.company_type.replace('_', ' ').title())

    @property
    def type_color(self):
        return next((color for k, lbl, icon, color in COMPANY_TYPES if k == self.company_type), '#64748b')

    @property
    def type_icon(self):
        return next((icon for k, lbl, icon, color in COMPANY_TYPES if k == self.company_type), 'fa-building')


# Legacy compatibility only. New people/access flows use ProjectMemberAC.
# ProjectTeamMember was the pre-AC-phase named-person record. Kept for
# backward compatibility with company.members FK. Do NOT build new UI on this.
class ProjectTeamMember(db.Model):
    """A named person on the project — not necessarily a registered user."""
    __tablename__ = 'project_team_members'
    id            = db.Column(db.Integer, primary_key=True)
    project_id    = db.Column(db.Integer, db.ForeignKey('projects.id'),          nullable=False)
    company_id    = db.Column(db.Integer, db.ForeignKey('project_companies.id'), nullable=True)

    # Identity
    name          = db.Column(db.String(100), nullable=False)
    email         = db.Column(db.String(150), default='')
    position      = db.Column(db.String(100), default='')
    phone         = db.Column(db.String(50),  default='')

    # Role & permissions
    project_role  = db.Column(db.String(40),  nullable=False, default='site_engineer')
    can_sign      = db.Column(db.Boolean,     default=True)

    # Linked platform user account (optional)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    # Invitation / portal access
    invite_token  = db.Column(db.String(100), unique=True, nullable=True)
    invite_sent_at= db.Column(db.DateTime, nullable=True)
    invite_status = db.Column(db.String(20), default='pending')  # pending|accepted|expired
    token_expires = db.Column(db.DateTime, nullable=True)

    added_at      = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    added_by      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    is_active     = db.Column(db.Boolean, default=True)

    project       = db.relationship('Project', backref='team_members', lazy=True)
    user          = db.relationship('User', foreign_keys=[user_id],
                                    backref='team_memberships', lazy=True)

    @property
    def role_label(self):
        return next((lbl for k, lbl, *_ in PROJECT_ROLES if k == self.project_role),
                    self.project_role.replace('_', ' ').title())

    @property
    def role_color(self):
        return next((color for k, lbl, icon, color in PROJECT_ROLES if k == self.project_role), '#64748b')

    @property
    def role_icon(self):
        return next((icon for k, lbl, icon, color in PROJECT_ROLES if k == self.project_role), 'fa-user')


# ═══════════════════════════════════════════════
# AUDIT TRAIL
# ═══════════════════════════════════════════════
class AuditEvent(db.Model):
    """Immutable audit trail — one row per meaningful event."""
    __tablename__ = 'audit_events'
    id            = db.Column(db.Integer, primary_key=True)
    project_id    = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)

    # Actor snapshot (captured at time of event so it survives account changes)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    actor_name    = db.Column(db.String(100), default='')
    actor_email   = db.Column(db.String(150), default='')
    actor_company = db.Column(db.String(100), default='')
    actor_role    = db.Column(db.String(50),  default='')

    # Event type
    event_type    = db.Column(db.String(60), nullable=False)
    # Values: itp_item_signed | itp_item_client_reviewed | itp_client_invited |
    #         member_added | member_removed | company_added | company_removed |
    #         itp_submitted | itp_complete | document_uploaded | login

    # Entity affected
    entity_type   = db.Column(db.String(30), default='')   # itp_record|itp_item|member|company|document
    entity_id     = db.Column(db.Integer,    nullable=True)
    entity_label  = db.Column(db.String(200), default='')  # human-readable label at time of event

    # Extra structured detail (JSON blob)
    detail_json   = db.Column(db.Text, default='{}')
    ip_address    = db.Column(db.String(45), default='')

    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    project       = db.relationship('Project', backref='audit_events', lazy=True)
    actor         = db.relationship('User',    backref='audit_events', lazy=True)

    @property
    def detail(self):
        try:    return json.loads(self.detail_json or '{}')
        except: return {}


# ═══════════════════════════════════════════════
class ProgressWidget(db.Model):
    """User-configured chart/table widgets on the Progress Tracker page."""
    __tablename__ = 'progress_widgets'
    id            = db.Column(db.Integer, primary_key=True)
    title         = db.Column(db.String(120), nullable=False)
    widget_type   = db.Column(db.String(30), default='bar')  # bar|pie|line|table|ring
    data_source   = db.Column(db.String(50), default='wtg_completion')
                  # wtg_completion | test_type_breakdown | foundation_stages
                  # daily_tests | area_completion
    filter_json   = db.Column(db.Text, default='{}')   # optional filter (wtg, area, date range)
    color_scheme  = db.Column(db.String(20), default='default')
    sort_order    = db.Column(db.Integer, default=0)
    created_by    = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# ═══════════════════════════════════════════════════════════════════
# INVITE / AUTH TOKENS  (Phase 1 — no raw tokens stored)
# ═══════════════════════════════════════════════════════════════════

# Allowed status values for UserInvite.status
INVITE_STATUSES = ['pending', 'accepted', 'expired', 'revoked']


class UserInvite(db.Model):
    """
    Invite record for a person to join a project.

    Security contract:
      - Only the SHA-256 hex digest of the raw token is stored here
        (token_hash).  The raw token travels only in the invite email.
      - Tokens expire after 14 days (set at creation time in expires_at).
      - Revocation is permanent; a revoked invite cannot be re-activated.
      - status transitions: pending → accepted | expired | revoked
    """
    __tablename__ = 'user_invites'

    id                     = db.Column(db.Integer, primary_key=True)

    # Scope — which project this invite is for (nullable for platform-wide invites)
    project_id             = db.Column(db.Integer, db.ForeignKey('projects.id'),            nullable=True)
    # Optionally links to a ProjectTeamMember roster entry (legacy)
    project_team_member_id = db.Column(db.Integer, db.ForeignKey('project_team_members.id'), nullable=True)
    # AC-3: links to the new ProjectMemberAC row
    project_member_ac_id   = db.Column(db.Integer, db.ForeignKey('project_members_ac.id'),   nullable=True)

    # Invitee identity (captured at invite time)
    email                  = db.Column(db.String(150), nullable=False)
    name                   = db.Column(db.String(100), nullable=False, default='')
    company                = db.Column(db.String(100), default='')

    # Role & permissions to assign upon acceptance
    role                   = db.Column(db.String(40),  default='site_engineer')
    permission_template    = db.Column(db.String(40),  default='')
    can_sign               = db.Column(db.Boolean,     default=False)

    # Security — raw token NEVER stored; only its SHA-256 hex digest
    token_hash             = db.Column(db.String(64),  nullable=False, unique=True)

    # Who sent the invite
    invited_by_id          = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    invited_at             = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at             = db.Column(db.DateTime, nullable=True)

    # Lifecycle timestamps
    accepted_at            = db.Column(db.DateTime, nullable=True)
    revoked_at             = db.Column(db.DateTime, nullable=True)

    # status: pending | accepted | expired | revoked
    status                 = db.Column(db.String(20), nullable=False, default='pending')

    # Relationships
    project     = db.relationship('Project',           backref='user_invites', lazy=True)
    invited_by  = db.relationship('User', foreign_keys=[invited_by_id],
                                  backref='sent_invites', lazy=True)
    team_member = db.relationship('ProjectTeamMember', backref=db.backref('user_invite', uselist=False),
                                  lazy=True)
    ac_member   = db.relationship('ProjectMemberAC',
                                  foreign_keys=[project_member_ac_id],
                                  backref=db.backref('invite_record', uselist=False),
                                  lazy=True)

    @property
    def is_usable(self):
        """True only if the invite can still be accepted right now."""
        if self.status != 'pending':
            return False
        expires_at = self.expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:          # SQLite returns naive datetimes
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < datetime.now(timezone.utc):
                return False
        return True


class PasswordResetToken(db.Model):
    """
    Short-lived token for password reset.

    Security contract:
      - Only the SHA-256 hex digest of the raw token is stored (token_hash).
      - The raw token travels only in the reset email.
      - Tokens expire after 1 hour (set at creation time in expires_at).
      - Tokens are single-use; used_at is set on first use, subsequent use fails.
      - Revocation is permanent; any prior un-used token is revoked when a new
        one is issued for the same user.
    """
    __tablename__ = 'password_reset_tokens'

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    # Security — raw token NEVER stored; only its SHA-256 hex digest
    token_hash = db.Column(db.String(64), nullable=False, unique=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime, nullable=True)
    used_at    = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)

    # Relationship
    user = db.relationship('User', backref='password_reset_tokens', lazy=True)

    @property
    def is_usable(self):
        """True only if the token can still be used to reset a password."""
        if self.used_at or self.revoked_at:
            return False
        expires_at = self.expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:          # SQLite returns naive datetimes
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < datetime.now(timezone.utc):
                return False
        return True


# ═══════════════════════════════════════════════════════════════════
# ACCESS CONTROL CENTRE — Phase AC-1 Models
# ProjectMemberAC and ProjectMemberPermission coexist alongside the
# old ProjectMember table. Old names/tables remain untouched.
# ═══════════════════════════════════════════════════════════════════

class ProjectMemberAC(db.Model):
    """Future unified project-member record.
    Replaces ProjectMember + ProjectTeamMember in Phase AC-3."""
    __tablename__ = 'project_members_ac'
    __table_args__ = (
        db.UniqueConstraint('project_id', 'user_id', name='uq_pm_ac_project_user'),
    )

    id           = db.Column(db.Integer, primary_key=True)
    project_id   = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    is_owner     = db.Column(db.Boolean, default=False,  nullable=False)
    access_level = db.Column(db.String(30), nullable=False, default='engineer')
    is_active    = db.Column(db.Boolean, default=True,   nullable=False)

    # Display / contact fields (copied from ProjectTeamMember pattern)
    name         = db.Column(db.String(100), nullable=False)
    email        = db.Column(db.String(150), default='')
    position     = db.Column(db.String(100), default='')
    phone        = db.Column(db.String(50),  default='')
    company_id   = db.Column(db.Integer, db.ForeignKey('project_companies.id'), nullable=True)

    # Invite lifecycle
    invite_status      = db.Column(db.String(20), default='not_invited')
    invite_token_hash  = db.Column(db.String(64), unique=True, nullable=True)
    invite_sent_at     = db.Column(db.DateTime, nullable=True)
    invite_expires_at  = db.Column(db.DateTime, nullable=True)
    invite_accepted_at = db.Column(db.DateTime, nullable=True)

    added_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    added_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    # Relationships
    project     = db.relationship('Project', foreign_keys=[project_id],
                                  backref=db.backref('access_members', lazy=True))
    user        = db.relationship('User', foreign_keys=[user_id],
                                  backref='ac_memberships', lazy=True)
    added_by    = db.relationship('User', foreign_keys=[added_by_id],
                                  backref='ac_memberships_added', lazy=True)
    permissions = db.relationship('ProjectMemberPermission', backref='member',
                                  lazy=True, cascade='all, delete-orphan')

    # ── Convenience properties ──────────────────────────────────────
    @property
    def access_level_label(self):
        return next(
            (lbl for key, lbl, *_ in AC_ACCESS_LEVELS if key == self.access_level),
            self.access_level.replace('_', ' ').title()
        )

    @property
    def access_level_color(self):
        return next(
            (color for key, lbl, icon, color, *_ in AC_ACCESS_LEVELS if key == self.access_level),
            '#64748b'
        )

    @property
    def access_level_icon(self):
        return next(
            (icon for key, lbl, icon, *_ in AC_ACCESS_LEVELS if key == self.access_level),
            'fa-user'
        )

    def has_permission(self, permission_key: str) -> bool:
        """Return True if this member holds the requested permission.

        Owners always return True.
        All others look up the matching ProjectMemberPermission row.
        """
        if self.is_owner:
            return True
        perm = next((p for p in self.permissions if p.permission_key == permission_key), None)
        return bool(perm and perm.value)


class ProjectMemberPermission(db.Model):
    """One row per (ProjectMemberAC × permission_key).

    value  — True = permission granted
    locked — True = UI toggle disabled (owner rows are locked on)
    """
    __tablename__ = 'project_member_permissions'
    __table_args__ = (
        db.UniqueConstraint('member_id', 'permission_key', name='uq_pm_perm_member_key'),
    )

    id             = db.Column(db.Integer, primary_key=True)
    member_id      = db.Column(db.Integer, db.ForeignKey('project_members_ac.id'), nullable=False)
    permission_key = db.Column(db.String(50), nullable=False)
    value          = db.Column(db.Boolean, default=False, nullable=False)
    locked         = db.Column(db.Boolean, default=False, nullable=False)


# ── Phase AC-1 helper functions ─────────────────────────────────────

def default_permissions_for_access_level(access_level: str) -> frozenset:
    """Return the set of permission keys that are ON by default for an access level."""
    return DEFAULT_PERMISSIONS.get(access_level, frozenset())


def locked_permissions_for_access_level(access_level: str) -> frozenset:
    """Return the set of permission keys that are locked (cannot be toggled) for an access level."""
    return LOCKED_PERMISSIONS.get(access_level, frozenset())


def seed_member_permissions(member: ProjectMemberAC) -> list:
    """Create one ProjectMemberPermission row per PERMISSION_KEY for *member*.

    Truly idempotent — safe to call multiple times before flush/commit.

    Two guarantees:
    1. ``no_autoflush`` prevents SQLAlchemy from issuing a premature flush
       when the relationship collection is loaded for reading.
    2. New rows are appended to ``member.permissions`` (not added to the
       session directly), so the in-memory collection stays in sync and a
       second call in the same transaction sees the pending rows without
       needing a flush first.

    Does NOT call db.session.commit(); caller is responsible.

    Returns the list of newly-created rows (empty when all rows already exist).
    """
    defaults   = default_permissions_for_access_level(member.access_level)
    locked_set = locked_permissions_for_access_level(member.access_level)

    new_rows = []
    with db.session.no_autoflush:
        # Reads the in-memory collection, picking up both persisted rows
        # AND rows appended (but not yet flushed) by a previous call.
        existing_keys = {p.permission_key for p in member.permissions}

        for key in PERMISSION_KEYS:
            if key in existing_keys:
                continue
            row = ProjectMemberPermission(
                permission_key = key,
                value          = key in defaults,
                locked         = key in locked_set,
            )
            # Append to the relationship collection so SQLAlchemy sets
            # member_id automatically AND the collection reflects the new
            # row immediately — no explicit db.session.add() required.
            member.permissions.append(row)
            new_rows.append(row)

    return new_rows
