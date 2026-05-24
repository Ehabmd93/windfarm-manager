"""
seed.py — Database initialisation.

This file is split into two concerns:

1. Schema migrations (always safe to run):
   - Add missing columns to existing tables
   - Create foundation stage templates
   - Seed generic custom tracking field defaults
   - Create progress widgets if none exist

2. Demo / legacy data (only if ENABLE_DEMO_KRWF_SEED=true):
   - Create King Rocks Wind Farm project
   - Create demo WTGs with hardstand / crane pad / blade areas
   - Create demo user accounts (engineer@cbop.com, etc.)
   - Link orphan WTGs to King Rocks project

Production startup does NOT create King Rocks or demo users unless the
env var is set.  Existing production data is never touched.
"""
import os
from werkzeug.security import generate_password_hash
from models import (db, User, WTG, Area, QATest, ITPRecord, ITPItemStatus,
                    FoundationStage, FoundationStageTemplate, FOUNDATION_STAGES,
                    WTGGroup, ELEMENT_TYPES,
                    CustomTrackingField, ProgressWidget)


# ─── Demo data constants (KRWF only) ─────────────────────────────────────────

DEMO_WTG_NAMES = [
    'WTG02','WTG04','WTG06','WTG07','WTG08',
    'WTG09','WTG10','WTG14','WTG15','WTG16',
    'WTG17','WTG18','WTG19','WTG20','WTG21',
    'WTG22','WTG23'
]

DEMO_TEST_MATRIX = {
    'hardstand': [
        'dcp','subgrade_compaction','subbase_compaction','basecourse_compaction',
        'proof_roll_foundation','proof_roll_subgrade','proof_roll_subbase',
        'proof_roll_basecourse','plate_load_test',
    ],
    'crane_pad': [
        'dcp','subgrade_compaction','subbase_compaction','basecourse_compaction',
        'proof_roll_foundation','proof_roll_subgrade','proof_roll_subbase',
        'proof_roll_basecourse',
    ],
    'boom_pad': [
        'dcp','subgrade_compaction','subbase_compaction','basecourse_compaction',
    ],
    'blade_fingers': [
        'dcp','subgrade_compaction','basecourse_compaction',
        'proof_roll_foundation','proof_roll_subgrade','proof_roll_basecourse',
    ],
}

DEMO_AREA_LABELS = {
    'hardstand':     'Hardstand',
    'crane_pad':     'Crane Pad',
    'boom_pad':      'Boom Pad',
    'blade_fingers': 'Blade Fingers',
}

# ─── Generic defaults (always seeded) ────────────────────────────────────────

DEFAULT_FIELDS = [
    # DCP
    dict(scope='dcp', field_key='report_no',   label='Report / TRN No.',  field_type='text',   unit='',            default_value='', sort_order=1),
    dict(scope='dcp', field_key='layer',       label='Layer Tested',       field_type='select', unit='',            default_value='Foundation', options='Foundation,Subgrade,Subbase,Basecourse', sort_order=2),
    dict(scope='dcp', field_key='blows',       label='DCP Result',         field_type='number', unit='blows/100mm', default_value='', spec_min=6, sort_order=3),
    dict(scope='dcp', field_key='depth_mm',    label='Test Depth',         field_type='number', unit='mm',          default_value='300', sort_order=4),
    dict(scope='dcp', field_key='moisture',    label='Moisture Condition', field_type='select', unit='',            default_value='Moist', options='Dry,Moist,Wet', sort_order=5),
    # Compaction
    dict(scope='subgrade_compaction',   field_key='report_no',    label='Report / TRN No.',  field_type='text',   unit='', sort_order=1),
    dict(scope='subgrade_compaction',   field_key='wet_density',  label='Field Wet Density', field_type='number', unit='t/m³',  sort_order=2),
    dict(scope='subgrade_compaction',   field_key='dry_density',  label='Field Dry Density', field_type='number', unit='t/m³',  sort_order=3),
    dict(scope='subgrade_compaction',   field_key='mdd',          label='Max Dry Density',   field_type='number', unit='t/m³',  sort_order=4),
    dict(scope='subgrade_compaction',   field_key='moisture_pct', label='Field Moisture',    field_type='number', unit='%',     sort_order=5),
    dict(scope='subgrade_compaction',   field_key='omc',          label='OMC',               field_type='number', unit='%',     sort_order=6),
    dict(scope='subgrade_compaction',   field_key='density_ratio',label='Density Ratio',     field_type='number', unit='%',     spec_min=92, sort_order=7),
    dict(scope='subgrade_compaction',   field_key='spec',         label='Specification',     field_type='select', unit='',      default_value='92% MDDR', options='92% MDDR,95% MDDR,98% MDDR', sort_order=8),
    # Basecourse
    dict(scope='basecourse_compaction', field_key='report_no',    label='Report / TRN No.',  field_type='text',   unit='', sort_order=1),
    dict(scope='basecourse_compaction', field_key='wet_density',  label='Field Wet Density', field_type='number', unit='t/m³',  sort_order=2),
    dict(scope='basecourse_compaction', field_key='dry_density',  label='Field Dry Density', field_type='number', unit='t/m³',  sort_order=3),
    dict(scope='basecourse_compaction', field_key='mdd',          label='Max Dry Density',   field_type='number', unit='t/m³',  sort_order=4),
    dict(scope='basecourse_compaction', field_key='moisture_pct', label='Field Moisture',    field_type='number', unit='%',     sort_order=5),
    dict(scope='basecourse_compaction', field_key='density_ratio',label='Density Ratio',     field_type='number', unit='%',     spec_min=95, sort_order=6),
    dict(scope='basecourse_compaction', field_key='spec',         label='Specification',     field_type='select', unit='',      default_value='95% MDDR', options='92% MDDR,95% MDDR,98% MDDR', sort_order=7),
    # Plate load
    dict(scope='plate_load_test', field_key='report_no',  label='Report No.',    field_type='text',   unit='', sort_order=1),
    dict(scope='plate_load_test', field_key='ev2',        label='EV2 (Modulus)', field_type='number', unit='MN/m²', sort_order=2),
    dict(scope='plate_load_test', field_key='ev2_ev1',    label='EV2/EV1 Ratio', field_type='number', unit='',      spec_max=2.5, sort_order=3),
]

DEFAULT_WIDGETS = [
    dict(title='Element Completion Overview', widget_type='bar',   data_source='wtg_completion',     sort_order=1),
    dict(title='Overall QA Status',           widget_type='pie',   data_source='status_breakdown',   sort_order=2),
    dict(title='Foundation Stage Progress',   widget_type='bar',   data_source='foundation_stages',  sort_order=3),
    dict(title='Tests by Area Type',          widget_type='pie',   data_source='area_completion',    sort_order=4),
    dict(title='Test Results Table',          widget_type='table', data_source='test_records_table', sort_order=5),
]


# ─── Schema migrations ────────────────────────────────────────────────────────

def _schema_migrations(app):
    """Safe column-level migrations. Never creates data. Never touches project rows.
    Called every startup — idempotent."""
    from sqlalchemy import text, inspect as sa_inspect

    with app.app_context():
        insp = sa_inspect(db.engine)

        # ── wtgs columns ──
        if 'wtgs' in insp.get_table_names():
            cols = {c['name'] for c in insp.get_columns('wtgs')}
            for col_name, sql in [
                ('project_id',      'ALTER TABLE wtgs ADD COLUMN project_id INTEGER'),
                ('group_id',        'ALTER TABLE wtgs ADD COLUMN group_id INTEGER'),
                ('element_type',    "ALTER TABLE wtgs ADD COLUMN element_type VARCHAR(30) DEFAULT 'wtg'"),
                ('work_package_id', 'ALTER TABLE wtgs ADD COLUMN work_package_id INTEGER'),
            ]:
                if col_name not in cols:
                    _exec_ddl(db, text(sql))

        # ── work_packages columns ──
        if 'work_packages' in insp.get_table_names():
            wp_cols = {c['name'] for c in insp.get_columns('work_packages')}
            if 'group_id' not in wp_cols:
                _exec_ddl(db, text('ALTER TABLE work_packages ADD COLUMN group_id INTEGER'))

        # ── documents columns ──
        if 'documents' in insp.get_table_names():
            doc_cols = {c['name'] for c in insp.get_columns('documents')}
            for col_name, sql in [
                ('folder_id', 'ALTER TABLE documents ADD COLUMN folder_id INTEGER REFERENCES document_folders(id)'),
                ('file_key',  'ALTER TABLE documents ADD COLUMN file_key VARCHAR(500)'),
            ]:
                if col_name not in doc_cols:
                    _exec_ddl(db, text(sql))
            # Make file_data nullable
            try:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE documents ALTER COLUMN file_data DROP NOT NULL"))
                    conn.commit()
            except Exception:
                pass  # already nullable or SQLite

        # ── Ensure all features exist for all existing projects ──────────────
        from models import Project, ProjectFeature, ALL_FEATURES
        try:
            for proj in Project.query.all():
                existing_keys = {f.feature_key for f in proj.features}
                added = 0
                for key, *_ in ALL_FEATURES:
                    if key not in existing_keys:
                        db.session.add(ProjectFeature(project_id=proj.id, feature_key=key, enabled=True))
                        added += 1
                if added:
                    db.session.commit()
        except Exception:
            pass  # projects table may not exist yet


def _exec_ddl(db, stmt):
    """Execute a DDL statement and ignore errors (column already exists)."""
    try:
        with db.engine.connect() as conn:
            conn.execute(stmt)
            conn.commit()
    except Exception:
        pass


# ─── Generic defaults (always seeded, never project-specific) ─────────────────

def _ensure_default_users(app):
    """
    If NO users exist at all, create a minimal set of default accounts
    so the app is always accessible on a fresh database.
    Runs unconditionally — safe on existing databases (noop if users exist).
    """
    from sqlalchemy.exc import IntegrityError
    with app.app_context():
        if User.query.first():
            return  # users already exist, nothing to do
        print("No users found — creating default demo accounts...")
        default_users = [
            User(name='Engineer',   email='engineer@demo.com',   password=generate_password_hash('engineer123'),   role='engineer',   company='Demo Co',    is_active=True, email_verified=True),
            User(name='Supervisor', email='supervisor@demo.com', password=generate_password_hash('supervisor123'), role='supervisor', company='Demo Co',    is_active=True, email_verified=True),
            User(name='Manager',    email='manager@demo.com',    password=generate_password_hash('manager123'),    role='manager',    company='Demo Co',    is_active=True, email_verified=True),
            User(name='Client',     email='client@demo.com',     password=generate_password_hash('client123'),     role='client',     company='Client Co',  is_active=True, email_verified=True),
        ]
        try:
            db.session.add_all(default_users)
            db.session.commit()
            print("Default demo accounts created: engineer/supervisor/manager/client @demo.com (password: role+123)")
        except IntegrityError:
            db.session.rollback()
            print("Default users already exist (concurrent startup).")


def _seed_defaults(app):
    """Foundation stage templates, custom fields, progress widgets.
    Safe to run on any project type — not wind-farm-specific."""
    with app.app_context():
        eng_user = User.query.filter_by(role='engineer').first()
        eid = eng_user.id if eng_user else 1

        # Foundation stage templates
        existing_tmpl_keys = {t.stage_key for t in FoundationStageTemplate.query.all()}
        new_tmpls = []
        for i, (key, label) in enumerate(FOUNDATION_STAGES):
            if key not in existing_tmpl_keys:
                new_tmpls.append(FoundationStageTemplate(stage_key=key, stage_label=label, sort_order=i))
        if new_tmpls:
            db.session.add_all(new_tmpls)
            db.session.commit()
            print(f"Seeded {len(new_tmpls)} foundation stage templates")

        # Foundation stages for all existing WTGs
        wtgs = WTG.query.all()
        if wtgs:
            existing_keys = {(s.wtg_id, s.stage_key) for s in FoundationStage.query.all()}
            new_stages = [
                FoundationStage(wtg_id=wtg.id, stage_key=key, stage_label=label)
                for wtg in wtgs
                for key, label in FOUNDATION_STAGES
                if (wtg.id, key) not in existing_keys
            ]
            if new_stages:
                db.session.add_all(new_stages)
                db.session.commit()
                print(f"Created {len(new_stages)} missing foundation stages")

        # Custom tracking fields
        existing_fields = {(f.scope, f.field_key) for f in CustomTrackingField.query.all()}
        new_fields = []
        for f in DEFAULT_FIELDS:
            if (f['scope'], f['field_key']) not in existing_fields:
                kwargs = {k: v for k, v in f.items() if k not in ('spec_min', 'spec_max')}
                if 'spec_min' in f: kwargs['spec_min'] = f['spec_min']
                if 'spec_max' in f: kwargs['spec_max'] = f['spec_max']
                new_fields.append(CustomTrackingField(created_by=eid, **kwargs))
        if new_fields:
            db.session.add_all(new_fields)
            db.session.commit()
            print(f"Created {len(new_fields)} missing custom tracking fields")

        # Progress widgets
        if ProgressWidget.query.count() == 0:
            for w in DEFAULT_WIDGETS:
                db.session.add(ProgressWidget(created_by=eid, **w))
            db.session.commit()
            print(f"Created {len(DEFAULT_WIDGETS)} default progress widgets")


# ─── ITP schema migration ──────────────────────────────────────────────────────

def _migrate_itp_schema(app):
    """Rebuild ITP tables if the old schema (no criterion_text) is detected."""
    from sqlalchemy import text, inspect
    with app.app_context():
        insp = inspect(db.engine)
        if 'itp_item_statuses' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('itp_item_statuses')]
            if 'criterion_text' not in cols:
                with db.engine.connect() as conn:
                    conn.execute(text('DROP TABLE IF EXISTS itp_item_statuses'))
                    conn.execute(text('DROP TABLE IF EXISTS itp_records'))
                    conn.commit()
                print("Migrated ITP tables to per-criterion schema")


# ─── KRWF demo seed (guarded by env var) ──────────────────────────────────────

def _seed_krwf_demo(app):
    """Create King Rocks Wind Farm demo data.
    Only runs when ENABLE_DEMO_KRWF_SEED=true.
    Safe to run multiple times — all operations are idempotent."""
    from sqlalchemy.exc import IntegrityError
    from models import Project, ProjectMember, ProjectFeature, ALL_FEATURES

    with app.app_context():
        # ── Create / locate King Rocks project ──
        kr = Project.query.filter_by(name='King Rocks Wind Farm').first()
        if not kr:
            eng = (User.query.filter_by(email='engineer@demo.com').first() or
                   User.query.filter_by(email='engineer@cbop.com').first())
            kr = Project(
                name         = 'King Rocks Wind Farm',
                project_type = 'Wind Farm',
                location     = 'King Rocks, South Australia',
                postcode     = '5641',
                status       = 'active',
                client_name  = 'CBOP',
                contract_ref = 'KRWF-2024',
                color        = '#0f2942',
                description  = 'King Rocks Wind Farm geotechnical QA management (demo).',
                created_by   = eng.id if eng else None,
            )
            db.session.add(kr)
            db.session.flush()
            for user in User.query.all():
                role = 'lead' if user.role in ('engineer', 'manager') else 'member'
                db.session.add(ProjectMember(project_id=kr.id, user_id=user.id, proj_role=role))
            for key, *_ in ALL_FEATURES:
                db.session.add(ProjectFeature(project_id=kr.id, feature_key=key, enabled=True))
            db.session.commit()
            print(f"Created King Rocks Wind Farm demo project (id={kr.id})")

        # ── Link orphan WTGs to King Rocks ──
        unlinked = WTG.query.filter(
            (WTG.project_id == None) | (WTG.project_id == 0)  # noqa: E711
        ).all()
        if unlinked:
            for wtg in unlinked:
                wtg.project_id = kr.id
            db.session.commit()
            print(f"Linked {len(unlinked)} orphan WTGs to King Rocks")

        # ── Seed demo users if empty ──
        # Support both legacy @cbop.com and new @demo.com accounts
        if User.query.first():
            print("Demo users already exist — skipping user seed.")
        else:
            users = [
                User(name='Demo Engineer',   email='engineer@demo.com',   password=generate_password_hash('engineer123'),   role='engineer',   company='Demo Co',   is_active=True, email_verified=True),
                User(name='Demo Supervisor', email='supervisor@demo.com', password=generate_password_hash('supervisor123'), role='supervisor', company='Demo Co',   is_active=True, email_verified=True),
                User(name='Demo Manager',    email='manager@demo.com',    password=generate_password_hash('manager123'),    role='manager',    company='Demo Co',   is_active=True, email_verified=True),
                User(name='Demo Client',     email='client@demo.com',     password=generate_password_hash('client123'),     role='client',     company='Client Co', is_active=True, email_verified=True),
            ]
            try:
                db.session.add_all(users)
                db.session.flush()
            except IntegrityError:
                db.session.rollback()
                print("Demo users already exist (concurrent worker).")
                return

            # ── WTGs ──
            for wtg_name in DEMO_WTG_NAMES:
                wtg = WTG(name=wtg_name, project_id=kr.id)
                db.session.add(wtg)
                db.session.flush()
                for area_type, tests in DEMO_TEST_MATRIX.items():
                    area = Area(wtg_id=wtg.id, area_type=area_type, label=DEMO_AREA_LABELS[area_type])
                    db.session.add(area)
                    db.session.flush()
                    for test_type in tests:
                        db.session.add(QATest(area_id=area.id, test_type=test_type))

            db.session.commit()
            print(f"Seeded {len(DEMO_WTG_NAMES)} demo WTGs (King Rocks).")


# ─── Main entry point ──────────────────────────────────────────────────────────

def seed(app):
    from sqlalchemy.exc import IntegrityError
    with app.app_context():
        _migrate_itp_schema(app)
        db.create_all()

    # Schema-level migrations (always safe)
    _schema_migrations(app)

    with app.app_context():
        # Fix old test type name
        old_tests = QATest.query.filter_by(test_type='blade_load_test').all()
        if old_tests:
            for t in old_tests:
                t.test_type = 'plate_load_test'
            db.session.commit()
            print(f"Renamed {len(old_tests)} blade_load_test → plate_load_test")

        # Rename legacy engineer user (cbop → demo)
        _eng = (User.query.filter_by(email='engineer@cbop.com').first() or
                User.query.filter_by(email='engineer@demo.com').first())
        if _eng and _eng.name not in ('Ehab', 'Engineer', 'Demo Engineer'):
            _eng.name = 'Demo Engineer'
            db.session.commit()

    # Ensure at least one user always exists (noop if users already present)
    _ensure_default_users(app)

    # Seed generic defaults (always)
    _seed_defaults(app)

    # ── KRWF demo seed — only when env var is set ──────────────────────────
    if os.environ.get('ENABLE_DEMO_KRWF_SEED', '').lower() == 'true':
        print("ENABLE_DEMO_KRWF_SEED=true — creating King Rocks demo data…")
        _seed_krwf_demo(app)
    else:
        with app.app_context():
            # Still link orphan WTGs on existing production DBs (safe operation)
            kr = None
            try:
                from models import Project
                kr = Project.query.filter_by(name='King Rocks Wind Farm').first()
            except Exception:
                pass
            if kr:
                unlinked = WTG.query.filter(
                    (WTG.project_id == None) | (WTG.project_id == 0)  # noqa: E711
                ).all()
                if unlinked:
                    for wtg in unlinked:
                        wtg.project_id = kr.id
                    db.session.commit()
