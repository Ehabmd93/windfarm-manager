import os, json, base64, uuid, unicodedata, re
from urllib.parse import quote as _urlquote
from datetime import datetime, date, timezone
from flask import (Flask, render_template, request, redirect,
                   url_for, flash, jsonify, abort, send_from_directory, make_response)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename
import json as _json
from models import (db, User, WTG, Area, QATest, TestRecord,
                    Project, ProjectMember, ProjectFeature,
                    PROJECT_TYPES, PROJECT_STATUSES, ALL_FEATURES,
                    ProofRollRecord, ProofRollSignatory,
                    ProofRollEquipment, ProofRollPhoto, ProofRollRectPhoto,
                    TempPhotoUpload,
                    TestPhoto,
                    ITPRecord, ITPItemStatus, ITPItemDocument,
                    FoundationStage, FoundationStageTemplate, FoundationDocument, FOUNDATION_STAGES,
                    CustomTrackingField, ProgressWidget,
                    Document, DocumentLink, DocumentFolder,
                    DOCUMENT_CATEGORIES, DOCUMENT_LINK_TYPES, DOCUMENT_LINK_DICT)
from itp_definitions import ITP_DEFINITIONS, CLIENTS
from seed import seed
from kml_parser import get_geojson
from email_utils import email_client_invitation, email_client_signed
import r2_storage

# ─── App setup ──────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
ALLOWED_IMG = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'heic'}

# Dirs created after app init (DATA_DIR may not be set yet at import time)
UPLOAD_DIR = PHOTO_DIR = None   # set in create_dirs() below

def create_dirs():
    global UPLOAD_DIR, PHOTO_DIR
    _data_root = os.environ.get('DATA_DIR', os.path.join(BASE_DIR, 'static'))
    UPLOAD_DIR = os.path.join(_data_root, 'uploads')
    PHOTO_DIR  = os.path.join(_data_root, 'photos')
    for d in [UPLOAD_DIR, PHOTO_DIR,
              os.path.join(_data_root, 'foundation_docs'),
              os.path.join(_data_root, 'itp_item_docs')]:
        os.makedirs(d, exist_ok=True)

def allowed_image(filename):
    return '.' in filename and filename.rsplit('.',1)[1].lower() in ALLOWED_IMG

def _content_disposition(disposition, filename):
    """Build a safe Content-Disposition header value.
    HTTP headers must be ASCII.  Use RFC 5987 filename* for Unicode names."""
    # ASCII fallback: normalise accents then strip remaining non-ASCII
    ascii_name = unicodedata.normalize('NFKD', filename).encode('ascii', 'ignore').decode('ascii')
    ascii_name = re.sub(r'[\\"]', '', ascii_name)          # remove backslash & quote
    ascii_name = re.sub(r'[^\x20-\x7E]', '_', ascii_name)  # replace any remaining weird bytes
    ascii_name = ascii_name.strip() or 'download'
    # RFC 5987 UTF-8 encoded version (modern browsers prefer this)
    utf8_enc   = _urlquote(filename, safe='')
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_enc}"

app = Flask(__name__)

# ── Fix for Railway / any reverse proxy (fixes HTTPS redirects) ──────────────
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# ── Production config via environment variables ──────────────────────────────
app.secret_key = os.environ.get('SECRET_KEY', 'KRKWF-secret-2024-cbop-dev')

# Database: prefer DATABASE_URL env var (Postgres on Railway),
#           fallback to local SQLite for development
_db_url = os.environ.get('DATABASE_URL', '')
if _db_url.startswith('postgres://'):          # Railway gives postgres://, SQLAlchemy wants postgresql://
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
if not _db_url:
    _db_url = f"sqlite:///{os.path.join(BASE_DIR, 'windfarm.db')}"
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500 MB — DB fallback limit (R2 path bypasses this entirely)

# ── File upload dirs ─────────────────────────────────────────────────────────
# On Railway with a volume mounted at /data, use that; else local static/
_data_root = os.environ.get('DATA_DIR', os.path.join(BASE_DIR, 'static'))

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@app.before_request
def load_active_project():
    """Resolve which project is currently active and expose it as flask.g.project."""
    from flask import g, session
    g.project        = None
    g.user_projects  = []
    if not current_user.is_authenticated:
        return
    try:
        # Admin/engineer/manager/supervisor see all active projects; clients see only assigned ones
        if current_user.role in ('admin', 'manager', 'engineer', 'supervisor'):
            projects = Project.query.filter_by(is_active=True).order_by(Project.name).all()
        else:
            ids = [m.project_id for m in ProjectMember.query.filter_by(user_id=current_user.id).all()]
            projects = Project.query.filter(Project.id.in_(ids), Project.is_active==True).order_by(Project.name).all()
        g.user_projects = projects
        pid = session.get('active_project_id')
        if pid and any(p.id == pid for p in projects):
            g.project = next(p for p in projects if p.id == pid)
        elif projects:
            g.project = projects[0]
            session['active_project_id'] = projects[0].id
    except Exception:
        # DB not ready yet (migration in progress) — serve page without project context
        g.project       = None
        g.user_projects = []


@app.context_processor
def inject_now():
    from flask import g
    return {
        'now':                datetime.now(timezone.utc),
        'active_project':     getattr(g, 'project', None),
        'user_projects':      getattr(g, 'user_projects', []),
        'ALL_FEATURES':       ALL_FEATURES,
        'PROJECT_TYPES':      PROJECT_TYPES,
        'PROJECT_STATUSES':   PROJECT_STATUSES,
        'DOCUMENT_CATEGORIES':  DOCUMENT_CATEGORIES,
        'DOCUMENT_LINK_TYPES':  DOCUMENT_LINK_TYPES,
        'DOCUMENT_LINK_DICT':   DOCUMENT_LINK_DICT,
    }

# ─── Context helpers ─────────────────────────────────────────────────────────
def wtg_summary():
    from flask import g
    proj = getattr(g, 'project', None)
    q = WTG.query.order_by(WTG.name)
    if proj:
        q = q.filter_by(project_id=proj.id)
    return q.all()

# ─── Auth ────────────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email'].strip()).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid email or password', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ─── Projects ────────────────────────────────────────────────────────────────
@app.route('/projects')
@login_required
def projects_list():
    from flask import g
    return render_template('projects.html', projects=g.user_projects)


@app.route('/projects/switch/<int:pid>')
@login_required
def switch_project(pid):
    from flask import g, session
    ids = [p.id for p in g.user_projects]
    if pid in ids:
        session['active_project_id'] = pid
    return redirect(request.referrer or url_for('dashboard'))


@app.route('/projects/new', methods=['GET', 'POST'])
@login_required
def new_project():
    if current_user.role not in ('engineer', 'manager', 'admin'):
        abort(403)
    all_users = User.query.order_by(User.name).all()

    if request.method == 'POST':
        # ── Basic info ──
        start_raw = request.form.get('start_date', '').strip()
        end_raw   = request.form.get('end_date',   '').strip()
        proj = Project(
            name         = request.form.get('name', '').strip(),
            project_type = request.form.get('project_type', 'Wind Farm'),
            location     = request.form.get('location', '').strip(),
            postcode     = request.form.get('postcode', '').strip(),
            status       = request.form.get('status', 'active'),
            client_name  = request.form.get('client_name', '').strip(),
            contract_ref = request.form.get('contract_ref', '').strip(),
            start_date   = datetime.strptime(start_raw, '%Y-%m-%d').date() if start_raw else None,
            end_date     = datetime.strptime(end_raw,   '%Y-%m-%d').date() if end_raw   else None,
            color        = request.form.get('color', '#0f2942'),
            description  = request.form.get('description', '').strip(),
            created_by   = current_user.id,
        )
        db.session.add(proj)
        db.session.flush()

        # ── Members ──
        member_ids  = request.form.getlist('member_ids[]')
        member_roles = request.form.getlist('member_roles[]')
        added_ids = set()
        for uid, role in zip(member_ids, member_roles):
            if uid and uid not in added_ids:
                db.session.add(ProjectMember(project_id=proj.id, user_id=int(uid), proj_role=role))
                added_ids.add(uid)
        # Always add creator as lead
        if str(current_user.id) not in added_ids:
            db.session.add(ProjectMember(project_id=proj.id, user_id=current_user.id, proj_role='lead'))

        # ── Features ──
        for key, *_ in ALL_FEATURES:
            enabled = request.form.get(f'feat_{key}') == '1'
            db.session.add(ProjectFeature(project_id=proj.id, feature_key=key, enabled=enabled))

        db.session.commit()
        from flask import session as fsession
        fsession['active_project_id'] = proj.id
        flash(f'Project "{proj.name}" created successfully!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('project_new.html', all_users=all_users)


@app.route('/projects/<int:pid>/settings', methods=['GET', 'POST'])
@login_required
def project_settings(pid):
    if current_user.role not in ('engineer', 'manager', 'admin'):
        abort(403)
    proj      = Project.query.get_or_404(pid)
    all_users = User.query.order_by(User.name).all()

    if request.method == 'POST':
        action = request.form.get('action', 'info')

        if action == 'info':
            start_raw = request.form.get('start_date', '').strip()
            end_raw   = request.form.get('end_date',   '').strip()
            proj.name         = request.form.get('name', proj.name).strip()
            proj.project_type = request.form.get('project_type', proj.project_type)
            proj.location     = request.form.get('location', '').strip()
            proj.postcode     = request.form.get('postcode', '').strip()
            proj.status       = request.form.get('status', proj.status)
            proj.client_name  = request.form.get('client_name', '').strip()
            proj.contract_ref = request.form.get('contract_ref', '').strip()
            proj.color        = request.form.get('color', proj.color)
            proj.description  = request.form.get('description', '').strip()
            proj.start_date   = datetime.strptime(start_raw, '%Y-%m-%d').date() if start_raw else None
            proj.end_date     = datetime.strptime(end_raw,   '%Y-%m-%d').date() if end_raw   else None
            db.session.commit()
            flash('Project details updated.', 'success')

        elif action == 'features':
            for key, *_ in ALL_FEATURES:
                feat = next((f for f in proj.features if f.feature_key == key), None)
                enabled = request.form.get(f'feat_{key}') == '1'
                if feat:
                    feat.enabled = enabled
                else:
                    db.session.add(ProjectFeature(project_id=proj.id, feature_key=key, enabled=enabled))
            db.session.commit()
            flash('Project features updated.', 'success')

        elif action == 'team':
            ProjectMember.query.filter_by(project_id=proj.id).delete()
            member_ids   = request.form.getlist('member_ids[]')
            member_roles = request.form.getlist('member_roles[]')
            added = set()
            for uid, role in zip(member_ids, member_roles):
                if uid and uid not in added:
                    db.session.add(ProjectMember(project_id=proj.id, user_id=int(uid), proj_role=role))
                    added.add(uid)
            db.session.commit()
            flash('Team updated.', 'success')

        return redirect(url_for('project_settings', pid=pid))

    return render_template('project_settings.html', proj=proj, all_users=all_users)


# ─── Documents ───────────────────────────────────────────────────────────────
ALLOWED_DOC_EXTS = {
    'pdf','docx','doc','xlsx','xls','pptx','ppt',
    'jpg','jpeg','png','gif','bmp','webp',
    'txt','csv','zip','dwg','dxf',
}

def _allowed_doc(filename):
    return '.' in filename and filename.rsplit('.',1)[1].lower() in ALLOWED_DOC_EXTS

def _mime_for_ext(ext):
    return {
        'pdf':'application/pdf',
        'docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'doc':'application/msword',
        'xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'xls':'application/vnd.ms-excel',
        'pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'ppt':'application/vnd.ms-powerpoint',
        'jpg':'image/jpeg','jpeg':'image/jpeg',
        'png':'image/png','gif':'image/gif','webp':'image/webp',
        'txt':'text/plain','csv':'text/csv',
        'zip':'application/zip',
        'dwg':'application/acad','dxf':'application/dxf',
    }.get(ext.lower(), 'application/octet-stream')


def _build_folder_tree(folders, parent_id=None, level=0):
    """Return flat list of (folder, level) in tree order."""
    result = []
    for f in sorted(folders, key=lambda x: x.name.lower()):
        if f.parent_id == parent_id:
            result.append((f, level))
            result.extend(_build_folder_tree(folders, f.id, level + 1))
    return result


def _folder_ancestors(folder):
    """Return list [root … folder] of ancestor folders."""
    chain = []
    f = folder
    while f:
        chain.insert(0, f)
        f = DocumentFolder.query.get(f.parent_id) if f.parent_id else None
    return chain


@app.route('/documents')
@login_required
def documents_list():
    from flask import g
    proj = getattr(g, 'project', None)

    folder_id = request.args.get('folder', type=int)
    search    = request.args.get('q', '').strip()

    # All folders for this project (sidebar tree)
    fq = DocumentFolder.query
    if proj:
        fq = fq.filter_by(project_id=proj.id)
    all_folders   = fq.order_by(DocumentFolder.name).all()
    folder_tree   = _build_folder_tree(all_folders)

    # Current folder object
    current_folder = DocumentFolder.query.get(folder_id) if folder_id else None
    ancestors      = _folder_ancestors(current_folder) if current_folder else []

    # Sub-folders to show in the main panel
    sub_folders = [f for f in all_folders if f.parent_id == folder_id]

    # Files in the current folder
    base_q = Document.query.filter_by(is_active=True, folder_id=folder_id)
    if proj:
        base_q = base_q.filter_by(project_id=proj.id)
    if search:
        base_q = base_q.filter(
            Document.title.ilike(f'%{search}%') |
            Document.original_filename.ilike(f'%{search}%')
        )
    docs = base_q.order_by(Document.uploaded_at.desc()).all()

    # Total count for sidebar badge
    all_q = Document.query.filter_by(is_active=True)
    if proj:
        all_q = all_q.filter_by(project_id=proj.id)
    total_count = all_q.count()

    return render_template('documents.html',
                           docs=docs,
                           sub_folders=sub_folders,
                           folder_tree=folder_tree,
                           all_folders=all_folders,
                           current_folder=current_folder,
                           ancestors=ancestors,
                           folder_id=folder_id,
                           search=search,
                           total_count=total_count)


@app.route('/documents/folder/new', methods=['POST'])
@login_required
def folder_create():
    from flask import g
    proj      = getattr(g, 'project', None)
    name      = request.form.get('name', '').strip()
    parent_id = request.form.get('parent_id', type=int)

    if not name:
        flash('Folder name is required.', 'danger')
        return redirect(url_for('documents_list', folder=parent_id or ''))

    folder = DocumentFolder(
        project_id = proj.id if proj else None,
        parent_id  = parent_id or None,
        name       = name,
        created_by = current_user.id,
    )
    db.session.add(folder)
    db.session.commit()
    flash(f'Folder "{name}" created.', 'success')
    return redirect(url_for('documents_list', folder=folder.id))


@app.route('/documents/folder/<int:folder_id>/delete', methods=['POST'])
@login_required
def folder_delete(folder_id):
    folder = DocumentFolder.query.get_or_404(folder_id)
    parent = folder.parent_id
    # Move all documents in this folder to the parent (or root)
    for doc in folder.documents:
        doc.folder_id = parent
    db.session.delete(folder)
    db.session.commit()
    flash(f'Folder "{folder.name}" deleted. Files moved to parent folder.', 'success')
    return redirect(url_for('documents_list', folder=parent or ''))


@app.route('/documents/<int:doc_id>/move', methods=['POST'])
@login_required
def document_move(doc_id):
    doc       = Document.query.filter_by(id=doc_id, is_active=True).first_or_404()
    folder_id = request.form.get('folder_id', type=int)   # None / 0 = root
    doc.folder_id = folder_id if folder_id else None
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/documents/upload', methods=['GET', 'POST'])
@login_required
def document_upload():
    from flask import g
    proj = getattr(g, 'project', None)
    if request.method == 'POST':
        f = request.files.get('file')
        if not f or not f.filename:
            flash('No file selected.', 'danger')
            return redirect(request.url)
        if not _allowed_doc(f.filename):
            flash('File type not allowed.', 'danger')
            return redirect(request.url)

        raw   = f.read()
        ext   = f.filename.rsplit('.',1)[1].lower()
        title = request.form.get('title','').strip() or f.filename.rsplit('.',1)[0]

        folder_id_up = request.form.get('folder_id', type=int) or None

        # ── Choose storage backend ────────────────────────────────────────
        file_key  = None
        file_data = None
        if r2_storage.r2_enabled():
            try:
                file_key = r2_storage.make_key(f.filename, proj.id if proj else None)
                r2_storage.upload(file_key, raw, content_type=_mime_for_ext(ext))
            except Exception as e:
                app.logger.error(f'R2 upload failed: {e}')
                flash('Cloud storage error — falling back to database storage.', 'warning')
                file_key  = None
                file_data = base64.b64encode(raw).decode('utf-8')
        else:
            file_data = base64.b64encode(raw).decode('utf-8')

        doc = Document(
            project_id        = proj.id if proj else None,
            folder_id         = folder_id_up,
            title             = title,
            description       = request.form.get('description','').strip(),
            original_filename = f.filename,
            file_ext          = ext,
            file_size         = len(raw),
            file_key          = file_key,
            file_data         = file_data,
            category          = request.form.get('category','general'),
            tags              = request.form.get('tags','').strip(),
            uploaded_by       = current_user.id,
        )
        db.session.add(doc)
        db.session.commit()
        storage_label = '☁️ R2' if file_key else '🗄️ DB'
        flash(f'"{doc.title}" uploaded successfully. ({storage_label})', 'success')
        return redirect(url_for('documents_list', folder=folder_id_up or ''))

    # Pass folder context to upload page
    from flask import g as _g
    _proj = getattr(_g, 'project', None)
    _fq   = DocumentFolder.query
    if _proj:
        _fq = _fq.filter_by(project_id=_proj.id)
    upload_folders = _fq.order_by(DocumentFolder.name).all()
    pre_folder     = request.args.get('folder', type=int)
    return render_template('document_upload.html',
                           upload_folders=upload_folders,
                           pre_folder=pre_folder,
                           r2_active=r2_storage.r2_enabled())


# ── R2 direct-upload: step 1 — browser requests a presigned PUT URL ──────────
@app.route('/documents/upload/presign', methods=['POST'])
@login_required
def document_upload_presign():
    """Return a presigned PUT URL so the browser can upload straight to R2."""
    if not r2_storage.r2_enabled():
        return jsonify({'error': 'R2 not configured'}), 400
    data         = request.get_json(force=True) or {}
    filename     = data.get('filename', 'file')
    content_type = data.get('content_type', 'application/octet-stream')
    from flask import g
    proj = getattr(g, 'project', None)
    key  = r2_storage.make_key(filename, proj.id if proj else None)
    url  = r2_storage.presigned_upload_url(key, content_type)
    return jsonify({'upload_url': url, 'key': key})


# ── R2 direct-upload: step 2 — browser confirms upload, save metadata ────────
@app.route('/documents/upload/complete', methods=['POST'])
@login_required
def document_upload_complete():
    """Save Document metadata after browser has uploaded the file directly to R2."""
    from flask import g
    proj      = getattr(g, 'project', None)
    file_key  = request.form.get('file_key', '').strip()
    filename  = request.form.get('filename', '').strip()
    file_size = request.form.get('file_size', 0, type=int)
    folder_id = request.form.get('folder_id', type=int) or None

    if not file_key or not filename:
        flash('Upload failed — missing file information.', 'danger')
        return redirect(url_for('document_upload'))

    ext   = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'bin'
    title = request.form.get('title', '').strip() or filename.rsplit('.', 1)[0]

    doc = Document(
        project_id        = proj.id if proj else None,
        folder_id         = folder_id,
        title             = title,
        description       = request.form.get('description', '').strip(),
        original_filename = filename,
        file_ext          = ext,
        file_size         = file_size,
        file_key          = file_key,
        file_data         = None,
        category          = request.form.get('category', 'general'),
        tags              = request.form.get('tags', '').strip(),
        uploaded_by       = current_user.id,
    )
    db.session.add(doc)
    db.session.commit()
    flash(f'"{doc.title}" uploaded successfully. ☁️ Stored in Cloudflare R2', 'success')
    return redirect(url_for('documents_list', folder=folder_id or ''))


@app.route('/documents/<int:doc_id>')
@login_required
def document_detail(doc_id):
    doc  = Document.query.filter_by(id=doc_id, is_active=True).first_or_404()
    from flask import g
    proj = getattr(g, 'project', None)

    # Build link target options (as simple {id, name} dicts for JS)
    wtg_options     = []
    pr_options      = []
    itp_options     = []
    qa_test_options = []

    if proj:
        for w in WTG.query.filter_by(project_id=proj.id).order_by(WTG.name).all():
            wtg_options.append({'id': w.id, 'name': w.name})

        for pr in (ProofRollRecord.query
                   .join(QATest).join(Area).join(WTG)
                   .filter(WTG.project_id == proj.id)
                   .order_by(ProofRollRecord.date.desc()).all()):
            try:
                label = f'{pr.qa_test.area.wtg.name} – {pr.qa_test.area.label} ({pr.date})'
            except Exception:
                label = f'Proof Roll #{pr.id}'
            pr_options.append({'id': pr.id, 'name': label})

        for it in (ITPRecord.query
                   .join(WTG, ITPRecord.wtg_id == WTG.id)
                   .filter(WTG.project_id == proj.id)
                   .order_by(ITPRecord.created_at.desc()).all()):
            wtg_obj = WTG.query.get(it.wtg_id)
            label   = f'{wtg_obj.name} / {it.itp_type.replace("_"," ").title()}' if wtg_obj else f'ITP #{it.id}'
            itp_options.append({'id': it.id, 'name': label})

        qa_test_options = []
        for qt in (QATest.query
                   .join(Area).join(WTG)
                   .filter(WTG.project_id == proj.id)
                   .order_by(QATest.id.desc()).all()):
            try:
                label = f'{qt.area.wtg.name} – {qt.area.label} / {qt.display_name}'
            except Exception:
                label = f'QA Test #{qt.id}'
            qa_test_options.append({'id': qt.id, 'name': label})

    # Enrich links with human-readable label
    link_labels = {}
    for lnk in doc.links:
        try:
            if lnk.link_type == 'wtg':
                w = WTG.query.get(lnk.link_id)
                link_labels[lnk.id] = w.name if w else f'WTG #{lnk.link_id}'
            elif lnk.link_type == 'proof_roll':
                pr = ProofRollRecord.query.get(lnk.link_id)
                link_labels[lnk.id] = (f'{pr.qa_test.area.wtg.name} – '
                                        f'{pr.qa_test.area.label} ({pr.date})') if pr else f'PR #{lnk.link_id}'
            elif lnk.link_type == 'itp_record':
                it  = ITPRecord.query.get(lnk.link_id)
                wtg = WTG.query.get(it.wtg_id) if it else None
                link_labels[lnk.id] = (f'{wtg.name} / {it.itp_type.replace("_"," ").title()}'
                                        if (it and wtg) else f'ITP #{lnk.link_id}')
            elif lnk.link_type == 'qa_test':
                qt = QATest.query.get(lnk.link_id)
                link_labels[lnk.id] = (f'{qt.display_name} – {qt.area.wtg.name} '
                                        f'{qt.area.label}') if qt else f'Test #{lnk.link_id}'
            else:
                link_labels[lnk.id] = 'Project (General)'
        except Exception:
            link_labels[lnk.id] = f'Record #{lnk.link_id}'

    return render_template('document_detail.html', doc=doc,
                           wtg_options=wtg_options, pr_options=pr_options,
                           itp_options=itp_options, qa_test_options=qa_test_options,
                           link_labels=link_labels)


@app.route('/documents/<int:doc_id>/view')
@login_required
def document_view(doc_id):
    """Open file inline in browser (PDF viewer / image). No Railway timeout."""
    doc = Document.query.filter_by(id=doc_id, is_active=True).first_or_404()
    if doc.stored_in_r2:
        url = r2_storage.presigned_url(doc.file_key, doc.original_filename,
                                        disposition='inline')
        return redirect(url)
    # Legacy: base64 in DB
    raw  = base64.b64decode(doc.file_data)
    resp = make_response(raw)
    resp.headers['Content-Type']        = doc.mime_type
    resp.headers['Content-Disposition'] = _content_disposition('inline', doc.original_filename)
    resp.headers['Cache-Control']       = 'private, max-age=3600'
    return resp


@app.route('/documents/<int:doc_id>/download')
@login_required
def document_download(doc_id):
    """Force-download the file."""
    doc = Document.query.filter_by(id=doc_id, is_active=True).first_or_404()
    if doc.stored_in_r2:
        url = r2_storage.presigned_url(doc.file_key, doc.original_filename,
                                        disposition='attachment')
        return redirect(url)
    # Legacy: base64 in DB
    raw  = base64.b64decode(doc.file_data)
    resp = make_response(raw)
    resp.headers['Content-Type']        = doc.mime_type
    resp.headers['Content-Disposition'] = _content_disposition('attachment', doc.original_filename)
    return resp


@app.route('/documents/<int:doc_id>/delete', methods=['POST'])
@login_required
def document_delete(doc_id):
    doc = Document.query.filter_by(id=doc_id, is_active=True).first_or_404()
    if current_user.role not in ('engineer','manager','admin') and doc.uploaded_by != current_user.id:
        abort(403)
    # Remove from R2 if stored there
    if doc.stored_in_r2:
        r2_storage.delete(doc.file_key)
    doc.is_active = False
    db.session.commit()
    flash(f'"{doc.title}" deleted.', 'success')
    return redirect(url_for('documents_list'))


@app.route('/documents/<int:doc_id>/link', methods=['POST'])
@login_required
def document_add_link(doc_id):
    doc       = Document.query.filter_by(id=doc_id, is_active=True).first_or_404()
    link_type = request.form.get('link_type','').strip()
    link_id   = request.form.get('link_id','0')
    note      = request.form.get('note','').strip()

    if not link_type or not link_id or link_id == '0':
        flash('Please choose a record to link.', 'danger')
        return redirect(url_for('document_detail', doc_id=doc_id))

    # Prevent duplicates
    exists = DocumentLink.query.filter_by(document_id=doc.id,
                                          link_type=link_type,
                                          link_id=int(link_id)).first()
    if not exists:
        db.session.add(DocumentLink(
            document_id = doc.id,
            link_type   = link_type,
            link_id     = int(link_id),
            note        = note,
            linked_by   = current_user.id,
        ))
        db.session.commit()
        flash('Link added.', 'success')
    else:
        flash('This link already exists.', 'info')
    return redirect(url_for('document_detail', doc_id=doc_id))


@app.route('/documents/links/<int:link_id>/delete', methods=['POST'])
@login_required
def document_remove_link(link_id):
    lnk = DocumentLink.query.get_or_404(link_id)
    doc_id = lnk.document_id
    db.session.delete(lnk)
    db.session.commit()
    flash('Link removed.', 'success')
    return redirect(url_for('document_detail', doc_id=doc_id))


# ── AJAX: get documents linked to a specific record ──────────────────────────
@app.route('/api/documents/for/<link_type>/<int:link_id>')
@login_required
def api_docs_for_record(link_type, link_id):
    links = DocumentLink.query.filter_by(link_type=link_type, link_id=link_id).all()
    result = []
    for lnk in links:
        doc = lnk.document
        if doc and doc.is_active:
            result.append({
                'id':       doc.id,
                'title':    doc.title,
                'file_ext': doc.file_ext,
                'file_size':doc.file_size_display,
                'category': doc.category_label,
                'cat_color':doc.category_color,
                'icon':     doc.icon_class,
                'icon_color':doc.icon_color,
                'can_preview': doc.can_preview,
                'note':     lnk.note or '',
                'link_id':  lnk.id,
                'view_url': url_for('document_view',    doc_id=doc.id),
                'dl_url':   url_for('document_download', doc_id=doc.id),
                'detail_url': url_for('document_detail', doc_id=doc.id),
            })
    return jsonify(result)


# ── AJAX: search documents in current project (for link-from-record modal) ───
@app.route('/api/documents/search')
@login_required
def api_docs_search():
    from flask import g
    proj  = getattr(g, 'project', None)
    q_str = request.args.get('q','').strip()
    q     = Document.query.filter_by(is_active=True)
    if proj:
        q = q.filter_by(project_id=proj.id)
    if q_str:
        q = q.filter(Document.title.ilike(f'%{q_str}%'))
    docs = q.order_by(Document.uploaded_at.desc()).limit(30).all()
    return jsonify([{
        'id': d.id, 'title': d.title,
        'file_ext': d.file_ext, 'category': d.category_label,
        'cat_color': d.category_color,
        'icon': d.icon_class, 'icon_color': d.icon_color,
        'file_size': d.file_size_display,
    } for d in docs])


# ─── Dashboard ───────────────────────────────────────────────────────────────
@app.route('/')
@login_required
def dashboard():
    wtgs = wtg_summary()
    total   = len(wtgs)
    complete = sum(1 for w in wtgs if w.completion_pct == 100)
    in_prog  = sum(1 for w in wtgs if 0 < w.completion_pct < 100)
    not_started = sum(1 for w in wtgs if w.completion_pct == 0)
    return render_template('dashboard.html',
                           wtgs=wtgs,
                           total=total,
                           complete=complete,
                           in_prog=in_prog,
                           not_started=not_started)

# ─── WTG Detail ──────────────────────────────────────────────────────────────
@app.route('/wtg/<int:wtg_id>')
@login_required
def wtg_detail(wtg_id):
    wtg   = WTG.query.get_or_404(wtg_id)
    areas = {a.area_type: a for a in wtg.areas}

    # Documents linked directly to this WTG
    wtg_links   = DocumentLink.query.filter_by(link_type='wtg', link_id=wtg.id).all()
    wtg_doc_ids = [lnk.document_id for lnk in wtg_links]
    wtg_docs    = Document.query.filter(Document.id.in_(wtg_doc_ids),
                                         Document.is_active == True).all() if wtg_doc_ids else []

    return render_template('wtg_detail.html', wtg=wtg, areas=areas, wtg_docs=wtg_docs)

# ─── QA Test detail / mark complete ──────────────────────────────────────────
@app.route('/test/<int:test_id>')
@login_required
def test_detail(test_id):
    test = QATest.query.get_or_404(test_id)
    area = test.area
    wtg  = area.wtg
    return render_template('test_detail.html', test=test, area=area, wtg=wtg)

# ─── Mark test complete (engineer only) ───────────────────────────────────────
@app.route('/test/<int:test_id>/complete', methods=['POST'])
@login_required
def mark_complete(test_id):
    if not current_user.can_enter_data():
        abort(403)
    test = QATest.query.get_or_404(test_id)
    test.is_complete  = True
    test.completed_at = datetime.utcnow()
    test.completed_by = current_user.id
    db.session.commit()
    flash(f'"{test.display_name}" marked as complete.', 'success')
    return redirect(url_for('wtg_detail', wtg_id=test.area.wtg_id))

# ─── Test Record Entry ────────────────────────────────────────────────────────
@app.route('/test/<int:test_id>/record', methods=['GET','POST'])
@login_required
def test_record(test_id):
    if not current_user.can_enter_data():
        abort(403)
    test = QATest.query.get_or_404(test_id)
    area = test.area
    wtg  = area.wtg

    if request.method == 'POST':
        rec = TestRecord(
            qa_test_id   = test.id,
            test_date    = datetime.strptime(request.form['test_date'], '%Y-%m-%d').date(),
            lot_number   = request.form.get('lot_number',''),
            lab_ref      = request.form.get('lab_ref',''),
            result       = request.form.get('result','pending'),
            result_value = float(request.form['result_value']) if request.form.get('result_value') else None,
            result_unit  = request.form.get('result_unit',''),
            spec_value   = float(request.form['spec_value'])   if request.form.get('spec_value')   else None,
            comments     = request.form.get('comments',''),
            entered_by   = current_user.id,
            signature_data = request.form.get('signature_data',''),
        )
        db.session.add(rec)

        # Auto-mark complete if pass
        if rec.result == 'pass':
            test.is_complete  = True
            test.completed_at = datetime.utcnow()
            test.completed_by = current_user.id

        db.session.commit()
        flash('Test record saved successfully!', 'success')
        return redirect(url_for('wtg_detail', wtg_id=wtg.id))

    return render_template('test_record.html', test=test, area=area, wtg=wtg, today=date.today().isoformat())

# ─── Proof Roll Form ──────────────────────────────────────────────────────────
@app.route('/proof-rolls')
@login_required
def proof_roll_index():
    """Proof Rolling landing page — shows all WTGs with proof roll status."""
    wtgs = WTG.query.order_by(WTG.name).all()
    summary = []
    total_records = total_passed = total_failed = total_pending = 0

    # Pre-fetch document link counts keyed by proof_roll id
    from sqlalchemy import func
    pr_doc_counts = {}
    for row in (db.session.query(DocumentLink.link_id, func.count(DocumentLink.id))
                .filter_by(link_type='proof_roll')
                .group_by(DocumentLink.link_id).all()):
        pr_doc_counts[row[0]] = row[1]

    for wtg in wtgs:
        wtg_entry = {'wtg': wtg, 'areas': []}
        for area in sorted(wtg.areas, key=lambda a: ['hardstand','crane_pad','boom_pad','blade_fingers'].index(a.area_type) if a.area_type in ['hardstand','crane_pad','boom_pad','blade_fingers'] else 99):
            pr_tests = [t for t in area.required_tests if t.test_type.startswith('proof_roll')]
            if not pr_tests:
                continue
            area_rows = []
            for test in pr_tests:
                recs   = test.proof_rolls
                latest = recs[-1] if recs else None
                status = latest.passed if latest else 'pending'
                doc_count = pr_doc_counts.get(latest.id, 0) if latest else 0
                area_rows.append({'test': test, 'count': len(recs), 'latest': latest,
                                  'status': status, 'doc_count': doc_count})
                total_records += len(recs)
                total_passed  += sum(1 for r in recs if r.passed == 'yes')
                total_failed  += sum(1 for r in recs if r.passed == 'no')
                total_pending += (1 if not recs else 0)
            wtg_entry['areas'].append({'area': area, 'tests': area_rows})
        summary.append(wtg_entry)

    return render_template('proof_roll_index.html',
                           summary=summary,
                           total_records=total_records,
                           total_passed=total_passed,
                           total_failed=total_failed,
                           total_pending=total_pending)


@app.route('/proof-roll/upload-photo', methods=['POST'])
@login_required
def proof_roll_upload_photo():
    """Receive a single compressed base64 photo, store in temp table, return its ID.
    Each photo is uploaded immediately when the user picks it so the final form
    POST only carries text fields — no risk of hitting proxy body-size limits."""
    payload = request.get_json(force=True, silent=True) or {}
    photo_type = payload.get('photo_type', 'site')
    image_data = (payload.get('image_data') or '').strip()

    if not image_data or not image_data.startswith('data:image'):
        return jsonify({'error': 'invalid image data'}), 400

    tmp = TempPhotoUpload(
        photo_type  = photo_type,
        image_data  = image_data,
        taken_at    = datetime.now(timezone.utc),
        uploaded_by = current_user.id,
    )
    db.session.add(tmp)
    db.session.commit()
    return jsonify({'id': tmp.id, 'ok': True})


@app.route('/test/<int:test_id>/proof-roll', methods=['GET','POST'])
@login_required
def proof_roll_form(test_id):
    if not current_user.can_enter_data():
        abort(403)
    test = QATest.query.get_or_404(test_id)
    area = test.area
    wtg  = area.wtg

    if request.method == 'POST':
        # Parse rectification date safely
        rect_date_str = request.form.get('rectification_date','').strip()
        rect_date = datetime.strptime(rect_date_str, '%Y-%m-%d').date() if rect_date_str else None

        pr = ProofRollRecord(
            qa_test_id            = test.id,
            location              = request.form.get('location', f'{wtg.name} – {area.label}'),
            date                  = datetime.strptime(request.form['date'], '%Y-%m-%d').date(),
            pavement_area         = request.form.get('pavement_area',''),
            pavement_material     = request.form.get('pavement_material',''),
            material_layer        = request.form.get('material_layer',''),
            lot_number            = request.form.get('lot_number',''),
            comments              = request.form.get('comments',''),
            rectification_method  = request.form.get('rectification_method',''),
            rectification_date    = rect_date,
            passed                = request.form.get('passed',''),
            entered_by            = current_user.id,
        )
        db.session.add(pr)
        db.session.flush()  # get pr.id

        # ── Equipment rows (dynamic, array inputs) ──────────────────────
        names   = request.form.getlist('equip_name[]')
        masses  = request.form.getlist('equip_mass[]')
        values  = request.form.getlist('equip_value[]')
        passes_ = request.form.getlist('equip_passes[]')
        for i, ename in enumerate(names):
            ename = ename.strip()
            if not ename:
                continue
            eq = ProofRollEquipment(
                proof_roll_id  = pr.id,
                equipment_name = ename,
                mass_tonnes    = masses[i].strip()  if i < len(masses)  else '',
                value          = values[i].strip()  if i < len(values)  else '',
                passes         = passes_[i].strip() if i < len(passes_) else '',
                sort_order     = i,
            )
            db.session.add(eq)

        # ── Site photos (pre-uploaded via AJAX → TempPhotoUpload) ───────
        site_ids = []
        for raw in request.form.getlist('photo_id[]'):
            raw = raw.strip()
            if raw.isdigit():
                site_ids.append(int(raw))
        for ph_id in site_ids:
            tmp = db.session.get(TempPhotoUpload, ph_id)
            if tmp and tmp.uploaded_by == current_user.id and tmp.photo_type == 'site':
                db.session.add(ProofRollPhoto(
                    proof_roll_id = pr.id,
                    image_data    = tmp.image_data,
                    taken_at      = tmp.taken_at,
                    uploaded_by   = tmp.uploaded_by,
                ))
                db.session.delete(tmp)

        # ── Rectification photos ─────────────────────────────────────────
        rect_ids = []
        for raw in request.form.getlist('rect_photo_id[]'):
            raw = raw.strip()
            if raw.isdigit():
                rect_ids.append(int(raw))
        for ph_id in rect_ids:
            tmp = db.session.get(TempPhotoUpload, ph_id)
            if tmp and tmp.uploaded_by == current_user.id and tmp.photo_type == 'rect':
                db.session.add(ProofRollRectPhoto(
                    proof_roll_id = pr.id,
                    image_data    = tmp.image_data,
                    taken_at      = tmp.taken_at,
                    uploaded_by   = tmp.uploaded_by,
                ))
                db.session.delete(tmp)

        # ── Signatories (2: TCS rep + Client rep) ───────────────────────
        for i in range(1, 3):
            name     = request.form.get(f'sig_name_{i}','').strip()
            company  = request.form.get(f'sig_company_{i}','').strip()
            sig_data = request.form.get(f'sig_data_{i}','').strip()
            sig_date = request.form.get(f'sig_date_{i}','').strip()
            role     = request.form.get(f'sig_role_{i}','').strip()
            if name:
                sig = ProofRollSignatory(
                    proof_roll_id  = pr.id,
                    name           = name,
                    company        = company,
                    signature_data = sig_data,
                    signed_date    = datetime.strptime(sig_date, '%Y-%m-%d').date() if sig_date else None,
                    role           = role,
                )
                db.session.add(sig)

        # Auto-mark complete if passed
        if pr.passed == 'yes':
            test.is_complete  = True
            test.completed_at = datetime.utcnow()
            test.completed_by = current_user.id

        db.session.commit()
        flash('Proof Roll Record saved successfully!', 'success')
        return redirect(url_for('view_proof_roll', pr_id=pr.id))

    return render_template('proof_roll.html',
                           test=test, area=area, wtg=wtg,
                           today=date.today().isoformat(),
                           existing=test.proof_rolls)

# ─── View saved proof roll ────────────────────────────────────────────────────
@app.route('/proof-roll/<int:pr_id>')
@login_required
def view_proof_roll(pr_id):
    pr   = ProofRollRecord.query.get_or_404(pr_id)
    test = pr.qa_test
    area = test.area
    wtg  = area.wtg

    # Linked documents (via DocumentLink)
    pr_links   = DocumentLink.query.filter_by(link_type='proof_roll', link_id=pr.id).all()
    test_links = DocumentLink.query.filter_by(link_type='qa_test',    link_id=test.id).all()
    all_link_ids = list({lnk.document_id for lnk in pr_links + test_links})
    linked_docs  = Document.query.filter(Document.id.in_(all_link_ids),
                                         Document.is_active == True).all() if all_link_ids else []

    return render_template('proof_roll_view.html', pr=pr, test=test, area=area, wtg=wtg,
                           linked_docs=linked_docs)

# ─── PDF export — branded, printable ────────────────────────────────────────
@app.route('/proof-roll/<int:pr_id>/pdf')
@login_required
def proof_roll_pdf(pr_id):
    pr   = ProofRollRecord.query.get_or_404(pr_id)
    test = pr.qa_test
    area = test.area
    wtg  = area.wtg
    return render_template('proof_roll_pdf.html', pr=pr, test=test, area=area, wtg=wtg)

# ─── API: QA status JSON ──────────────────────────────────────────────────────
@app.route('/api/wtg/<int:wtg_id>/status')
@login_required
def api_wtg_status(wtg_id):
    wtg   = WTG.query.get_or_404(wtg_id)
    areas = {}
    for a in wtg.areas:
        areas[a.area_type] = {
            'label': a.label,
            'pct'  : a.completion_pct,
            'color': a.status_color,
            'tests': [
                {'id': t.id, 'name': t.display_name,
                 'complete': t.is_complete,
                 'is_proof_roll': t.test_type.startswith('proof_roll')}
                for t in a.required_tests
            ]
        }
    return jsonify({'wtg': wtg.name, 'pct': wtg.completion_pct, 'areas': areas})

# ─── API: All WTGs summary ────────────────────────────────────────────────────
@app.route('/api/dashboard')
@login_required
def api_dashboard():
    wtgs = WTG.query.order_by(WTG.name).all()
    return jsonify([{'id':w.id,'name':w.name,'pct':w.completion_pct} for w in wtgs])

# ─── Interactive Map ─────────────────────────────────────────────────────────
@app.route('/map')
@login_required
def map_view():
    wtgs = WTG.query.order_by(WTG.name).all()
    return render_template('map.html', wtgs=wtgs)

@app.route('/api/kml/geojson')
@login_required
def api_kml_geojson():
    """Serve parsed KML as GeoJSON layers."""
    data = get_geojson(use_cache=True)
    return jsonify(data)

@app.route('/api/kml/refresh')
@login_required
def api_kml_refresh():
    """Force re-parse KML (clears cache)."""
    cache = os.path.join(BASE_DIR, 'static', 'kml_cache.json')
    if os.path.exists(cache):
        os.remove(cache)
    data = get_geojson(use_cache=False)
    return jsonify({'ok': True, 'counts': {k: len(v['features']) for k,v in data.items()}})

# ─── Photo Upload ─────────────────────────────────────────────────────────────
@app.route('/test/<int:test_id>/photo', methods=['POST'])
@login_required
def upload_photo(test_id):
    if not current_user.can_enter_data():
        abort(403)
    test = QATest.query.get_or_404(test_id)

    file       = request.files.get('photo')
    caption    = request.form.get('caption', '')
    taken_date = request.form.get('taken_date', date.today().isoformat())

    if not file or file.filename == '':
        return jsonify({'error': 'No file'}), 400
    if not allowed_image(file.filename):
        return jsonify({'error': 'Invalid file type'}), 400

    ext      = file.filename.rsplit('.', 1)[1].lower()
    fname    = f"{uuid.uuid4().hex}.{ext}"
    rel_path = f"photos/{fname}"
    file.save(os.path.join(PHOTO_DIR, fname))

    try:
        td = datetime.strptime(taken_date, '%Y-%m-%d').date()
    except ValueError:
        td = date.today()

    photo = TestPhoto(
        qa_test_id  = test.id,
        file_path   = rel_path,
        caption     = caption,
        taken_date  = td,
        uploaded_by = current_user.id,
    )
    db.session.add(photo)
    db.session.commit()

    return jsonify({
        'id':         photo.id,
        'url':        photo.url,
        'caption':    photo.caption,
        'taken_date': photo.taken_date.isoformat(),
    })

@app.route('/api/test/<int:test_id>/photos')
@login_required
def api_test_photos(test_id):
    photos = TestPhoto.query.filter_by(qa_test_id=test_id).order_by(TestPhoto.taken_date).all()
    return jsonify([{
        'id':         p.id,
        'url':        p.url,
        'caption':    p.caption,
        'taken_date': p.taken_date.isoformat(),
    } for p in photos])

@app.route('/api/wtg/<int:wtg_id>/photos')
@login_required
def api_wtg_photos(wtg_id):
    """All photos across all tests for this WTG, grouped by date."""
    wtg = WTG.query.get_or_404(wtg_id)
    photos = (db.session.query(TestPhoto, QATest, Area)
              .join(QATest, QATest.id == TestPhoto.qa_test_id)
              .join(Area,   Area.id   == QATest.area_id)
              .filter(Area.wtg_id == wtg_id)
              .order_by(TestPhoto.taken_date)
              .all())

    by_date = {}
    for photo, test, area in photos:
        key = photo.taken_date.isoformat()
        by_date.setdefault(key, []).append({
            'id':        photo.id,
            'url':       photo.url,
            'caption':   photo.caption,
            'test_name': test.display_name,
            'area':      area.label,
        })
    return jsonify(by_date)

# ─── Zone storage (drawn polygons) ───────────────────────────────────────────
@app.route('/api/zones', methods=['POST'])
@login_required
def api_save_zone():
    data       = request.get_json()
    zones_file = os.path.join(BASE_DIR, 'static', 'zones.json')
    zones = []
    if os.path.exists(zones_file):
        with open(zones_file) as f:
            try: zones = json.load(f)
            except: zones = []
    zones.append(data)
    with open(zones_file, 'w') as f:
        json.dump(zones, f)
    return jsonify({'ok': True, 'count': len(zones)})

@app.route('/api/zones')
@login_required
def api_get_zones():
    zones_file = os.path.join(BASE_DIR, 'static', 'zones.json')
    if not os.path.exists(zones_file):
        return jsonify([])
    with open(zones_file) as f:
        try: return jsonify(json.load(f))
        except: return jsonify([])

# ─── ITP ─────────────────────────────────────────────────────────────────────

@app.route('/itp')
@login_required
def itp_index():
    """Landing: choose WTG + ITP type."""
    wtgs = WTG.query.order_by(WTG.name).all()
    # Existing ITP records so we can show status badges
    records = ITPRecord.query.all()
    by_key = {(r.wtg_id, r.itp_type): r for r in records}
    return render_template('itp_index.html',
                           wtgs=wtgs,
                           itp_types=list(ITP_DEFINITIONS.keys()),
                           itp_defs=ITP_DEFINITIONS,
                           by_key=by_key)


@app.route('/itp/<int:wtg_id>/<itp_type>', methods=['GET', 'POST'])
@login_required
def itp_detail(wtg_id, itp_type):
    """Full ITP checklist view + per-criterion sign-off."""
    if itp_type not in ITP_DEFINITIONS:
        abort(404)
    wtg  = WTG.query.get_or_404(wtg_id)
    defn = ITP_DEFINITIONS[itp_type]

    # Get or create ITPRecord
    record = ITPRecord.query.filter_by(wtg_id=wtg_id, itp_type=itp_type).first()
    if not record:
        record = ITPRecord(wtg_id=wtg_id, itp_type=itp_type,
                           created_by=current_user.id, status='draft')
        db.session.add(record)
        db.session.flush()

    # Ensure per-criterion rows exist (create on first access)
    existing_keys = {(s.item_no, s.criterion_index) for s in record.item_statuses}
    needs_rows = False
    for item in defn['items']:
        for ci, crit in enumerate(item['criteria']):
            if (item['no'], ci) not in existing_keys:
                needs_rows = True
                row = item['rows'][ci] if ci < len(item['rows']) else {}
                db.session.add(ITPItemStatus(
                    itp_record_id   = record.id,
                    item_no         = item['no'],
                    criterion_index = ci,
                    activity        = item['activity'],
                    criterion_text  = crit,
                    inspection_code = row.get('inspection', ''),
                    frequency       = row.get('frequency', ''),
                ))
    if needs_rows:
        db.session.commit()

    # Index statuses by (item_no, criterion_index) tuple
    statuses = {(s.item_no, s.criterion_index): s for s in record.item_statuses}

    if request.method == 'POST':
        action = request.form.get('action')

        # ── Save lot/location metadata ────────────────────────────────────
        if action == 'save_meta':
            record.lot_number = request.form.get('lot_number', '').strip()
            record.location   = request.form.get('location', '').strip()
            record.engineer_name    = request.form.get('engineer_name', 'Lucas').strip()
            record.engineer_company = request.form.get('engineer_company', 'CBOP').strip()
            db.session.commit()
            flash('ITP details saved.', 'success')
            return redirect(url_for('itp_detail', wtg_id=wtg_id, itp_type=itp_type))

        # ── Invite client ──────────────────────────────────────────────────
        elif action == 'invite_client':
            client_id    = request.form.get('client_id', '')
            client_email = request.form.get('client_email', '').strip()
            client_info  = next((c for c in CLIENTS if c['id'] == client_id), None)
            if not client_info:
                flash('Please select a client.', 'danger')
                return redirect(url_for('itp_detail', wtg_id=wtg_id, itp_type=itp_type))

            token = uuid.uuid4().hex
            record.client_name       = client_info['name']
            record.client_company    = client_info['company']
            record.client_email      = client_email
            record.client_token      = token
            record.client_invited_at = datetime.now(timezone.utc)
            record.engineer_signed_at = record.engineer_signed_at or datetime.now(timezone.utc)
            record.status            = 'client_invited'
            db.session.commit()

            sign_url = url_for('itp_client_sign', token=token, _external=True)

            # ── Send email invitation ──────────────────────────────────────
            if client_email:
                sent = email_client_invitation(
                    record   = record,
                    wtg_name = wtg.name,
                    sign_url = sign_url,
                    client_name  = client_info['name'],
                    client_email = client_email,
                )
                if sent:
                    flash(f'Invitation email sent to {client_email}! They can sign without logging in.', 'success')
                else:
                    flash(f'Client link generated for {client_info["name"]}. Email could not be sent — copy the link below manually.', 'warning')
            else:
                flash(f'Client link generated for {client_info["name"]}! Copy link below.', 'success')
            return redirect(url_for('itp_detail', wtg_id=wtg_id, itp_type=itp_type))

    now = datetime.now()
    now_dt = now.strftime('%Y-%m-%dT%H:%M')

    # Linked documents for this ITP record
    itp_links   = DocumentLink.query.filter_by(link_type='itp_record', link_id=record.id).all()
    itp_doc_ids = [lnk.document_id for lnk in itp_links]
    itp_linked_docs = Document.query.filter(Document.id.in_(itp_doc_ids),
                                             Document.is_active == True).all() if itp_doc_ids else []

    return render_template('itp_detail.html',
                           wtg=wtg,
                           itp_type=itp_type,
                           defn=defn,
                           record=record,
                           statuses=statuses,
                           clients=CLIENTS,
                           today=date.today().isoformat(),
                           now_dt=now_dt,
                           linked_docs=itp_linked_docs)


@app.route('/itp/client/<token>', methods=['GET', 'POST'])
def itp_client_sign(token):
    """Public page for client to review + sign the ITP."""
    record   = ITPRecord.query.filter_by(client_token=token).first_or_404()
    wtg      = record.wtg
    defn     = ITP_DEFINITIONS.get(record.itp_type, {})
    statuses = {(s.item_no, s.criterion_index): s for s in record.item_statuses}

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'client_sign':
            sig = request.form.get('client_signature', '').strip()
            if not sig:
                flash('Please draw your signature.', 'danger')
            else:
                record.client_signature = sig
                record.client_signed_at = datetime.now(timezone.utc)
                record.status           = 'complete'
                db.session.commit()

                # ── Notify internal team ───────────────────────────────────
                from models import User
                notify = User.query.filter(
                    User.role.in_(['engineer', 'supervisor', 'manager'])
                ).all()
                email_client_signed(
                    record       = record,
                    wtg_name     = wtg.name,
                    client_name  = record.client_name or 'Client',
                    notify_users = notify,
                )

                flash('ITP signed successfully. Thank you!', 'success')
            return redirect(url_for('itp_client_sign', token=token))

    return render_template('itp_client_sign.html',
                           record=record,
                           wtg=wtg,
                           defn=defn,
                           statuses=statuses,
                           today=date.today().isoformat())


@app.route('/api/itp/<int:record_id>/sign/<item_no>/<int:crit_idx>', methods=['POST'])
@login_required
def api_itp_sign_criterion(record_id, item_no, crit_idx):
    """AJAX: Save engineer signature for one criterion (with date + time)."""
    s = ITPItemStatus.query.filter_by(
        itp_record_id=record_id, item_no=item_no, criterion_index=crit_idx
    ).first_or_404()
    data     = request.get_json() or {}
    sig      = data.get('signature', '').strip()
    comments = data.get('comments', '').strip()
    dt_str   = data.get('datetime', '').strip()   # ISO datetime-local string

    if not sig:
        return jsonify({'error': 'No signature data'}), 400

    s.lucas_complete  = True
    s.lucas_signature = sig
    s.lucas_comments  = comments
    try:
        # datetime-local sends "YYYY-MM-DDTHH:MM"
        s.lucas_signed_at = datetime.strptime(dt_str[:16], '%Y-%m-%dT%H:%M')
    except (ValueError, TypeError):
        s.lucas_signed_at = datetime.now()

    # Update ITP record status from draft → in_progress
    record = ITPRecord.query.get(record_id)
    if record and record.status == 'draft':
        record.status = 'in_progress'

    db.session.commit()
    return jsonify({
        'ok':        True,
        'signature': s.lucas_signature,
        'datetime':  s.lucas_signed_at.strftime('%Y-%m-%dT%H:%M'),
        'display':   s.lucas_signed_at.strftime('%d %b %Y %H:%M'),
        'comments':  s.lucas_comments,
    })


@app.route('/api/itp/<int:record_id>/unsign/<item_no>/<int:crit_idx>', methods=['POST'])
@login_required
def api_itp_unsign_criterion(record_id, item_no, crit_idx):
    """AJAX: Remove engineer signature from one criterion."""
    s = ITPItemStatus.query.filter_by(
        itp_record_id=record_id, item_no=item_no, criterion_index=crit_idx
    ).first_or_404()
    s.lucas_complete   = False
    s.lucas_signature  = None
    s.lucas_signed_at  = None
    s.lucas_comments   = None
    db.session.commit()
    return jsonify({'ok': True})


# ITP item document upload
def get_itp_docs_dir():
    return os.path.join(os.environ.get('DATA_DIR', os.path.join(BASE_DIR,'static')), 'itp_item_docs')

@app.route('/api/itp/<int:record_id>/item/<item_no>/<int:crit_idx>/upload', methods=['POST'])
@login_required
def api_itp_item_upload(record_id, item_no, crit_idx):
    """Upload a document/photo to a specific ITP criterion."""
    from models import ITPItemDocument
    s = ITPItemStatus.query.filter_by(
        itp_record_id=record_id, item_no=item_no, criterion_index=crit_idx
    ).first_or_404()
    f = request.files.get('file')
    if not f or f.filename == '':
        return jsonify({'error': 'No file'}), 400
    ext  = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'file'
    fname = f'{record_id}_{item_no}_{crit_idx}_{uuid.uuid4().hex[:8]}.{ext}'
    ITP_ITEM_DOCS_DIR = get_itp_docs_dir()
    os.makedirs(ITP_ITEM_DOCS_DIR, exist_ok=True)
    fpath = os.path.join(ITP_ITEM_DOCS_DIR, fname)
    f.save(fpath)
    dtype = 'photo' if ext in ('png','jpg','jpeg','gif','webp','heic') else ('pdf' if ext=='pdf' else 'file')
    doc = ITPItemDocument(
        item_status_id=s.id,
        itp_record_id=record_id,
        original_name=f.filename,
        filename=fname,
        url=f'/static/itp_item_docs/{fname}',
        doc_type=dtype,
        uploaded_by=current_user.id,
    )
    db.session.add(doc)
    db.session.commit()
    return jsonify({'id': doc.id, 'name': doc.original_name, 'url': doc.url, 'type': doc.doc_type})


@app.route('/api/itp/item-doc/<int:doc_id>/delete', methods=['POST'])
@login_required
def api_itp_item_doc_delete(doc_id):
    from models import ITPItemDocument
    doc = ITPItemDocument.query.get_or_404(doc_id)
    try:
        fpath = os.path.join(get_itp_docs_dir(), doc.filename)
        if os.path.exists(fpath):
            os.remove(fpath)
    except Exception:
        pass
    db.session.delete(doc)
    db.session.commit()
    return jsonify({'ok': True})


# ITP Print/PDF route
@app.route('/itp/<int:record_id>/print')
@login_required
def itp_print(record_id):
    """Render a print-friendly ITP for PDF download."""
    record   = ITPRecord.query.get_or_404(record_id)
    defn     = ITP_DEFINITIONS.get(record.itp_type)
    if not defn:
        return 'ITP type not found', 404
    statuses = {(s.item_no, s.criterion_index): s for s in record.item_statuses}
    return render_template('itp_print.html', record=record, defn=defn,
                           statuses=statuses, wtg=record.wtg)


# ITP bulk export page
@app.route('/itp/export')
@login_required
def itp_export():
    """Page to select and bulk-download ITPs as PDFs."""
    wtgs    = WTG.query.order_by(WTG.name).all()
    records = ITPRecord.query.order_by(ITPRecord.wtg_id, ITPRecord.itp_type).all()
    return render_template('itp_export.html', wtgs=wtgs, records=records,
                           itp_types=list(ITP_DEFINITIONS.keys()))


@app.route('/itp/export-zip', methods=['POST'])
@login_required
def itp_export_zip():
    """Generate a ZIP of self-contained HTML print pages for selected ITP records."""
    import zipfile, io as _io
    data       = request.get_json() or {}
    record_ids = data.get('ids', [])
    if not record_ids:
        return jsonify({'error': 'No ITP records selected'}), 400

    zip_buf = _io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for rid in record_ids:
            record = ITPRecord.query.get(rid)
            if not record:
                continue
            defn = ITP_DEFINITIONS.get(record.itp_type)
            if not defn:
                continue
            statuses = {(s.item_no, s.criterion_index): s for s in record.item_statuses}
            # Render the print template to a string
            html_content = render_template(
                'itp_print.html',
                record=record, defn=defn,
                statuses=statuses, wtg=record.wtg
            )
            # Remove the auto-print JS so HTML files don't auto-trigger print
            html_content = html_content.replace(
                "window.addEventListener('load', () => setTimeout(() => window.print(), 900));",
                "// Auto-print disabled in ZIP export — use Ctrl+P to print/save as PDF"
            )
            fname = f"{record.wtg.name}_{record.itp_type}_{record.lot_number or 'NoLot'}.html"
            fname = fname.replace('/', '-').replace(' ', '_')
            zf.writestr(fname, html_content)

    zip_buf.seek(0)
    from flask import send_file
    return send_file(
        zip_buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name='King_Rocks_ITPs.zip'
    )


# ══════════════════════════════════════════════════════════════════════════════
# FOUNDATION SECTION
# ══════════════════════════════════════════════════════════════════════════════

def get_foundation_docs_dir():
    return os.path.join(os.environ.get('DATA_DIR', os.path.join(BASE_DIR,'static')), 'foundation_docs')

ALLOWED_DOCS = {'pdf','png','jpg','jpeg','gif','webp','docx','xlsx','heic','dwg'}

def allowed_doc(filename):
    return '.' in filename and filename.rsplit('.',1)[1].lower() in ALLOWED_DOCS


def get_active_stages():
    """Return stage list from DB templates if seeded, otherwise fall back to hardcoded."""
    templates = FoundationStageTemplate.query.order_by(FoundationStageTemplate.sort_order).all()
    if templates:
        return [(t.stage_key, t.stage_label) for t in templates]
    return FOUNDATION_STAGES


@app.route('/foundation')
@login_required
def foundation_index():
    wtgs      = WTG.query.order_by(WTG.name).all()
    active_stages = get_active_stages()
    # Ensure all foundation stages exist for every WTG
    existing = {(s.wtg_id, s.stage_key) for s in FoundationStage.query.all()}
    new_stages = []
    for wtg in wtgs:
        for key, label in active_stages:
            if (wtg.id, key) not in existing:
                new_stages.append(FoundationStage(wtg_id=wtg.id, stage_key=key, stage_label=label))
    if new_stages:
        db.session.add_all(new_stages)
        db.session.commit()
    all_stages = FoundationStage.query.all()
    # Build stage_map: {wtg_id: {stage_key: stage_obj}}
    stage_map = {}
    for s in all_stages:
        stage_map.setdefault(s.wtg_id, {})[s.stage_key] = s
    # Aggregate counts — only count active stages
    active_keys = {k for k, _ in active_stages}
    active_all  = [s for s in all_stages if s.stage_key in active_keys]
    total_stages      = len(active_all)
    complete_stages   = sum(1 for s in active_all if s.status == 'complete')
    in_progress_stages= sum(1 for s in active_all if s.status == 'in_progress')
    not_started_stages= sum(1 for s in active_all if s.status == 'not_started')
    return render_template('foundation_index.html', wtgs=wtgs, stage_map=stage_map,
                           stages=active_stages,
                           total_stages=total_stages, complete_stages=complete_stages,
                           in_progress_stages=in_progress_stages,
                           not_started_stages=not_started_stages)


@app.route('/foundation/<int:wtg_id>')
@login_required
def foundation_detail(wtg_id):
    wtg    = WTG.query.get_or_404(wtg_id)
    active_stages = get_active_stages()
    stages = {s.stage_key: s for s in
              FoundationStage.query.filter_by(wtg_id=wtg_id).order_by(FoundationStage.id).all()}
    # Ensure all stages exist
    for key, label in active_stages:
        if key not in stages:
            s = FoundationStage(wtg_id=wtg_id, stage_key=key, stage_label=label)
            db.session.add(s)
    db.session.commit()
    stages = {s.stage_key: s for s in
              FoundationStage.query.filter_by(wtg_id=wtg_id).all()}
    return render_template('foundation_detail.html', wtg=wtg,
                           stages=stages, stage_order=active_stages,
                           today=date.today().isoformat())


@app.route('/api/foundation/<int:stage_id>/update', methods=['POST'])
@login_required
def api_foundation_update(stage_id):
    if not current_user.can_enter_data():
        return jsonify({'error': 'Forbidden'}), 403
    stage  = FoundationStage.query.get_or_404(stage_id)
    data   = request.get_json() or {}
    status = data.get('status')
    if status in ('not_started','in_progress','in_review','approved','complete','na'):
        stage.status = status
    if data.get('notes')        is not None: stage.notes       = data['notes']
    if data.get('lot_number')   is not None: stage.lot_number  = data['lot_number']
    if data.get('reference_no') is not None: stage.reference_no= data['reference_no']
    if data.get('date_completed'):
        try:
            stage.date_completed = datetime.strptime(data['date_completed'],'%Y-%m-%d').date()
        except ValueError: pass
    if data.get('result_json')  is not None: stage.result_json = data['result_json']
    stage.entered_by  = current_user.id
    stage.updated_at  = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({'ok': True, 'status': stage.status})


@app.route('/api/foundation/<int:stage_id>/upload', methods=['POST'])
@login_required
def api_foundation_upload(stage_id):
    if not current_user.can_enter_data():
        return jsonify({'error': 'Forbidden'}), 403
    stage   = FoundationStage.query.get_or_404(stage_id)
    f       = request.files.get('file')
    caption = request.form.get('caption', '')
    if not f or f.filename == '':
        return jsonify({'error': 'No file'}), 400
    if not allowed_doc(f.filename):
        return jsonify({'error': 'File type not allowed'}), 400
    ext      = f.filename.rsplit('.',1)[1].lower()
    fname    = f"{uuid.uuid4().hex}.{ext}"
    rel_path = f"foundation_docs/{fname}"
    FOUNDATION_DOCS_DIR = get_foundation_docs_dir()
    os.makedirs(FOUNDATION_DOCS_DIR, exist_ok=True)
    f.save(os.path.join(FOUNDATION_DOCS_DIR, fname))
    doc = FoundationDocument(
        stage_id=stage_id, file_path=rel_path,
        original_name=secure_filename(f.filename),
        doc_type='photo' if ext in {'png','jpg','jpeg','gif','webp','heic'} else 'document',
        caption=caption, uploaded_by=current_user.id
    )
    db.session.add(doc)
    db.session.commit()
    return jsonify({'ok': True, 'id': doc.id, 'url': doc.url,
                    'name': doc.original_name, 'doc_type': doc.doc_type})


@app.route('/api/foundation/<int:stage_id>/docs')
@login_required
def api_foundation_docs(stage_id):
    docs = FoundationDocument.query.filter_by(stage_id=stage_id).all()
    return jsonify([{'id': d.id, 'url': d.url, 'name': d.original_name,
                     'doc_type': d.doc_type, 'caption': d.caption} for d in docs])


@app.route('/api/foundation/<int:stage_id>/doc/<int:doc_id>/delete', methods=['POST'])
@login_required
def api_foundation_doc_delete(stage_id, doc_id):
    if not current_user.can_enter_data():
        return jsonify({'error': 'Forbidden'}), 403
    doc = FoundationDocument.query.filter_by(id=doc_id, stage_id=stage_id).first_or_404()
    full = os.path.join(BASE_DIR, 'static', doc.file_path)
    if os.path.exists(full):
        os.remove(full)
    db.session.delete(doc)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/wtg/<int:wtg_id>/foundation/summary')
@login_required
def api_foundation_summary(wtg_id):
    wtg    = WTG.query.get_or_404(wtg_id)
    stages = FoundationStage.query.filter_by(wtg_id=wtg_id).all()
    total  = len(stages)
    done   = sum(1 for s in stages if s.status == 'complete')
    return jsonify({
        'wtg': wtg.name, 'total': total, 'complete': done,
        'pct': int(done/total*100) if total else 0,
        'stages': [{'key': s.stage_key, 'label': s.stage_label,
                    'status': s.status, 'date': s.date_completed.isoformat() if s.date_completed else None,
                    'ref': s.reference_no} for s in stages]
    })


# ══════════════════════════════════════════════════════════════════════════════
# FOUNDATION STAGE SETTINGS (admin: edit global stage list)
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/foundation/stage-settings')
@login_required
def foundation_stage_settings():
    if current_user.role != 'admin':
        flash('Admin access required.', 'error')
        return redirect(url_for('foundation_index'))
    templates = FoundationStageTemplate.query.order_by(FoundationStageTemplate.sort_order).all()
    # Seed from hardcoded list if DB is empty
    if not templates:
        for i, (key, label) in enumerate(FOUNDATION_STAGES):
            db.session.add(FoundationStageTemplate(stage_key=key, stage_label=label, sort_order=i))
        db.session.commit()
        templates = FoundationStageTemplate.query.order_by(FoundationStageTemplate.sort_order).all()
    return render_template('foundation_stage_settings.html', templates=templates)


@app.route('/api/foundation/stages', methods=['GET'])
@login_required
def api_foundation_stages_list():
    templates = FoundationStageTemplate.query.order_by(FoundationStageTemplate.sort_order).all()
    return jsonify([t.to_dict() for t in templates])


@app.route('/api/foundation/stages/add', methods=['POST'])
@login_required
def api_foundation_stages_add():
    if current_user.role != 'admin':
        return jsonify({'error': 'Forbidden'}), 403
    data  = request.get_json() or {}
    label = data.get('stage_label', '').strip()
    if not label:
        return jsonify({'error': 'Label required'}), 400
    # Auto-generate key from label
    import re
    key = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')
    if FoundationStageTemplate.query.filter_by(stage_key=key).first():
        key = key + '_' + str(FoundationStageTemplate.query.count() + 1)
    max_order = db.session.query(db.func.max(FoundationStageTemplate.sort_order)).scalar() or 0
    t = FoundationStageTemplate(stage_key=key, stage_label=label, sort_order=max_order + 1)
    db.session.add(t)
    # Create FoundationStage rows for every WTG
    wtgs = WTG.query.all()
    existing_keys = {s.stage_key for s in FoundationStage.query.all()}
    for wtg in wtgs:
        if key not in {s.stage_key for s in FoundationStage.query.filter_by(wtg_id=wtg.id).all()}:
            db.session.add(FoundationStage(wtg_id=wtg.id, stage_key=key, stage_label=label))
    db.session.commit()
    return jsonify(t.to_dict())


@app.route('/api/foundation/stages/<int:tid>/update', methods=['POST'])
@login_required
def api_foundation_stages_update(tid):
    if current_user.role != 'admin':
        return jsonify({'error': 'Forbidden'}), 403
    t    = FoundationStageTemplate.query.get_or_404(tid)
    data = request.get_json() or {}
    if 'stage_label' in data:
        new_label = data['stage_label'].strip()
        t.stage_label = new_label
        # Update label on all existing FoundationStage rows with this key
        FoundationStage.query.filter_by(stage_key=t.stage_key).update({'stage_label': new_label})
    if 'sort_order' in data:
        t.sort_order = int(data['sort_order'])
    db.session.commit()
    return jsonify(t.to_dict())


@app.route('/api/foundation/stages/<int:tid>/delete', methods=['POST'])
@login_required
def api_foundation_stages_delete(tid):
    if current_user.role != 'admin':
        return jsonify({'error': 'Forbidden'}), 403
    t = FoundationStageTemplate.query.get_or_404(tid)
    db.session.delete(t)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/foundation/stages/reorder', methods=['POST'])
@login_required
def api_foundation_stages_reorder():
    if current_user.role != 'admin':
        return jsonify({'error': 'Forbidden'}), 403
    order = request.get_json() or []   # list of {id, sort_order}
    for item in order:
        FoundationStageTemplate.query.filter_by(id=item['id']).update({'sort_order': item['sort_order']})
    db.session.commit()
    return jsonify({'ok': True})


# ══════════════════════════════════════════════════════════════════════════════
# PROGRESS TRACKER
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/progress')
@login_required
def progress_tracker():
    widgets = ProgressWidget.query.order_by(ProgressWidget.sort_order).all()
    wtgs    = WTG.query.order_by(WTG.name).all()
    return render_template('progress_tracker.html', widgets=widgets, wtgs=wtgs)


@app.route('/api/progress/data/<source>')
@login_required
def api_progress_data(source):
    """Return JSON data for a given chart data source."""

    if source == 'wtg_completion':
        wtgs = WTG.query.order_by(WTG.name).all()
        return jsonify({
            'labels': [w.name for w in wtgs],
            'datasets': [{
                'label': 'QA Complete %',
                'data': [w.completion_pct for w in wtgs],
                'backgroundColor': [
                    '#22c55e' if w.completion_pct==100
                    else '#f59e0b' if w.completion_pct>0
                    else '#fca5a5' for w in wtgs
                ]
            }]
        })

    elif source == 'foundation_stages':
        wtgs = WTG.query.order_by(WTG.name).all()
        stage_labels = [label for _, label in FOUNDATION_STAGES]
        complete_counts = []
        for _, key in [(k,k) for k,_ in FOUNDATION_STAGES]:
            n = FoundationStage.query.filter_by(stage_key=key, status='complete').count()
            complete_counts.append(n)
        return jsonify({
            'labels': [lbl.split('–')[1].strip()[:25] for _, lbl in FOUNDATION_STAGES],
            'datasets': [{
                'label': 'WTGs Complete',
                'data': complete_counts,
                'backgroundColor': '#7c3aed'
            }, {
                'label': 'Total WTGs',
                'data': [17] * len(FOUNDATION_STAGES),
                'backgroundColor': '#ede9fe',
                'type': 'line',
                'borderColor': '#7c3aed',
                'fill': False
            }]
        })

    elif source == 'status_breakdown':
        wtgs = WTG.query.order_by(WTG.name).all()
        complete  = sum(1 for w in wtgs if w.completion_pct == 100)
        in_prog   = sum(1 for w in wtgs if 0 < w.completion_pct < 100)
        not_started = sum(1 for w in wtgs if w.completion_pct == 0)
        return jsonify({
            'labels': ['Complete', 'In Progress', 'Not Started'],
            'datasets': [{'data': [complete, in_prog, not_started],
                          'backgroundColor': ['#22c55e','#f59e0b','#fca5a5']}]
        })

    elif source == 'area_completion':
        areas = ['hardstand','crane_pad','boom_pad','blade_fingers']
        labels = ['Hardstand','Crane Pad','Boom Pad','Blade Fingers']
        data = []
        for at in areas:
            all_a = Area.query.filter_by(area_type=at).all()
            if all_a:
                avg = sum(a.completion_pct for a in all_a) / len(all_a)
            else:
                avg = 0
            data.append(round(avg, 1))
        return jsonify({
            'labels': labels,
            'datasets': [{'data': data,
                          'backgroundColor': ['#fca5a5','#86efac','#fde047','#93c5fd']}]
        })

    elif source == 'daily_tests':
        from sqlalchemy import func
        rows = (db.session.query(
            func.date(TestRecord.test_date).label('d'),
            func.count(TestRecord.id).label('n'))
            .group_by(func.date(TestRecord.test_date))
            .order_by(func.date(TestRecord.test_date))
            .limit(30).all())
        return jsonify({
            'labels': [str(r.d) for r in rows],
            'datasets': [{'label': 'Tests Recorded', 'data': [r.n for r in rows],
                          'borderColor': '#7c3aed', 'backgroundColor': '#ede9fe',
                          'fill': True, 'tension': 0.4}]
        })

    elif source == 'test_records_table':
        records = (db.session.query(TestRecord, QATest, Area, WTG)
                   .join(QATest, QATest.id == TestRecord.qa_test_id)
                   .join(Area,   Area.id   == QATest.area_id)
                   .join(WTG,    WTG.id    == Area.wtg_id)
                   .order_by(TestRecord.test_date.desc())
                   .limit(200).all())
        return jsonify([{
            'wtg':      w.name,
            'area':     a.label,
            'test':     t.display_name,
            'date':     r.test_date.isoformat() if r.test_date else '',
            'result':   r.result,
            'value':    r.result_value,
            'unit':     r.result_unit,
            'lot':      r.lot_number,
            'lab_ref':  r.lab_ref,
        } for r, t, a, w in records])

    elif source == 'foundation_table':
        rows = (db.session.query(FoundationStage, WTG)
                .join(WTG, WTG.id == FoundationStage.wtg_id)
                .order_by(WTG.name, FoundationStage.id).all())
        return jsonify([{
            'wtg':    w.name,
            'stage':  s.stage_label,
            'status': s.status,
            'date':   s.date_completed.isoformat() if s.date_completed else '',
            'ref':    s.reference_no or '',
            'lot':    s.lot_number or '',
        } for s, w in rows])

    return jsonify({'error': 'Unknown source'}), 404


@app.route('/api/progress/widgets', methods=['GET'])
@login_required
def api_get_widgets():
    return jsonify([{'id': w.id, 'title': w.title, 'widget_type': w.widget_type,
                     'data_source': w.data_source, 'sort_order': w.sort_order}
                    for w in ProgressWidget.query.order_by(ProgressWidget.sort_order).all()])


@app.route('/api/progress/widgets', methods=['POST'])
@login_required
def api_add_widget():
    if not current_user.can_enter_data():
        return jsonify({'error': 'Forbidden'}), 403
    data = request.get_json() or {}
    max_order = db.session.query(db.func.max(ProgressWidget.sort_order)).scalar() or 0
    w = ProgressWidget(
        title       = data.get('title', 'New Chart'),
        widget_type = data.get('widget_type', 'bar'),
        data_source = data.get('data_source', 'wtg_completion'),
        sort_order  = max_order + 1,
        created_by  = current_user.id
    )
    db.session.add(w); db.session.commit()
    return jsonify({'ok': True, 'id': w.id})


@app.route('/api/progress/widgets/<int:wid>', methods=['DELETE'])
@login_required
def api_delete_widget(wid):
    if not current_user.can_enter_data():
        return jsonify({'error': 'Forbidden'}), 403
    w = ProgressWidget.query.get_or_404(wid)
    db.session.delete(w); db.session.commit()
    return jsonify({'ok': True})


# ══════════════════════════════════════════════════════════════════════════════
# DYNAMIC CUSTOM FIELDS  (engineer-configurable per test type)
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/settings/fields')
@login_required
def custom_fields():
    if not current_user.can_enter_data():
        abort(403)
    all_fields = CustomTrackingField.query.order_by(
        CustomTrackingField.scope, CustomTrackingField.sort_order).all()
    # Group by scope
    grouped = {}
    for f in all_fields:
        grouped.setdefault(f.scope, []).append(f)
    # All possible scopes
    scopes = {}
    for area_type, tests in {
        'hardstand':     ['dcp','subgrade_compaction','subbase_compaction','basecourse_compaction','plate_load_test'],
        'crane_pad':     ['dcp','subgrade_compaction','subbase_compaction','basecourse_compaction'],
        'boom_pad':      ['dcp','subgrade_compaction','subbase_compaction','basecourse_compaction'],
        'blade_fingers': ['dcp','subgrade_compaction','basecourse_compaction'],
    }.items():
        for t in tests:
            scopes[t] = t.replace('_',' ').title()
    return render_template('custom_fields.html', grouped=grouped,
                           scopes=scopes, today=date.today().isoformat())


@app.route('/api/fields', methods=['POST'])
@login_required
def api_add_field():
    if not current_user.can_enter_data():
        return jsonify({'error': 'Forbidden'}), 403
    data = request.get_json() or {}
    max_ord = (db.session.query(db.func.max(CustomTrackingField.sort_order))
               .filter_by(scope=data.get('scope','')).scalar() or 0)
    f = CustomTrackingField(
        scope        = data.get('scope',''),
        field_key    = data.get('field_key','').lower().replace(' ','_'),
        label        = data.get('label','New Field'),
        field_type   = data.get('field_type','text'),
        unit         = data.get('unit',''),
        default_value= data.get('default_value',''),
        spec_min     = data.get('spec_min'),
        spec_max     = data.get('spec_max'),
        options      = data.get('options',''),
        required     = bool(data.get('required', False)),
        sort_order   = max_ord + 1,
        created_by   = current_user.id
    )
    db.session.add(f); db.session.commit()
    return jsonify({'ok': True, 'id': f.id})


@app.route('/api/fields/<int:fid>', methods=['PUT'])
@login_required
def api_update_field(fid):
    if not current_user.can_enter_data():
        return jsonify({'error': 'Forbidden'}), 403
    f    = CustomTrackingField.query.get_or_404(fid)
    data = request.get_json() or {}
    for k in ('label','unit','default_value','spec_min','spec_max','options','required','sort_order'):
        if k in data:
            setattr(f, k, data[k])
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/fields/<int:fid>', methods=['DELETE'])
@login_required
def api_delete_field(fid):
    if not current_user.can_enter_data():
        return jsonify({'error': 'Forbidden'}), 403
    f = CustomTrackingField.query.get_or_404(fid)
    db.session.delete(f); db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/fields/<scope>')
@login_required
def api_get_fields(scope):
    fields = CustomTrackingField.query.filter_by(scope=scope).order_by(
        CustomTrackingField.sort_order).all()
    return jsonify([{
        'id': f.id, 'key': f.field_key, 'label': f.label,
        'type': f.field_type, 'unit': f.unit,
        'default': f.default_value, 'spec_min': f.spec_min, 'spec_max': f.spec_max,
        'options': f.options.split(',') if f.options else [],
        'required': f.required
    } for f in fields])


# ─── Health check (for Railway / load balancers) ─────────────────────────────
@app.route('/health')
def health():
    return {'status': 'ok', 'app': 'windfarm-manager'}, 200

# ─── Startup: create tables, dirs, seed ──────────────────────────────────────
def startup():
    create_dirs()
    with app.app_context():
        db.create_all()
    seed(app)
    with app.app_context():
        db.session.remove()   # clean up any sessions left open by seed

# ─── Run ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    startup()
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    port  = int(os.environ.get('PORT', 5000))
    app.run(debug=debug, host='0.0.0.0', port=port)
