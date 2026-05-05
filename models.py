import os
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timezone

db = SQLAlchemy()

# ─────────────────────────────────────────────
# USERS
# ─────────────────────────────────────────────
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    email      = db.Column(db.String(150), unique=True, nullable=False)
    password   = db.Column(db.String(256), nullable=False)
    role       = db.Column(db.String(30), nullable=False)  # engineer | supervisor | manager | client
    company    = db.Column(db.String(100), default='CBOP')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def can_enter_data(self):
        return self.role == 'engineer'

    def can_view_only(self):
        return self.role in ('supervisor', 'manager', 'client')

    def is_manager_or_above(self):
        return self.role in ('manager', 'supervisor')

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


# ─────────────────────────────────────────────
# WIND TURBINE GENERATORS
# ─────────────────────────────────────────────
class WTG(db.Model):
    __tablename__ = 'wtgs'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(20), unique=True, nullable=False)
    easting    = db.Column(db.Float, nullable=True)
    northing   = db.Column(db.Float, nullable=True)
    status     = db.Column(db.String(20), default='in_progress')
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)

    areas      = db.relationship('Area', backref='wtg', lazy=True, cascade='all,delete')

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

    required_tests = db.relationship('QATest', backref='area', lazy=True, cascade='all,delete')

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
class ITPRecord(db.Model):
    __tablename__ = 'itp_records'
    id              = db.Column(db.Integer, primary_key=True)
    wtg_id          = db.Column(db.Integer, db.ForeignKey('wtgs.id'), nullable=False)
    itp_type        = db.Column(db.String(10), nullable=False)   # ITP02 | ITP03
    lot_number      = db.Column(db.String(50))
    location        = db.Column(db.String(200))
    created_at      = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    created_by      = db.Column(db.Integer, db.ForeignKey('users.id'))

    # Engineer sign-off (Lucas)
    engineer_name       = db.Column(db.String(100))
    engineer_company    = db.Column(db.String(100), default='CBOP')
    engineer_signature  = db.Column(db.Text)           # base64
    engineer_signed_at  = db.Column(db.DateTime, nullable=True)

    # Status: draft | engineer_signed | client_invited | complete
    status = db.Column(db.String(30), default='draft')

    # Client invite
    client_name         = db.Column(db.String(100))
    client_email        = db.Column(db.String(150))
    client_company      = db.Column(db.String(100), default='Vestas')
    client_token        = db.Column(db.String(64), unique=True, nullable=True)
    client_invited_at   = db.Column(db.DateTime, nullable=True)
    client_signature    = db.Column(db.Text)
    client_signed_at    = db.Column(db.DateTime, nullable=True)

    wtg          = db.relationship('WTG',         backref='itp_records', lazy=True)
    item_statuses = db.relationship('ITPItemStatus', backref='itp_record', lazy=True, cascade='all,delete')

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

    # Lucas TCS sign-off (per criterion)
    lucas_complete   = db.Column(db.Boolean, default=False)
    lucas_signed_at  = db.Column(db.DateTime, nullable=True)   # date + time
    lucas_comments   = db.Column(db.Text)
    lucas_signature  = db.Column(db.Text)   # base64 PNG

    # Client sign-off (per criterion)
    client_complete  = db.Column(db.Boolean, default=False)
    client_signed_at = db.Column(db.DateTime, nullable=True)   # date + time
    client_comments  = db.Column(db.Text)
    client_signature = db.Column(db.Text)   # base64 PNG

    # Attached documents / photos per criterion
    documents        = db.relationship('ITPItemDocument', backref='item_status',
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
    file_data         = db.Column(db.Text, nullable=False)         # base64 encoded
    category          = db.Column(db.String(50), default='general')
    tags              = db.Column(db.String(500))                  # comma-separated
    uploaded_by       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    uploaded_at       = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    is_active         = db.Column(db.Boolean, default=True)

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
