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
# WIND TURBINE GENERATORS
# ─────────────────────────────────────────────
class WTG(db.Model):
    __tablename__ = 'wtgs'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(20), unique=True, nullable=False)
    easting    = db.Column(db.Float, nullable=True)
    northing   = db.Column(db.Float, nullable=True)
    status     = db.Column(db.String(20), default='in_progress')

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
