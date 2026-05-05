"""
Seed the database with:
 - 4 demo users (one per role)
 - 17 WTGs
 - 4 areas per WTG with correct test matrix
"""
from werkzeug.security import generate_password_hash
from models import (db, User, WTG, Area, QATest, ITPRecord, ITPItemStatus,
                    FoundationStage, FoundationStageTemplate, FOUNDATION_STAGES,
                    CustomTrackingField, ProgressWidget)

WTG_NAMES = [
    'WTG02','WTG04','WTG06','WTG07','WTG08',
    'WTG09','WTG10','WTG14','WTG15','WTG16',
    'WTG17','WTG18','WTG19','WTG20','WTG21',
    'WTG22','WTG23'
]

# test_type → which areas require it
# areas: hardstand (H), crane_pad (C), boom_pad (B), blade_fingers (F)
TEST_MATRIX = {
    'hardstand': [
        'dcp',
        'subgrade_compaction',
        'subbase_compaction',
        'basecourse_compaction',
        'proof_roll_foundation',
        'proof_roll_subgrade',
        'proof_roll_subbase',
        'proof_roll_basecourse',
        'plate_load_test',
    ],
    'crane_pad': [
        'dcp',
        'subgrade_compaction',
        'subbase_compaction',
        'basecourse_compaction',
        'proof_roll_foundation',
        'proof_roll_subgrade',
        'proof_roll_subbase',
        'proof_roll_basecourse',
    ],
    'boom_pad': [
        'dcp',
        'subgrade_compaction',
        'subbase_compaction',
        'basecourse_compaction',
    ],
    'blade_fingers': [
        'dcp',
        'subgrade_compaction',
        'basecourse_compaction',
        'proof_roll_foundation',
        'proof_roll_subgrade',
        'proof_roll_basecourse',
    ],
}

AREA_LABELS = {
    'hardstand':     'Hardstand',
    'crane_pad':     'Crane Pad',
    'boom_pad':      'Boom Pad',
    'blade_fingers': 'Blade Fingers',
}

def _migrate_projects(app):
    """Add project_id column to wtgs if missing, create King Rocks project, link WTGs.
    NOTE: called from inside seed()'s app_context — no nested context needed."""
    from sqlalchemy import text, inspect as sa_inspect
    from models import Project, ProjectMember, ProjectFeature, ALL_FEATURES, User, WTG

    # ── 1. Add project_id column to wtgs table if missing ────────────────────
    insp = sa_inspect(db.engine)
    if 'wtgs' in insp.get_table_names():
        cols = [c['name'] for c in insp.get_columns('wtgs')]
        if 'project_id' not in cols:
            try:
                with db.engine.connect() as conn:
                    conn.execute(text('ALTER TABLE wtgs ADD COLUMN project_id INTEGER'))
                    conn.commit()
                print("Added project_id column to wtgs")
            except Exception as e:
                print(f"project_id column note: {e}")

    # ── 2. Create King Rocks Wind Farm project if it doesn't exist ───────────
    try:
        kr = Project.query.filter_by(name='King Rocks Wind Farm').first()
    except Exception:
        # projects table might not exist yet in an edge case — skip
        return

    if not kr:
        eng = User.query.filter_by(email='engineer@cbop.com').first()
        kr = Project(
            name         = 'King Rocks Wind Farm',
            project_type = 'Wind Farm',
            location     = 'King Rocks, South Australia',
            postcode     = '5641',
            status       = 'active',
            client_name  = 'CBOP',
            contract_ref = 'KRWF-2024',
            color        = '#0f2942',
            description  = 'King Rocks Wind Farm geotechnical QA management.',
            created_by   = eng.id if eng else None,
        )
        db.session.add(kr)
        db.session.flush()

        # Add all existing users as members
        for user in User.query.all():
            role = 'lead' if user.role in ('engineer', 'manager') else 'member'
            db.session.add(ProjectMember(project_id=kr.id, user_id=user.id, proj_role=role))

        # Enable all features
        for key, *_ in ALL_FEATURES:
            db.session.add(ProjectFeature(project_id=kr.id, feature_key=key, enabled=True))

        db.session.commit()
        print(f"Created King Rocks Wind Farm project (id={kr.id})")

    # ── 2b. Ensure all features exist for all existing projects ──────────────
    for proj in Project.query.all():
        existing_keys = {f.feature_key for f in proj.features}
        added = 0
        for key, *_ in ALL_FEATURES:
            if key not in existing_keys:
                db.session.add(ProjectFeature(project_id=proj.id, feature_key=key, enabled=True))
                added += 1
        if added:
            db.session.commit()
            print(f"Added {added} missing features to project '{proj.name}'")

    # ── 3. Link any WTGs with no project to King Rocks ───────────────────────
    if kr:
        try:
            unlinked = WTG.query.filter(
                (WTG.project_id == None) | (WTG.project_id == 0)  # noqa: E711
            ).all()
            if unlinked:
                for wtg in unlinked:
                    wtg.project_id = kr.id
                db.session.commit()
                print(f"Linked {len(unlinked)} WTGs to King Rocks project")
        except Exception as e:
            print(f"WTG linking note: {e}")
            db.session.rollback()


def _migrate_itp_schema(app):
    """Drop and recreate ITP tables if schema has changed (new per-criterion columns)."""
    from sqlalchemy import text, inspect
    with app.app_context():
        insp = inspect(db.engine)
        if 'itp_item_statuses' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('itp_item_statuses')]
            if 'criterion_text' not in cols:
                # Old schema — drop and let db.create_all() rebuild
                with db.engine.connect() as conn:
                    conn.execute(text('DROP TABLE IF EXISTS itp_item_statuses'))
                    conn.execute(text('DROP TABLE IF EXISTS itp_records'))
                    conn.commit()
                print("✅ Migrated ITP tables to per-criterion schema")


DEFAULT_FIELDS = [
    # DCP fields
    dict(scope='dcp', field_key='report_no',   label='Report / TRN No.',      field_type='text',   unit='',            default_value='', sort_order=1),
    dict(scope='dcp', field_key='layer',       label='Layer Tested',           field_type='select', unit='',            default_value='Foundation', options='Foundation,Subgrade,Subbase,Basecourse', sort_order=2),
    dict(scope='dcp', field_key='blows',       label='DCP Result',             field_type='number', unit='blows/100mm', default_value='', spec_min=6, sort_order=3),
    dict(scope='dcp', field_key='depth_mm',    label='Test Depth',             field_type='number', unit='mm',          default_value='300', sort_order=4),
    dict(scope='dcp', field_key='moisture',    label='Moisture Condition',     field_type='select', unit='',            default_value='Moist', options='Dry,Moist,Wet', sort_order=5),
    # Compaction fields
    dict(scope='subgrade_compaction',   field_key='report_no',    label='Report / TRN No.',    field_type='text',   unit='', sort_order=1),
    dict(scope='subgrade_compaction',   field_key='wet_density',  label='Field Wet Density',   field_type='number', unit='t/m³',  sort_order=2),
    dict(scope='subgrade_compaction',   field_key='dry_density',  label='Field Dry Density',   field_type='number', unit='t/m³',  sort_order=3),
    dict(scope='subgrade_compaction',   field_key='mdd',          label='Max Dry Density',     field_type='number', unit='t/m³',  sort_order=4),
    dict(scope='subgrade_compaction',   field_key='moisture_pct', label='Field Moisture',      field_type='number', unit='%',     sort_order=5),
    dict(scope='subgrade_compaction',   field_key='omc',          label='OMC',                 field_type='number', unit='%',     sort_order=6),
    dict(scope='subgrade_compaction',   field_key='density_ratio',label='Density Ratio',       field_type='number', unit='%',     spec_min=92, sort_order=7),
    dict(scope='subgrade_compaction',   field_key='spec',         label='Specification',       field_type='select', unit='',      default_value='92% MDDR', options='92% MDDR,95% MDDR,98% MDDR', sort_order=8),
    # Basecourse compaction
    dict(scope='basecourse_compaction', field_key='report_no',    label='Report / TRN No.',    field_type='text',   unit='', sort_order=1),
    dict(scope='basecourse_compaction', field_key='wet_density',  label='Field Wet Density',   field_type='number', unit='t/m³',  sort_order=2),
    dict(scope='basecourse_compaction', field_key='dry_density',  label='Field Dry Density',   field_type='number', unit='t/m³',  sort_order=3),
    dict(scope='basecourse_compaction', field_key='mdd',          label='Max Dry Density',     field_type='number', unit='t/m³',  sort_order=4),
    dict(scope='basecourse_compaction', field_key='moisture_pct', label='Field Moisture',      field_type='number', unit='%',     sort_order=5),
    dict(scope='basecourse_compaction', field_key='density_ratio',label='Density Ratio',       field_type='number', unit='%',     spec_min=95, sort_order=6),
    dict(scope='basecourse_compaction', field_key='spec',         label='Specification',       field_type='select', unit='',      default_value='95% MDDR', options='92% MDDR,95% MDDR,98% MDDR', sort_order=7),
    # Plate load test
    dict(scope='plate_load_test', field_key='report_no',  label='Report No.',         field_type='text',   unit='', sort_order=1),
    dict(scope='plate_load_test', field_key='ev2',        label='EV2 (Modulus)',       field_type='number', unit='MN/m²', sort_order=2),
    dict(scope='plate_load_test', field_key='ev2_ev1',    label='EV2/EV1 Ratio',      field_type='number', unit='',      spec_max=2.5, sort_order=3),
]

DEFAULT_WIDGETS = [
    dict(title='WTG Completion Overview',  widget_type='bar',   data_source='wtg_completion',     sort_order=1),
    dict(title='Overall QA Status',         widget_type='pie',   data_source='status_breakdown',   sort_order=2),
    dict(title='Foundation Stage Progress', widget_type='bar',   data_source='foundation_stages',  sort_order=3),
    dict(title='Tests by Area Type',        widget_type='pie',   data_source='area_completion',    sort_order=4),
    dict(title='Test Results Table',        widget_type='table', data_source='test_records_table', sort_order=5),
]


def _seed_defaults(app):
    """Idempotently seed foundation stages, custom fields and widgets.
    Safe to call even if users/WTGs already exist."""
    with app.app_context():
        eng_user = User.query.filter_by(role='engineer').first()
        eid = eng_user.id if eng_user else 1

        # Foundation stage templates (global editable list)
        existing_tmpl_keys = {t.stage_key for t in FoundationStageTemplate.query.all()}
        new_tmpls = []
        for i, (key, label) in enumerate(FOUNDATION_STAGES):
            if key not in existing_tmpl_keys:
                new_tmpls.append(FoundationStageTemplate(stage_key=key, stage_label=label, sort_order=i))
        if new_tmpls:
            db.session.add_all(new_tmpls)
            db.session.commit()
            print(f"Seeded {len(new_tmpls)} foundation stage templates")

        # Foundation stages — one per (wtg, stage_key)
        wtgs = WTG.query.all()
        if wtgs:
            existing_keys = {(s.wtg_id, s.stage_key) for s in FoundationStage.query.all()}
            new_stages = []
            for wtg in wtgs:
                for key, label in FOUNDATION_STAGES:
                    if (wtg.id, key) not in existing_keys:
                        new_stages.append(FoundationStage(wtg_id=wtg.id, stage_key=key, stage_label=label))
            if new_stages:
                db.session.add_all(new_stages)
                db.session.commit()
                print(f"Created {len(new_stages)} missing foundation stages")

        # Custom tracking fields — keyed by (scope, field_key)
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
            print(f"✅ Created {len(new_fields)} missing custom tracking fields")

        # Progress widgets — only if none exist yet
        if ProgressWidget.query.count() == 0:
            for w in DEFAULT_WIDGETS:
                db.session.add(ProgressWidget(created_by=eid, **w))
            db.session.commit()
            print(f"✅ Created {len(DEFAULT_WIDGETS)} default progress widgets")


def seed(app):
    from sqlalchemy.exc import IntegrityError
    with app.app_context():
        _migrate_itp_schema(app)
        db.create_all()  # Creates / updates tables
        _migrate_projects(app)   # runs inside the same app_context (no nested context)

        # Fix any old 'blade_load_test' records
        old_tests = QATest.query.filter_by(test_type='blade_load_test').all()
        if old_tests:
            for t in old_tests:
                t.test_type = 'plate_load_test'
            db.session.commit()
            print(f"✅ Renamed {len(old_tests)} blade_load_test → plate_load_test")

        # ── Idempotent defaults (run even if DB already has users) ───────────
        _seed_defaults(app)

        # ── One-time renames on existing DB ──────────────────────────
        _eng = User.query.filter_by(email='engineer@cbop.com').first()
        if _eng and _eng.name != 'Ehab':
            _eng.name = 'Ehab'
            db.session.commit()
            print("✅ Renamed engineer → Ehab")

        # Only seed users/WTGs if empty
        if User.query.first():
            print("Database already seeded.")
            return

        # ── Users ──────────────────────────────────────
        users = [
            User(name='Ehab',            email='engineer@cbop.com',   password=generate_password_hash('engineer123'),   role='engineer',   company='CBOP'),
            User(name='Sam Supervisor',  email='supervisor@cbop.com', password=generate_password_hash('supervisor123'), role='supervisor', company='CBOP'),
            User(name='Morgan Manager',  email='manager@cbop.com',    password=generate_password_hash('manager123'),    role='manager',    company='CBOP'),
            User(name='Client Rep',      email='client@client.com',   password=generate_password_hash('client123'),    role='client',     company='Client'),
        ]
        try:
            db.session.add_all(users)
            db.session.flush()
        except IntegrityError:
            # Another worker already seeded — this worker can safely exit seed
            db.session.rollback()
            print("Database already seeded (concurrent worker).")
            return

        # ── WTGs & Areas ───────────────────────────────
        # Find King Rocks project to link WTGs (created earlier by _migrate_projects)
        from models import Project as _Proj
        _kr = _Proj.query.filter_by(name='King Rocks Wind Farm').first()
        for wtg_name in WTG_NAMES:
            wtg = WTG(name=wtg_name, project_id=_kr.id if _kr else None)
            db.session.add(wtg)
            db.session.flush()

            for area_type, tests in TEST_MATRIX.items():
                area = Area(
                    wtg_id=wtg.id,
                    area_type=area_type,
                    label=AREA_LABELS[area_type]
                )
                db.session.add(area)
                db.session.flush()

                for test_type in tests:
                    qa = QATest(area_id=area.id, test_type=test_type)
                    db.session.add(qa)

        db.session.commit()
        print(f"✅ Seeded {len(WTG_NAMES)} WTGs × 4 areas with full test matrix.")

        # Foundation stages, custom fields & widgets handled by _seed_defaults above

        print("Demo accounts:")
        for u in users:
            print(f"  {u.role:12s} → {u.email}")
