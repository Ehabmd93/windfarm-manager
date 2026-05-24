import os, json, base64, uuid, unicodedata, re, secrets, hashlib
import urllib.request, urllib.parse
from urllib.parse import quote as _urlquote
from datetime import datetime, date, timezone, timedelta
from flask import (Flask, render_template, request, redirect,
                   url_for, flash, jsonify, abort, send_from_directory, make_response)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
import json as _json
from models import (db, User, WTG, Area, QATest, TestRecord,
                    Project, ProjectMember, ProjectFeature, ProjectMapFile,
                    PROJECT_TYPES, PROJECT_STATUSES, ALL_FEATURES, PROJECT_ACCESS_LEVELS,
                    WTGGroup, WorkPackage, ELEMENT_TYPES,
                    Activity, ACTIVITY_TYPES,
                    ProofRollRecord, ProofRollSignatory,
                    ProofRollEquipment, ProofRollPhoto, ProofRollRectPhoto,
                    TempPhotoUpload,
                    TestPhoto,
                    ProjectITPTemplate,
                    ITPRecord, ITPItemStatus, ITPItemDocument,
                    FoundationStage, FoundationStageTemplate, FoundationDocument, FOUNDATION_STAGES,
                    CustomTrackingField, ProgressWidget,
                    Document, DocumentLink, DocumentFolder,
                    Notification, ITPClientInvite,
                    DOCUMENT_CATEGORIES, DOCUMENT_LINK_TYPES, DOCUMENT_LINK_DICT,
                    ProjectCompany, ProjectTeamMember, AuditEvent,
                    COMPANY_TYPES, PROJECT_ROLES, ITP_REVIEW_ACTIONS,
                    UserInvite, PasswordResetToken, INVITE_STATUSES)
from itp_definitions import ITP_DEFINITIONS, CLIENTS
from seed import seed
import kml_parser
from project_config import (PROJECT_TYPE_PROFILES, get_profile,
                             is_wind_farm, GENERIC_ELEMENT_TYPE_LABELS)
from kml_parser import get_geojson
from email_utils import email_client_invitation, email_client_signed, email_project_invitation
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


# ── Invite token helpers ──────────────────────────────────────────────────────

def _make_raw_token():
    """Generate a cryptographically random URL-safe token (43 chars)."""
    return secrets.token_urlsafe(32)


def _hash_token(raw_token):
    """SHA-256 hex digest of raw_token — only this digest is stored in the DB."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _project_role_label(role_key):
    """Human-readable label for a PROJECT_ROLES key."""
    return next((lbl for k, lbl, *_ in PROJECT_ROLES if k == role_key),
                role_key.replace('_', ' ').title())


# ── Invite role → User/ProjectMember role mappings (Phase 4B) ─────────────────

_INVITE_TO_USER_ROLE = {
    'project_admin':           'manager',
    'project_manager':         'manager',
    'qa_manager':              'manager',
    'site_engineer':           'engineer',
    'supervisor':              'supervisor',
    'subcontractor_submitter': 'engineer',
    'testing_lab':             'engineer',
    'client_reviewer':         'client',
    'client_approver':         'client',
    'consultant':              'supervisor',
    'auditor':                 'supervisor',
}

_INVITE_TO_PROJ_ROLE = {
    'project_admin':           'admin',
    'project_manager':         'lead',
    'qa_manager':              'lead',
    'site_engineer':           'engineer',
    'supervisor':              'engineer',
    'subcontractor_submitter': 'engineer',
    'testing_lab':             'engineer',
    'client_reviewer':         'client',
    'client_approver':         'client',
    'consultant':              'viewer',
    'auditor':                 'viewer',
}


def _invite_role_to_user_role(role):
    """Map a project-directory role key to a global User.role value."""
    return _INVITE_TO_USER_ROLE.get(role or '', 'engineer')


def _invite_role_to_proj_role(role):
    """Map a project-directory role key to a ProjectMember.proj_role value."""
    return _INVITE_TO_PROJ_ROLE.get(role or '', 'viewer')


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
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2 GB — DB fallback limit (R2 path bypasses this entirely)

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
        'ELEMENT_TYPES':        ELEMENT_TYPES,
        'COMPANY_TYPES':        COMPANY_TYPES,
        'PROJECT_ROLES':        PROJECT_ROLES,
        'ITP_REVIEW_ACTIONS':   ITP_REVIEW_ACTIONS,
        'PROJECT_TYPE_PROFILES': PROJECT_TYPE_PROFILES,
        # Project type profile for the active project (controls UI labels/types)
        'proj_profile': (get_profile(getattr(g, 'project', None).project_type)
                         if getattr(g, 'project', None) else
                         get_profile('Other')),
        'is_wind_farm': (is_wind_farm(getattr(g, 'project', None).project_type)
                         if getattr(g, 'project', None) else False),
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
            # Respect ?next= for invite accept-flow redirects.
            # Only follow local paths (must start with / but not //) to prevent
            # open-redirect attacks.
            next_url = request.args.get('next', '').strip()
            if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                return redirect(next_url)
            return redirect(url_for('dashboard'))
        flash('Invalid email or password', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ─── Public invite landing (Phase 4B — new-user acceptance) ─────────────────

def _resolve_invite_state(token):
    """
    Hash raw token, look up UserInvite, determine display state.

    Returns (invite_or_None, proj_or_None, state_str).
    State values: 'invalid' | 'revoked' | 'accepted' | 'expired' | 'valid'
    Also auto-marks pending-but-past-expiry invites as 'expired'.
    """
    token_hash = _hash_token(token)
    invite = UserInvite.query.filter_by(token_hash=token_hash).first()

    if invite is None:
        return None, None, 'invalid'

    proj = invite.project  # may be None if project deleted

    if invite.status == 'revoked':
        return invite, proj, 'revoked'

    if invite.status == 'accepted':
        return invite, proj, 'accepted'

    if invite.status == 'expired':
        return invite, proj, 'expired'

    # status == 'pending' — check wall-clock expiry
    # SQLite returns naive datetimes; PostgreSQL returns aware ones.
    # Normalise to UTC-aware before comparing.
    now = datetime.now(timezone.utc)
    expires_at = invite.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at < now:
        try:
            invite.status = 'expired'
            db.session.commit()
        except Exception:
            db.session.rollback()
        return invite, proj, 'expired'

    return invite, proj, 'valid'


@app.route('/invite/<token>', methods=['GET', 'POST'])
def invite_accept(token):
    """Public invite landing page — Phase 4B (new user) + 4C (existing user).

    GET:
      Non-valid state          → informational error page (invalid/revoked/
                                 accepted/expired — unchanged from 4A)
      Valid + no account       → password-setup form (4B)
      Valid + existing account:
        not authenticated      → sign-in prompt with ?next= (4C)
        wrong logged-in user   → blocked wrong-user page (4C)
        correct user, disabled → blocked disabled page (4C)
        correct user, active   → Accept invite button (4C)

    POST:
      New user   → validate password → create User → link → login → redirect
      Existing user:
        not authenticated      → redirect /login?next=/invite/<token>
        wrong email            → blocked wrong-user page
        account disabled       → blocked disabled page
        correct + active       → link ProjectTeamMember, create ProjectMember,
                                  mark accepted, set session, redirect
    """
    from flask import session as flask_session

    # ── Shared render helper (captures token from outer scope) ───────────────
    def _render(state, invite, proj, role_label, *,
                user_exists=False, form_error=None, existing_sub_state=None):
        return render_template('invite_accept.html',
                               state=state,
                               invite=invite,
                               proj=proj,
                               role_label=role_label,
                               user_exists=user_exists,
                               form_error=form_error,
                               existing_sub_state=existing_sub_state,
                               token=token)

    # ── Resolve invite state (same for GET and POST) ─────────────────────────
    invite, proj, state = _resolve_invite_state(token)

    if state != 'valid':
        role_label = _project_role_label(invite.role) if invite else ''
        return _render(state, invite, proj, role_label)

    role_label    = _project_role_label(invite.role) if invite.role else ''
    existing_user = User.query.filter_by(
        email=invite.email.strip().lower()
    ).first()

    # ════════════════════════════════════════════════════════════════════════
    # BRANCH A — existing user (Phase 4C)
    # ════════════════════════════════════════════════════════════════════════
    if existing_user:

        def _get_existing_sub_state():
            """Determine display sub-state for an existing-user invite."""
            if not existing_user.is_active:
                return 'disabled'
            if not current_user.is_authenticated:
                return 'not_logged_in'
            if current_user.email.strip().lower() != invite.email.strip().lower():
                return 'wrong_user'
            return 'ready'

        # ── GET ──────────────────────────────────────────────────────────────
        if request.method == 'GET':
            return _render('valid', invite, proj, role_label,
                           user_exists=True,
                           existing_sub_state=_get_existing_sub_state())

        # ── POST ─────────────────────────────────────────────────────────────

        # Not authenticated → send to login with next= so they return here
        if not current_user.is_authenticated:
            return redirect(url_for('login', next=f'/invite/{token}'))

        # Wrong email → blocked (no DB changes)
        if current_user.email.strip().lower() != invite.email.strip().lower():
            return _render('valid', invite, proj, role_label,
                           user_exists=True,
                           existing_sub_state='wrong_user')

        # Disabled account → blocked (no DB changes)
        if not existing_user.is_active:
            return _render('valid', invite, proj, role_label,
                           user_exists=True,
                           existing_sub_state='disabled')

        # ── Accept ────────────────────────────────────────────────────────────
        now = datetime.now(timezone.utc)

        # Ensure email is verified
        if not existing_user.email_verified:
            existing_user.email_verified = True
        if existing_user.email_verified_at is None:
            existing_user.email_verified_at = now

        # Link ProjectTeamMember → existing User
        if invite.project_team_member_id is not None:
            team_member = ProjectTeamMember.query.filter_by(
                id=invite.project_team_member_id,
                project_id=invite.project_id,
            ).first()
            if team_member and team_member.user_id is None:
                team_member.user_id = existing_user.id
                log_audit('project_member_linked',
                          project_id=invite.project_id,
                          actor=current_user,
                          entity_type='team_member',
                          entity_id=team_member.id,
                          entity_label=existing_user.name,
                          detail={'user_id': existing_user.id,
                                  'role': invite.role,
                                  'invite_id': invite.id})

        # Create ProjectMember if not already present
        if invite.project_id is not None:
            already = ProjectMember.query.filter_by(
                project_id=invite.project_id,
                user_id=existing_user.id,
            ).first()
            if not already:
                pm = ProjectMember(
                    project_id=invite.project_id,
                    user_id=existing_user.id,
                    proj_role=_invite_role_to_proj_role(invite.role),
                    added_at=now,
                )
                db.session.add(pm)
                log_audit('project_member_created',
                          project_id=invite.project_id,
                          actor=current_user,
                          entity_type='project_member',
                          entity_id=None,
                          entity_label=existing_user.name,
                          detail={'user_id': existing_user.id,
                                  'proj_role': _invite_role_to_proj_role(invite.role),
                                  'invite_id': invite.id})

        # Mark invite accepted
        invite.status      = 'accepted'
        invite.accepted_at = now

        # Audit — no user_created_from_invite for existing users
        log_audit('user_invite_accepted',
                  project_id=invite.project_id,
                  actor=current_user,
                  entity_type='user_invite',
                  entity_id=invite.id,
                  entity_label=invite.email,
                  detail={'user_id': existing_user.id,
                          'user_email': existing_user.email,
                          'invite_role': invite.role,
                          'proj_role': _invite_role_to_proj_role(invite.role)})

        db.session.commit()

        if invite.project_id is not None:
            flask_session['active_project_id'] = invite.project_id

        flash('You have been added to the project.', 'success')
        return redirect(url_for('projects_list'))

    # ════════════════════════════════════════════════════════════════════════
    # BRANCH B — new user (Phase 4B, unchanged)
    # ════════════════════════════════════════════════════════════════════════

    # ── GET ──────────────────────────────────────────────────────────────────
    if request.method == 'GET':
        return _render('valid', invite, proj, role_label,
                       user_exists=False)

    # ── POST: new-user account creation ──────────────────────────────────────
    password  = (request.form.get('password')  or '').strip()
    password2 = (request.form.get('password2') or '').strip()

    # Password validation
    form_error = None
    if not password:
        form_error = 'Password is required.'
    elif len(password) < 8:
        form_error = 'Password must be at least 8 characters.'
    elif password != password2:
        form_error = 'Passwords do not match.'

    if form_error:
        return _render('valid', invite, proj, role_label,
                       user_exists=False, form_error=form_error)

    # Create User
    now = datetime.now(timezone.utc)
    new_user = User(
        name=invite.name,
        email=invite.email.strip().lower(),
        password=generate_password_hash(password),
        role=_invite_role_to_user_role(invite.role),
        company=invite.company or '',
        is_active=True,
        email_verified=True,
        email_verified_at=now,
        password_changed_at=now,
        failed_login_count=0,
        locked_until=None,
    )
    db.session.add(new_user)
    db.session.flush()  # get new_user.id before FK references

    # Link ProjectTeamMember → new User
    if invite.project_team_member_id is not None:
        team_member = ProjectTeamMember.query.filter_by(
            id=invite.project_team_member_id,
            project_id=invite.project_id,
        ).first()
        if team_member and team_member.user_id is None:
            team_member.user_id = new_user.id
            log_audit('project_member_linked',
                      project_id=invite.project_id,
                      actor=None,
                      entity_type='team_member',
                      entity_id=team_member.id,
                      entity_label=new_user.name,
                      detail={'user_id': new_user.id,
                              'role': invite.role})

    # Create ProjectMember if not already present
    if invite.project_id is not None:
        already = ProjectMember.query.filter_by(
            project_id=invite.project_id,
            user_id=new_user.id,
        ).first()
        if not already:
            pm = ProjectMember(
                project_id=invite.project_id,
                user_id=new_user.id,
                proj_role=_invite_role_to_proj_role(invite.role),
                added_at=now,
            )
            db.session.add(pm)

    # Mark invite accepted
    invite.status      = 'accepted'
    invite.accepted_at = now

    # Audit
    log_audit('user_created_from_invite',
              project_id=invite.project_id,
              actor=None,
              entity_type='user',
              entity_id=new_user.id,
              entity_label=new_user.email,
              detail={'name': new_user.name,
                      'role': new_user.role,
                      'invite_id': invite.id})

    log_audit('user_invite_accepted',
              project_id=invite.project_id,
              actor=None,
              entity_type='user_invite',
              entity_id=invite.id,
              entity_label=invite.email,
              detail={'new_user_id': new_user.id})

    db.session.commit()

    # Login & redirect
    login_user(new_user)
    if invite.project_id is not None:
        flask_session['active_project_id'] = invite.project_id

    flash('Welcome! Your account has been created and you have been added to the project.', 'success')
    return redirect(url_for('projects_list'))

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

    if request.method == 'POST':
        import secrets, string
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

        # ── Members (JSON list from wizard) ──
        members_json = request.form.get('members_json', '[]')
        try:
            members_data = json.loads(members_json)
        except Exception:
            members_data = []

        new_credentials = []  # track newly created accounts
        added_ids = set()

        # Always add creator as owner
        db.session.add(ProjectMember(project_id=proj.id, user_id=current_user.id, proj_role='owner'))
        added_ids.add(current_user.id)

        for m in members_data:
            email    = (m.get('email') or '').strip().lower()
            name     = (m.get('name')  or '').strip()
            position = (m.get('position') or '').strip()
            access   = (m.get('access') or 'viewer').strip()
            if not email:
                continue
            user = User.query.filter_by(email=email).first()
            if not user:
                # Create new account with temp password
                alphabet = string.ascii_letters + string.digits
                temp_pw  = ''.join(secrets.choice(alphabet) for _ in range(10))
                # Map project access → system role
                sys_role = 'engineer' if access in ('admin','lead','engineer') else \
                           'client'   if access == 'client' else 'supervisor'
                user = User(
                    name=name or email.split('@')[0].title(),
                    email=email,
                    password=generate_password_hash(temp_pw),
                    role=sys_role,
                    position=position,
                    company=proj.client_name or '',
                )
                db.session.add(user)
                db.session.flush()
                new_credentials.append({'name': user.name, 'email': email, 'password': temp_pw})
            else:
                # Update position if provided and currently blank
                if position and not user.position:
                    user.position = position

            if user.id not in added_ids:
                db.session.add(ProjectMember(project_id=proj.id, user_id=user.id, proj_role=access))
                added_ids.add(user.id)

        # ── Features ──
        for key, *_ in ALL_FEATURES:
            enabled = request.form.get(f'feat_{key}') == '1'
            db.session.add(ProjectFeature(project_id=proj.id, feature_key=key, enabled=enabled))

        db.session.commit()
        from flask import session as fsession
        fsession['active_project_id'] = proj.id

        if new_credentials:
            cred_lines = ' | '.join([f"{c['name']} → {c['email']} / {c['password']}" for c in new_credentials])
            flash(f'New accounts created — share these credentials: {cred_lines}', 'success')
        flash(f'Project "{proj.name}" created successfully!', 'success')
        return redirect(url_for('project_setup', pid=proj.id))

    return render_template('project_new.html', access_levels=PROJECT_ACCESS_LEVELS)


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
    from flask import g
    proj = getattr(g, 'project', None)

    wtgs = wtg_summary()
    total       = len(wtgs)
    complete    = sum(1 for w in wtgs if w.completion_pct == 100)
    in_prog     = sum(1 for w in wtgs if 0 < w.completion_pct < 100)
    not_started = sum(1 for w in wtgs if w.completion_pct == 0)

    # Project Setup brain stats
    pid = proj.id if proj else None
    try:
        group_count    = WTGGroup.query.filter_by(project_id=pid).count()    if pid else 0
        wp_count       = WorkPackage.query.filter_by(project_id=pid).count() if pid else 0
        element_count  = WTG.query.filter_by(project_id=pid).count()         if pid else 0
        area_count     = Area.query.join(WTG, Area.wtg_id == WTG.id)\
                             .filter(WTG.project_id == pid).count()           if pid else 0
        activity_count = Activity.query.join(Area, Activity.area_id == Area.id)\
                             .join(WTG, Area.wtg_id == WTG.id)\
                             .filter(WTG.project_id == pid).count()           if pid else 0
        member_count   = len(proj.members) if proj else 0
    except Exception:
        group_count = wp_count = element_count = area_count = activity_count = member_count = 0

    return render_template('dashboard.html',
                           wtgs=wtgs,
                           total=total,
                           complete=complete,
                           in_prog=in_prog,
                           not_started=not_started,
                           group_count=group_count,
                           wp_count=wp_count,
                           element_count=element_count,
                           area_count=area_count,
                           activity_count=activity_count,
                           member_count=member_count)

# ─── Project Setup (elements + groups) ──────────────────────────────────────
@app.route('/projects/<int:pid>/setup')
@login_required
def project_setup(pid):
    if current_user.role not in ('engineer', 'manager', 'admin'):
        abort(403)
    proj          = Project.query.get_or_404(pid)
    from flask import session as fsession
    fsession['active_project_id'] = pid   # switch active project when visiting setup
    elements      = WTG.query.filter_by(project_id=pid).order_by(WTG.name).all()
    groups        = WTGGroup.query.filter_by(project_id=pid).order_by(WTGGroup.sort_order, WTGGroup.name).all()
    work_packages = WorkPackage.query.filter_by(project_id=pid).order_by(WorkPackage.sort_order, WorkPackage.name).all()
    return render_template('project_setup.html', proj=proj,
                           elements=elements, groups=groups,
                           work_packages=work_packages,
                           element_types=ELEMENT_TYPES,
                           activity_types=ACTIVITY_TYPES)


# ══════════════════════════════════════════════════════════════════════════════
# PEOPLE & COMPANIES — project team management
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/projects/<int:pid>/people')
@login_required
def project_people(pid):
    """Companies + team members management page."""
    if current_user.role not in ('engineer', 'manager', 'admin'):
        abort(403)
    proj     = Project.query.get_or_404(pid)
    from flask import session as fsession
    fsession['active_project_id'] = pid
    companies = (ProjectCompany.query
                 .filter_by(project_id=pid)
                 .order_by(ProjectCompany.company_type, ProjectCompany.name)
                 .all())
    # Team members per company (also unassigned)
    members = (ProjectTeamMember.query
               .filter_by(project_id=pid, is_active=True)
               .order_by(ProjectTeamMember.name)
               .all())
    # Invite index: team_member_id → UserInvite (one active invite per member)
    invites_by_member = {}
    for inv in UserInvite.query.filter_by(project_id=pid).all():
        if inv.project_team_member_id is not None:
            # Keep the most-recently-created invite if duplicates somehow exist
            existing = invites_by_member.get(inv.project_team_member_id)
            if existing is None or inv.id > existing.id:
                invites_by_member[inv.project_team_member_id] = inv
    can_manage = current_user.role in ('manager', 'admin')
    return render_template('project_people.html',
                           proj=proj,
                           companies=companies,
                           members=members,
                           company_types=COMPANY_TYPES,
                           project_roles=PROJECT_ROLES,
                           invites_by_member=invites_by_member,
                           can_manage=can_manage)


@app.route('/projects/<int:pid>/companies', methods=['POST'])
@login_required
def api_add_company(pid):
    """AJAX — add a company to the project. Requires manager or admin."""
    if current_user.role not in ('manager', 'admin'):
        return jsonify({'error': 'Forbidden — only managers and admins can add companies.'}), 403
    proj = Project.query.get_or_404(pid)
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Company name is required'}), 400
    c = ProjectCompany(
        project_id    = pid,
        company_type  = data.get('company_type', 'client'),
        name          = name,
        short_name    = (data.get('short_name') or '').strip(),
        contact_name  = (data.get('contact_name') or '').strip(),
        contact_email = (data.get('contact_email') or '').strip(),
        contact_phone = (data.get('contact_phone') or '').strip(),
        notes         = (data.get('notes') or '').strip(),
        added_by      = current_user.id,
    )
    db.session.add(c)
    log_audit('company_added', project_id=pid, actor=current_user,
              entity_type='company', entity_label=name,
              detail={'company_type': c.company_type})
    db.session.commit()
    return jsonify({'ok': True, 'id': c.id, 'name': c.name,
                    'company_type': c.company_type,
                    'type_label': c.type_label,
                    'type_color': c.type_color,
                    'type_icon':  c.type_icon})


@app.route('/projects/<int:pid>/companies/<int:cid>', methods=['DELETE'])
@login_required
def api_delete_company(pid, cid):
    """AJAX — remove a company from the project. Requires manager or admin."""
    if current_user.role not in ('manager', 'admin'):
        return jsonify({'error': 'Forbidden — only managers and admins can remove companies.'}), 403
    c = ProjectCompany.query.filter_by(id=cid, project_id=pid).first_or_404()
    log_audit('company_removed', project_id=pid, actor=current_user,
              entity_type='company', entity_id=cid, entity_label=c.name)
    db.session.delete(c)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/projects/<int:pid>/team', methods=['POST'])
@login_required
def api_add_team_member(pid):
    """AJAX — add a team member to the project, creating a UserInvite if email is provided."""
    if current_user.role not in ('manager', 'admin'):
        return jsonify({'error': 'Forbidden — only managers and admins can add team members.'}), 403
    proj = Project.query.get_or_404(pid)
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Name is required'}), 400

    # Normalise email to lowercase; empty string if not provided
    email_norm = (data.get('email') or '').strip().lower()

    # Resolve company name now (before flush/commit) to avoid lazy-load timing issues
    company_id_raw = data.get('company_id') or None
    company_id     = int(company_id_raw) if company_id_raw else None
    company_name   = ''
    if company_id:
        _co = ProjectCompany.query.filter_by(id=company_id, project_id=pid).first()
        company_name = _co.name if _co else ''

    # Check whether a registered User with this email already exists — link if so
    linked_user = User.query.filter_by(email=email_norm).first() if email_norm else None

    m = ProjectTeamMember(
        project_id   = pid,
        company_id   = company_id,
        name         = name,
        email        = email_norm,
        position     = (data.get('position') or '').strip(),
        phone        = (data.get('phone')    or '').strip(),
        project_role = data.get('project_role', 'site_engineer'),
        can_sign     = bool(data.get('can_sign', True)),
        added_by     = current_user.id,
        user_id      = linked_user.id if linked_user else None,
    )
    db.session.add(m)
    db.session.flush()   # assign m.id before referencing it in the invite

    raw_token  = None
    invite_url = None
    invite_obj = None

    if email_norm:
        # Create a UserInvite — only the SHA-256 hash is stored; raw token is e-mailed only
        raw_token  = _make_raw_token()
        now        = datetime.now(timezone.utc)
        invite_obj = UserInvite(
            project_id             = pid,
            project_team_member_id = m.id,
            email                  = email_norm,
            name                   = name,
            company                = company_name,
            role                   = m.project_role,
            can_sign               = m.can_sign,
            token_hash             = _hash_token(raw_token),
            invited_by_id          = current_user.id,
            invited_at             = now,
            expires_at             = now + timedelta(days=14),
            status                 = 'pending',
        )
        db.session.add(invite_obj)
        db.session.flush()   # assign invite_obj.id

        _base      = (os.environ.get('APP_URL') or request.host_url.rstrip('/')).rstrip('/')
        invite_url = f"{_base}/invite/{raw_token}"

        log_audit('user_invite_sent', project_id=pid, actor=current_user,
                  entity_type='user_invite', entity_id=invite_obj.id,
                  entity_label=email_norm,
                  detail={'name': name, 'role': m.project_role})

    log_audit('member_added', project_id=pid, actor=current_user,
              entity_type='member', entity_id=m.id, entity_label=name,
              detail={'role': m.project_role, 'email': email_norm})

    db.session.commit()

    # Send invitation email after commit so DB is safe even if email fails
    if email_norm and invite_url and invite_obj:
        try:
            email_project_invitation(
                to_email     = email_norm,
                invitee_name = name,
                inviter_name = current_user.name,
                project_name = proj.name,
                role_label   = _project_role_label(m.project_role),
                invite_url   = invite_url,
                expires_at   = invite_obj.expires_at,
                company_name = company_name,
            )
        except Exception:
            pass   # email failure is non-fatal

    return jsonify({
        'ok':            True,
        'id':            m.id,
        'name':          m.name,
        'email':         m.email,
        'position':      m.position,
        'project_role':  m.project_role,
        'role_label':    m.role_label,
        'role_color':    m.role_color,
        'role_icon':     m.role_icon,
        'can_sign':      m.can_sign,
        'company_name':  company_name,
        'invite_status': 'pending' if invite_obj else None,
        'invite_url':    invite_url,
        'linked_user':   bool(linked_user),
    })


@app.route('/projects/<int:pid>/team/<int:mid>', methods=['DELETE'])
@login_required
def api_delete_team_member(pid, mid):
    """AJAX — remove (deactivate) a team member. Requires manager or admin."""
    if current_user.role not in ('manager', 'admin'):
        return jsonify({'error': 'Forbidden — only managers and admins can remove team members.'}), 403
    m = ProjectTeamMember.query.filter_by(id=mid, project_id=pid).first_or_404()
    m.is_active = False
    log_audit('member_removed', project_id=pid, actor=current_user,
              entity_type='member', entity_id=mid, entity_label=m.name)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/projects/<int:pid>/team/invites/<int:invite_id>/resend', methods=['POST'])
@login_required
def api_resend_invite(pid, invite_id):
    """AJAX — rotate token and re-send an invite email."""
    if current_user.role not in ('manager', 'admin'):
        return jsonify({'error': 'Only project managers or admins can resend invites.'}), 403
    invite = UserInvite.query.filter_by(id=invite_id, project_id=pid).first_or_404()
    if invite.status == 'accepted':
        return jsonify({'error': 'Cannot resend an already-accepted invite.'}), 400
    if invite.status == 'revoked':
        return jsonify({'error': 'Cannot resend a revoked invite. Create a new member entry instead.'}), 400

    # Rotate token — raw tokens are never stored so we must generate a fresh one
    raw_token = _make_raw_token()
    now = datetime.now(timezone.utc)
    invite.token_hash = _hash_token(raw_token)
    invite.invited_at = now
    invite.expires_at = now + timedelta(days=14)
    invite.status     = 'pending'

    _base      = (os.environ.get('APP_URL') or request.host_url.rstrip('/')).rstrip('/')
    invite_url = f"{_base}/invite/{raw_token}"

    log_audit('user_invite_resent', project_id=pid, actor=current_user,
              entity_type='user_invite', entity_id=invite.id,
              entity_label=invite.email,
              detail={'name': invite.name, 'role': invite.role})
    db.session.commit()

    # Send email after commit — failure is non-fatal
    try:
        proj = Project.query.get(pid)
        email_project_invitation(
            to_email     = invite.email,
            invitee_name = invite.name,
            inviter_name = current_user.name,
            project_name = proj.name if proj else '',
            role_label   = _project_role_label(invite.role),
            invite_url   = invite_url,
            expires_at   = invite.expires_at,
            company_name = invite.company or '',
        )
    except Exception:
        pass

    return jsonify({'ok': True, 'invite_url': invite_url})


@app.route('/projects/<int:pid>/team/invites/<int:invite_id>/revoke', methods=['POST'])
@login_required
def api_revoke_invite(pid, invite_id):
    """AJAX — permanently revoke an invite."""
    if current_user.role not in ('manager', 'admin'):
        return jsonify({'error': 'Only project managers or admins can revoke invites.'}), 403
    invite = UserInvite.query.filter_by(id=invite_id, project_id=pid).first_or_404()
    if invite.status == 'revoked':
        return jsonify({'error': 'Invite is already revoked.'}), 400
    if invite.status == 'accepted':
        return jsonify({'error': 'Cannot revoke an accepted invite.'}), 400

    now = datetime.now(timezone.utc)
    invite.status     = 'revoked'
    invite.revoked_at = now

    log_audit('user_invite_revoked', project_id=pid, actor=current_user,
              entity_type='user_invite', entity_id=invite.id,
              entity_label=invite.email,
              detail={'name': invite.name})
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/projects/<int:pid>/team/invites/<int:invite_id>/copy-link', methods=['POST'])
@login_required
def api_copy_invite_link(pid, invite_id):
    """AJAX — rotate token and return a fresh invite URL for copying.

    Raw tokens are never stored, so we must rotate to produce a new link.
    The old token (wherever it was) becomes invalid immediately.
    """
    if current_user.role not in ('manager', 'admin'):
        return jsonify({'error': 'Only project managers or admins can generate invite links.'}), 403
    invite = UserInvite.query.filter_by(id=invite_id, project_id=pid).first_or_404()
    if invite.status == 'accepted':
        return jsonify({'error': 'Cannot generate a new link for an accepted invite.'}), 400
    if invite.status == 'revoked':
        return jsonify({'error': 'Cannot generate a new link for a revoked invite.'}), 400

    raw_token = _make_raw_token()
    now = datetime.now(timezone.utc)
    invite.token_hash = _hash_token(raw_token)
    invite.invited_at = now
    invite.expires_at = now + timedelta(days=14)
    invite.status     = 'pending'

    _base      = (os.environ.get('APP_URL') or request.host_url.rstrip('/')).rstrip('/')
    invite_url = f"{_base}/invite/{raw_token}"

    log_audit('user_invite_link_copied', project_id=pid, actor=current_user,
              entity_type='user_invite', entity_id=invite.id,
              entity_label=invite.email)
    db.session.commit()
    return jsonify({'ok': True, 'invite_url': invite_url})


@app.route('/projects/<int:pid>/audit')
@login_required
def project_audit(pid):
    """Audit trail page — show all events for this project."""
    if current_user.role not in ('engineer', 'manager', 'admin'):
        abort(403)
    proj   = Project.query.get_or_404(pid)
    events = (AuditEvent.query
              .filter_by(project_id=pid)
              .order_by(AuditEvent.created_at.desc())
              .limit(500)
              .all())
    return render_template('project_audit.html', proj=proj, events=events)


@app.route('/projects/<int:pid>/hierarchy')
@login_required
def project_hierarchy(pid):
    proj          = Project.query.get_or_404(pid)
    from flask import session as fsession
    fsession['active_project_id'] = pid
    elements      = WTG.query.filter_by(project_id=pid).order_by(WTG.name).all()
    groups        = WTGGroup.query.filter_by(project_id=pid).order_by(WTGGroup.sort_order, WTGGroup.name).all()
    work_packages = WorkPackage.query.filter_by(project_id=pid).order_by(WorkPackage.sort_order, WorkPackage.name).all()
    total_areas       = sum(len(el.areas) for el in elements)
    total_activities  = sum(len(area.activities) for el in elements for area in el.areas)
    return render_template('hierarchy.html',
                           proj=proj,
                           groups=groups,
                           work_packages=work_packages,
                           elements=elements,
                           total_areas=total_areas,
                           total_activities=total_activities)


@app.route('/api/projects/<int:pid>/elements', methods=['POST'])
@login_required
def api_add_element(pid):
    if current_user.role not in ('engineer', 'manager', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    etype = data.get('element_type', 'wtg')
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    wp_id = data.get('work_package_id') or None
    # Duplicate check: same name within the same WP (or among unassigned if no WP)
    if WTG.query.filter_by(project_id=pid, name=name, work_package_id=wp_id).first():
        scope = f'work package' if wp_id else 'unassigned elements'
        return jsonify({'error': f'An element named "{name}" already exists in this {scope}'}), 400
    el = WTG(name=name, element_type=etype, project_id=pid, work_package_id=wp_id)
    db.session.add(el)
    db.session.commit()
    return jsonify({'id': el.id, 'name': el.name, 'element_type': el.element_type,
                    'element_type_label': el.element_type_label, 'group_id': el.group_id,
                    'work_package_id': el.work_package_id})


@app.route('/api/elements/<int:eid>', methods=['PATCH', 'DELETE'])
@login_required
def api_element(eid):
    if current_user.role not in ('engineer', 'manager', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403
    el = WTG.query.get_or_404(eid)
    if request.method == 'DELETE':
        db.session.delete(el)
        db.session.commit()
        return jsonify({'ok': True})
    data = request.get_json() or {}
    if 'name' in data:
        new_name = data['name'].strip()
        if not new_name:
            return jsonify({'error': 'Name is required'}), 400
        dup = WTG.query.filter_by(project_id=el.project_id, name=new_name).first()
        if dup and dup.id != el.id:
            return jsonify({'error': f'"{new_name}" already exists'}), 400
        el.name = new_name
    if 'element_type' in data:
        el.element_type = data['element_type']
    if 'group_id' in data:
        gid = data['group_id']
        el.group_id = int(gid) if gid else None
    if 'work_package_id' in data:
        wpid = data['work_package_id']
        el.work_package_id = int(wpid) if wpid else None
    db.session.commit()
    return jsonify({'id': el.id, 'name': el.name, 'element_type': el.element_type,
                    'element_type_label': el.element_type_label,
                    'group_id': el.group_id, 'work_package_id': el.work_package_id})


@app.route('/api/projects/<int:pid>/groups', methods=['POST'])
@login_required
def api_add_group(pid):
    if current_user.role not in ('engineer', 'manager', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403
    data  = request.get_json() or {}
    name  = data.get('name', '').strip()
    color = data.get('color', '#0f2942')
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    if WTGGroup.query.filter_by(project_id=pid, name=name).first():
        return jsonify({'error': f'Group "{name}" already exists'}), 400
    g = WTGGroup(project_id=pid, name=name, color=color,
                 sort_order=WTGGroup.query.filter_by(project_id=pid).count())
    db.session.add(g)
    db.session.commit()
    return jsonify({'id': g.id, 'name': g.name, 'color': g.color})


@app.route('/api/groups/<int:gid>', methods=['PATCH', 'DELETE'])
@login_required
def api_group(gid):
    if current_user.role not in ('engineer', 'manager', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403
    g = WTGGroup.query.get_or_404(gid)
    if request.method == 'DELETE':
        # Unlink elements from this group before deleting
        for el in g.elements:
            el.group_id = None
        db.session.delete(g)
        db.session.commit()
        return jsonify({'ok': True})
    data = request.get_json() or {}
    if 'name' in data:
        g.name = data['name'].strip()
    if 'color' in data:
        g.color = data['color']
    db.session.commit()
    return jsonify({'id': g.id, 'name': g.name, 'color': g.color})


@app.route('/api/projects/<int:pid>/work-packages', methods=['POST'])
@login_required
def api_add_work_package(pid):
    if current_user.role not in ('engineer', 'manager', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403
    data     = request.get_json() or {}
    name     = data.get('name', '').strip()
    color    = data.get('color', '#7c3aed')
    icon     = data.get('icon', 'layer-group')
    group_id = data.get('group_id') or None
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    # Uniqueness is per-group: same name allowed in different groups
    dup_q = WorkPackage.query.filter_by(project_id=pid, name=name)
    if group_id:
        dup_q = dup_q.filter_by(group_id=int(group_id))
    if dup_q.first():
        return jsonify({'error': f'"{name}" already exists in this group'}), 400
    wp = WorkPackage(project_id=pid, name=name, color=color, icon=icon,
                     group_id=int(group_id) if group_id else None,
                     sort_order=WorkPackage.query.filter_by(project_id=pid).count())
    db.session.add(wp)
    db.session.commit()
    return jsonify({'id': wp.id, 'name': wp.name, 'color': wp.color, 'icon': wp.icon,
                    'group_id': wp.group_id})


@app.route('/api/work-packages/<int:wpid>', methods=['PATCH', 'DELETE'])
@login_required
def api_work_package(wpid):
    if current_user.role not in ('engineer', 'manager', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403
    wp = WorkPackage.query.get_or_404(wpid)
    if request.method == 'DELETE':
        for el in wp.elements:
            el.work_package_id = None
        db.session.delete(wp)
        db.session.commit()
        return jsonify({'ok': True})
    data = request.get_json() or {}
    if 'name' in data:
        wp.name = data['name'].strip()
    if 'color' in data:
        wp.color = data['color']
    if 'icon' in data:
        wp.icon = data['icon']
    if 'group_id' in data:
        wp.group_id = int(data['group_id']) if data['group_id'] else None
    db.session.commit()
    return jsonify({'id': wp.id, 'name': wp.name, 'color': wp.color, 'icon': wp.icon,
                    'group_id': wp.group_id})


@app.route('/api/areas/<int:aid>/activities', methods=['GET', 'POST'])
@login_required
def api_area_activities(aid):
    area = Area.query.get_or_404(aid)
    if request.method == 'GET':
        return jsonify([{
            'id': a.id, 'name': a.name, 'activity_type': a.activity_type,
            'type_label': a.type_label, 'status': a.status,
            'status_label': a.status_label, 'status_color': a.status_color,
            'sort_order': a.sort_order
        } for a in sorted(area.activities, key=lambda x: (x.sort_order, x.name))])
    if current_user.role not in ('engineer', 'manager', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403
    data          = request.get_json() or {}
    name          = data.get('name', '').strip()
    activity_type = data.get('activity_type', 'general')
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    act = Activity(area_id=aid, name=name, activity_type=activity_type,
                   sort_order=Activity.query.filter_by(area_id=aid).count())
    db.session.add(act)
    db.session.commit()
    return jsonify({'id': act.id, 'name': act.name, 'activity_type': act.activity_type,
                    'type_label': act.type_label, 'status': act.status,
                    'status_label': act.status_label, 'status_color': act.status_color}), 201


@app.route('/api/activities/<int:actid>', methods=['PATCH', 'DELETE'])
@login_required
def api_activity(actid):
    if current_user.role not in ('engineer', 'manager', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403
    act = Activity.query.get_or_404(actid)
    if request.method == 'DELETE':
        db.session.delete(act)
        db.session.commit()
        return jsonify({'ok': True})
    data = request.get_json() or {}
    if 'name' in data:
        act.name = data['name'].strip()
    if 'activity_type' in data:
        act.activity_type = data['activity_type']
    if 'status' in data:
        act.status = data['status']
    db.session.commit()
    return jsonify({'id': act.id, 'name': act.name, 'activity_type': act.activity_type,
                    'type_label': act.type_label, 'status': act.status,
                    'status_label': act.status_label, 'status_color': act.status_color})


@app.route('/api/projects/<int:pid>/deploy', methods=['POST'])
@login_required
def api_deploy_project(pid):
    if current_user.role not in ('engineer', 'manager', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403
    data    = request.get_json() or {}
    modules = set(data.get('modules', []))
    if not modules:
        return jsonify({'error': 'Select at least one module'}), 400
    elements = WTG.query.filter_by(project_id=pid).all()
    created  = 0
    for el in elements:
        for area in el.areas:
            for act in area.activities:
                if act.activity_type in modules:
                    existing = QATest.query.filter_by(
                        area_id=area.id, test_type=act.activity_type).first()
                    if not existing:
                        db.session.add(QATest(area_id=area.id, test_type=act.activity_type))
                        created += 1
    db.session.commit()
    return jsonify({'ok': True, 'created': created})


@app.route('/api/projects/<int:pid>/reset-setup', methods=['DELETE'])
@login_required
def api_reset_project_setup(pid):
    if current_user.role not in ('engineer', 'manager', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403
    Project.query.get_or_404(pid)
    try:
        # Walk all elements → areas → tests/activities → delete bottom-up
        for el in list(WTG.query.filter_by(project_id=pid).all()):
            for area in list(el.areas):
                for test in list(area.required_tests):
                    for rec  in list(test.records):      db.session.delete(rec)
                    for pr   in list(test.proof_rolls):
                        for obj in list(pr.signatories):    db.session.delete(obj)
                        for obj in list(pr.equipment_rows): db.session.delete(obj)
                        for obj in list(pr.pr_photos):      db.session.delete(obj)
                        for obj in list(pr.rect_photos):    db.session.delete(obj)
                        db.session.delete(pr)
                    for ph in list(test.photos): db.session.delete(ph)
                    db.session.delete(test)
                for act in list(area.activities): db.session.delete(act)
                db.session.delete(area)
            db.session.delete(el)
        for wp in list(WorkPackage.query.filter_by(project_id=pid).all()):
            db.session.delete(wp)
        for g in list(WTGGroup.query.filter_by(project_id=pid).all()):
            db.session.delete(g)
        db.session.commit()
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/elements/<int:eid>/areas', methods=['POST'])
@login_required
def api_add_area(eid):
    if current_user.role not in ('engineer', 'manager', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403
    el   = WTG.query.get_or_404(eid)
    data = request.get_json() or {}
    area_type = data.get('area_type', '').strip()
    label     = data.get('label', '').strip() or area_type.replace('_', ' ').title()
    tests     = data.get('tests', [])   # list of test_type strings
    if not area_type:
        return jsonify({'error': 'area_type is required'}), 400
    area = Area(wtg_id=el.id, area_type=area_type, label=label)
    db.session.add(area)
    db.session.flush()
    for tt in tests:
        db.session.add(QATest(area_id=area.id, test_type=tt))
    db.session.commit()
    return jsonify({'id': area.id, 'area_type': area.area_type, 'label': area.label,
                    'test_count': len(area.required_tests)})


@app.route('/api/areas/<int:aid>', methods=['DELETE'])
@login_required
def api_delete_area(aid):
    if current_user.role not in ('engineer', 'manager', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403
    area = Area.query.get_or_404(aid)
    try:
        # Explicitly cascade to avoid FK constraint issues on PostgreSQL
        for test in list(area.required_tests):
            for rec in list(test.records):
                db.session.delete(rec)
            for pr in list(test.proof_rolls):
                for obj in list(pr.signatories):    db.session.delete(obj)
                for obj in list(pr.equipment_rows): db.session.delete(obj)
                for obj in list(pr.pr_photos):      db.session.delete(obj)
                for obj in list(pr.rect_photos):    db.session.delete(obj)
                db.session.delete(pr)
            for ph in list(test.photos):
                db.session.delete(ph)
            db.session.delete(test)
        for act in list(area.activities):
            db.session.delete(act)
        db.session.delete(area)
        db.session.commit()
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/areas/<int:aid>/tests', methods=['POST'])
@login_required
def api_add_test(aid):
    if current_user.role not in ('engineer', 'manager', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403
    area = Area.query.get_or_404(aid)
    data = request.get_json() or {}
    test_type = data.get('test_type', '').strip()
    if not test_type:
        return jsonify({'error': 'test_type is required'}), 400
    t = QATest(area_id=area.id, test_type=test_type)
    db.session.add(t)
    db.session.commit()
    return jsonify({'id': t.id, 'test_type': t.test_type, 'display_name': t.display_name})


@app.route('/api/tests/<int:tid>', methods=['DELETE'])
@login_required
def api_delete_test(tid):
    if current_user.role not in ('engineer', 'manager', 'admin'):
        return jsonify({'error': 'Forbidden'}), 403
    t = QATest.query.get_or_404(tid)
    db.session.delete(t)
    db.session.commit()
    return jsonify({'ok': True})


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
    """Proof Rolling landing page — shows all WTGs with proof roll status, grouped."""
    from flask import g
    proj = getattr(g, 'project', None)
    q = WTG.query.order_by(WTG.name)
    if proj:
        q = q.filter_by(project_id=proj.id)
    wtgs = q.all()

    summary = []
    total_records = total_passed = total_failed = total_pending = 0

    from sqlalchemy import func
    pr_doc_counts = {}
    for row in (db.session.query(DocumentLink.link_id, func.count(DocumentLink.id))
                .filter_by(link_type='proof_roll')
                .group_by(DocumentLink.link_id).all()):
        pr_doc_counts[row[0]] = row[1]

    for wtg in wtgs:
        wtg_entry = {'wtg': wtg, 'areas': []}
        for area in sorted(wtg.areas, key=lambda a: a.label):
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

    # Build grouped structure — GROUP → WP → ELEMENT hierarchy
    groups_map = {}
    ungrouped  = []
    for entry in summary:
        wtg   = entry['wtg']
        g_obj = (wtg.work_package.group if wtg.work_package else None) or wtg.group
        wp_obj = wtg.work_package
        if g_obj:
            if g_obj.id not in groups_map:
                groups_map[g_obj.id] = {'group': g_obj, 'wps': {}, 'ungrouped_entries': []}
            gd = groups_map[g_obj.id]
            if wp_obj:
                if wp_obj.id not in gd['wps']:
                    gd['wps'][wp_obj.id] = {'wp': wp_obj, 'entries': []}
                gd['wps'][wp_obj.id]['entries'].append(entry)
            else:
                gd['ungrouped_entries'].append(entry)
        else:
            ungrouped.append(entry)

    grouped = []
    for gd in sorted(groups_map.values(), key=lambda x: (x['group'].sort_order, x['group'].name)):
        wps_sorted = sorted(gd['wps'].values(), key=lambda x: (x['wp'].sort_order, x['wp'].name))
        all_entries = [e for w in wps_sorted for e in w['entries']] + gd['ungrouped_entries']
        grouped.append({
            'group': gd['group'],
            'entries': all_entries,
            'wps': wps_sorted,
            'ungrouped_entries': gd['ungrouped_entries'],
        })

    return render_template('proof_roll_index.html',
                           summary=summary,
                           grouped=grouped,
                           ungrouped=ungrouped,
                           proj=proj,
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

        # ── Signatories (2: engineer rep + client rep) ───────────────────
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

# ─── API: All elements summary (scoped to active project) ────────────────────
@app.route('/api/dashboard')
@login_required
def api_dashboard():
    from flask import g
    proj = getattr(g, 'project', None)
    q = WTG.query.order_by(WTG.name)
    if proj:
        q = q.filter_by(project_id=proj.id)
    wtgs = q.all()
    return jsonify([{'id':w.id,'name':w.name,'pct':w.completion_pct} for w in wtgs])

# ─── Interactive Map ─────────────────────────────────────────────────────────
@app.route('/map')
@login_required
def map_view():
    """Legacy /map route — redirect to active project's map or projects list."""
    pid = request.args.get('pid', type=int)
    if not pid:
        # Try to use the session active project
        from flask import session as flask_session
        pid = flask_session.get('active_project_id')
    if pid:
        return redirect(url_for('project_map', pid=pid))
    return redirect(url_for('projects'))


@app.route('/projects/<int:pid>/map')
@login_required
def project_map(pid):
    proj     = Project.query.get_or_404(pid)
    from flask import session as fsession
    fsession['active_project_id'] = pid   # switch active project when visiting map
    wtgs     = WTG.query.filter_by(project_id=pid).order_by(WTG.name).all()
    map_file = ProjectMapFile.query.filter_by(project_id=pid).first()
    return render_template('map.html', proj=proj, wtgs=wtgs, map_file=map_file)


@app.route('/projects/<int:pid>/map/upload', methods=['POST'])
@login_required
def project_map_upload(pid):
    """Accept a KML or KMZ file upload, parse it, store GeoJSON in DB."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    f = request.files['file']
    if not f or not f.filename:
        return jsonify({'error': 'Empty file'}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ('.kml', '.kmz'):
        return jsonify({'error': 'Only KML and KMZ files are supported'}), 400

    try:
        file_bytes = f.read()
        layers = kml_parser.parse_bytes(file_bytes, f.filename)
    except Exception as e:
        return jsonify({'error': f'Parse error: {str(e)}'}), 500

    if not layers:
        return jsonify({'error': 'No layers found in this file. Check it contains placemarks.'}), 400

    map_file = ProjectMapFile.query.filter_by(project_id=pid).first()
    if not map_file:
        map_file = ProjectMapFile(project_id=pid)
        db.session.add(map_file)

    map_file.filename    = secure_filename(f.filename)
    map_file.geojson_data = json.dumps(layers)
    map_file.layer_names  = json.dumps(list(layers.keys()))
    map_file.uploaded_by  = current_user.id
    map_file.uploaded_at  = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({
        'ok': True,
        'layers': list(layers.keys()),
        'counts': {k: len(v['features']) for k, v in layers.items()},
    })


@app.route('/projects/<int:pid>/map/delete', methods=['POST'])
@login_required
def project_map_delete(pid):
    map_file = ProjectMapFile.query.filter_by(project_id=pid).first()
    if map_file:
        db.session.delete(map_file)
        db.session.commit()
    return jsonify({'ok': True})


@app.route('/projects/<int:pid>/map/populate', methods=['POST'])
@login_required
def project_map_populate(pid):
    """Create or update WTG elements from map pins placed in identify mode.
    If an element with the same name already exists we update its map
    coordinates (upsert) instead of silently skipping it.
    """
    data     = request.get_json(force=True) or {}
    elements = data.get('elements', [])
    created  = 0
    updated  = 0
    for el in elements:
        name = (el.get('name') or '').strip()
        if not name:
            continue
        existing = WTG.query.filter_by(project_id=pid, name=name).first()
        if existing:
            # Update map position and type so re-identifying refreshes coordinates
            if el.get('lat') is not None:
                existing.northing     = el.get('lat')
                existing.easting      = el.get('lon')
                existing.element_type = el.get('type', existing.element_type or 'wtg')
            updated += 1
        else:
            wtg = WTG(
                name         = name,
                project_id   = pid,
                element_type = el.get('type', 'wtg'),
                northing     = el.get('lat'),
                easting      = el.get('lon'),
            )
            db.session.add(wtg)
            created += 1
    db.session.commit()
    return jsonify({'ok': True, 'created': created, 'updated': updated, 'skipped': 0})


# ── Weather API — uses wttr.in (handles AU postcodes + small towns) ────────────
_wx_cache = {}   # { cache_key: (data_dict, timestamp) }

# wttr.in weather codes → (emoji, label)
_WTTR = {
    113:('☀️','Sunny'),      116:('⛅','Partly cloudy'), 119:('☁️','Cloudy'),
    122:('☁️','Overcast'),   143:('🌫️','Mist'),         176:('🌦️','Patchy rain'),
    179:('🌨️','Patchy snow'),185:('🌦️','Drizzle'),      200:('⛈️','Thundery'),
    227:('❄️','Blowing snow'),230:('❄️','Blizzard'),     248:('🌫️','Fog'),
    260:('🌫️','Freezing fog'),263:('🌦️','Light drizzle'),266:('🌦️','Drizzle'),
    281:('🌧️','Freezing drizzle'),284:('🌧️','Heavy drizzle'),
    293:('🌦️','Light rain'), 296:('🌦️','Light rain'),   299:('🌧️','Moderate rain'),
    302:('🌧️','Moderate rain'),305:('🌧️','Heavy rain'),  308:('🌧️','Heavy rain'),
    311:('🌧️','Sleet'),      314:('🌧️','Moderate sleet'),317:('🌨️','Light sleet'),
    320:('🌨️','Moderate sleet'),323:('🌨️','Light snow'),326:('🌨️','Light snow'),
    329:('❄️','Moderate snow'),332:('❄️','Moderate snow'),335:('❄️','Heavy snow'),
    338:('❄️','Heavy snow'),  350:('🌧️','Ice pellets'),  353:('🌦️','Light showers'),
    356:('🌧️','Showers'),    359:('🌧️','Heavy showers'), 362:('🌧️','Sleet showers'),
    365:('🌧️','Sleet showers'),368:('🌨️','Snow showers'),371:('❄️','Snow showers'),
    374:('🌧️','Ice showers'), 377:('🌧️','Ice showers'),  386:('⛈️','Thunderstorm'),
    389:('⛈️','Thunderstorm'),392:('⛈️','Snow thunderstorm'),395:('⛈️','Blizzard storm'),
}

@app.route('/api/weather')
@login_required
def api_weather():
    """Fetch live weather via wttr.in.
    Accepts: loc=suburb, pc=postcode, lat=latitude, lon=longitude (any combo).
    """
    loc = request.args.get('loc', '').strip()
    pc  = request.args.get('pc',  '').strip()
    lat = request.args.get('lat', '').strip()
    lon = request.args.get('lon', '').strip()

    if not loc and not pc and not (lat and lon):
        return jsonify({'error': 'no location'}), 400

    cache_key = f"{loc}|{pc}|{lat}|{lon}"
    now_ts = datetime.now(timezone.utc)
    if cache_key in _wx_cache:
        data, ts = _wx_cache[cache_key]
        if (now_ts - ts).total_seconds() < 1800:
            return jsonify(data)

    # Build candidate query strings — most specific first
    candidates = []
    if loc and pc: candidates.append(f"{loc} {pc}")
    if loc:        candidates.append(loc)
    if pc:         candidates.append(pc)
    if lat and lon: candidates.append(f"{lat},{lon}")   # wttr.in supports lat,lon directly

    def _parse(w, fallback_q):
        c    = w['current_condition'][0]
        area = w.get('nearest_area', [{}])[0]
        city = (area.get('areaName', [{}])[0].get('value') or
                area.get('region',  [{}])[0].get('value') or fallback_q)
        rgn  =  area.get('region',  [{}])[0].get('value', '')
        code        = int(c.get('weatherCode', 113))
        ico, label  = _WTTR.get(code, ('🌡️', c['weatherDesc'][0]['value']))
        return {
            'temp':       int(c['temp_C']),
            'icon':       ico,
            'cond':       label,
            'wind_speed': int(c['windspeedKmph']),
            'wind_dir':   int(c.get('winddirDegree', 0)),
            'city':       city,
            'admin':      rgn,
        }

    for q in candidates:
        url = f'https://wttr.in/{urllib.parse.quote(q)}?format=j1'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'WindfarmManager/1.0',
            'Accept':     'application/json',
        })
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                w = json.loads(r.read())
            data = _parse(w, q)
            _wx_cache[cache_key] = (data, now_ts)
            return jsonify(data)
        except Exception:
            continue

    return jsonify({'error': 'weather unavailable'}), 503


# ── Suburb / Postcode autocomplete — Australian locations ─────────────────────
_suburb_cache = {}

_STATE_ABBR = {
    'New South Wales': 'NSW', 'Victoria': 'VIC', 'Queensland': 'QLD',
    'South Australia': 'SA', 'Western Australia': 'WA', 'Tasmania': 'TAS',
    'Australian Capital Territory': 'ACT', 'Northern Territory': 'NT',
}

@app.route('/api/suburb-search')
@login_required
def api_suburb_search():
    """Return AU suburb+postcode suggestions for autocomplete."""
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    ql = q.lower()
    now_ts = datetime.now(timezone.utc)
    if ql in _suburb_cache:
        data, ts = _suburb_cache[ql]
        if (now_ts - ts).total_seconds() < 3600:
            return jsonify(data)
    url = (f'https://nominatim.openstreetmap.org/search'
           f'?q={urllib.parse.quote(q)}&countrycodes=au'
           f'&format=json&addressdetails=1&limit=10')
    req = urllib.request.Request(url, headers={'User-Agent': 'WindfarmManager/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            results = json.loads(r.read())
        seen, out = set(), []
        for item in results:
            addr    = item.get('address', {})
            suburb  = (addr.get('suburb') or addr.get('city') or addr.get('town')
                       or addr.get('village') or addr.get('county') or '').strip()
            state   = addr.get('state', '')
            pc      = addr.get('postcode', '').strip()
            if not suburb or not pc:
                continue
            key = f"{suburb.lower()}|{pc}"
            if key in seen:
                continue
            seen.add(key)
            out.append({
                'suburb':   suburb,
                'state':    _STATE_ABBR.get(state, state[:3].upper() if state else ''),
                'postcode': pc,
            })
            if len(out) >= 6:
                break
        _suburb_cache[ql] = (out, now_ts)
        return jsonify(out)
    except Exception:
        return jsonify([])


@app.route('/api/projects/<int:pid>/map/geojson')
@login_required
def api_project_map_geojson(pid):
    """Return the project's uploaded map as GeoJSON."""
    map_file = ProjectMapFile.query.filter_by(project_id=pid).first()
    if not map_file or not map_file.geojson_data:
        return jsonify({})
    try:
        return jsonify(json.loads(map_file.geojson_data))
    except Exception:
        return jsonify({})


# ── Legacy KML API (KRWF only, kept for backward compat) ─────────────────────
@app.route('/api/kml/geojson')
@login_required
def api_kml_geojson():
    data = get_geojson(use_cache=True)
    return jsonify(data)

@app.route('/api/kml/refresh')
@login_required
def api_kml_refresh():
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
    """Landing: choose Element + ITP type — scoped to active project."""
    proj = getattr(g, 'project', None)
    if proj:
        wtgs    = WTG.query.filter_by(project_id=proj.id).order_by(WTG.name).all()
        wtg_ids = [w.id for w in wtgs]
        records = (ITPRecord.query
                   .filter(ITPRecord.wtg_id.in_(wtg_ids)).all()
                   if wtg_ids else [])
    else:
        wtgs    = WTG.query.order_by(WTG.name).all()
        records = ITPRecord.query.all()
    by_key = {(r.wtg_id, r.itp_type): r for r in records}
    return render_template('itp_index.html',
                           wtgs=wtgs,
                           itp_types=list(ITP_DEFINITIONS.keys()),
                           itp_defs=ITP_DEFINITIONS,
                           by_key=by_key)


# ─── Project-specific ITP (new-style, any project) ───────────────────────────

@app.route('/projects/<int:pid>/itp')
@login_required
def project_itp_index(pid):
    """Landing page for project-specific ITPs — welcome screen or grid if ITPs exist."""
    proj = Project.query.get_or_404(pid)
    if not _user_in_project(pid):
        abort(403)
    from flask import session as fsession
    fsession['active_project_id'] = pid

    templates = (ProjectITPTemplate.query
                 .filter_by(project_id=pid, is_active=True)
                 .order_by(ProjectITPTemplate.id).all())

    # All records for this project's templates
    tid_list = [t.id for t in templates]
    records_all = (ITPRecord.query
                   .filter(ITPRecord.project_itp_template_id.in_(tid_list)).all()
                   if tid_list else [])
    by_key = {(r.wtg_id, r.project_itp_template_id): r for r in records_all}

    # Hierarchy for the welcome mini-tree
    elements      = WTG.query.filter_by(project_id=pid).order_by(WTG.name).all()
    groups        = WTGGroup.query.filter_by(project_id=pid).order_by(WTGGroup.sort_order, WTGGroup.name).all()
    work_packages = WorkPackage.query.filter_by(project_id=pid).order_by(WorkPackage.sort_order, WorkPackage.name).all()

    # Build per-template filtered element lists based on applicable_scope
    def _scope_elements(tmpl, all_els):
        scope = tmpl.applicable_scope  # [{type, id, name}]
        if not scope:
            return all_els
        eids = set()
        for s in scope:
            stype = s.get('type')
            sid   = s.get('id')
            if stype == 'element':
                eids.add(sid)
            elif stype == 'wp':
                eids.update(e.id for e in all_els if e.work_package_id == sid)
            elif stype == 'group':
                eids.update(e.id for e in all_els if e.group_id == sid)
        return [e for e in all_els if e.id in eids] if eids else all_els

    elements_by_tid = {t.id: _scope_elements(t, elements) for t in templates}

    return render_template('project_itp_index.html',
                           proj=proj, templates=templates,
                           by_key=by_key, elements_by_tid=elements_by_tid,
                           elements=elements, groups=groups, work_packages=work_packages)


@app.route('/projects/<int:pid>/itp/create', methods=['GET', 'POST'])
@login_required
def project_itp_create(pid):
    """3-step ITP creation wizard."""
    proj = Project.query.get_or_404(pid)
    if not _user_in_project(pid):
        abort(403)
    from flask import session as fsession
    fsession['active_project_id'] = pid

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        t = ProjectITPTemplate(
            project_id   = pid,
            itp_number   = data.get('itp_number', '01').strip(),
            name         = data.get('name', 'Untitled ITP').strip(),
            revision     = data.get('revision', 'A').strip(),
            date         = data.get('date', '').strip(),
            works        = data.get('works', '').strip(),
            spec         = data.get('spec', '').strip(),
            scope        = data.get('scope', '').strip(),
            prepared_by  = data.get('prepared_by', '').strip(),
            approved_by  = data.get('approved_by', '').strip(),
            items_json   = json.dumps(data.get('items', [])),
            applicable_scope_json = json.dumps(data.get('scope_selection', [])),
            created_by_id = current_user.id,
        )
        db.session.add(t)
        db.session.commit()
        return jsonify({'ok': True, 'id': t.id,
                        'redirect': url_for('project_itp_index', pid=pid)})

    elements      = WTG.query.filter_by(project_id=pid).order_by(WTG.name).all()
    groups        = WTGGroup.query.filter_by(project_id=pid).order_by(WTGGroup.sort_order, WTGGroup.name).all()
    work_packages = WorkPackage.query.filter_by(project_id=pid).order_by(WorkPackage.sort_order, WorkPackage.name).all()
    existing_count = ProjectITPTemplate.query.filter_by(project_id=pid).count()
    suggested_no   = str(existing_count + 1).zfill(2)

    return render_template('project_itp_wizard.html',
                           proj=proj, elements=elements,
                           groups=groups, work_packages=work_packages,
                           suggested_no=suggested_no)


@app.route('/projects/<int:pid>/itp/<int:tid>/element/<int:eid>', methods=['GET', 'POST'])
@login_required
def project_itp_detail(pid, tid, eid):
    """Full ITP checklist for a project-specific template + element."""
    proj     = Project.query.get_or_404(pid)
    if not _user_in_project(pid):
        abort(403)
    template = ProjectITPTemplate.query.filter_by(id=tid, project_id=pid).first_or_404()
    wtg      = WTG.query.filter_by(id=eid, project_id=pid).first_or_404()
    defn     = template.to_dict()
    itp_type = template.itp_type_key

    # Get or create ITPRecord
    record = ITPRecord.query.filter_by(wtg_id=eid, itp_type=itp_type).first()
    if not record:
        record = ITPRecord(
            wtg_id=eid, itp_type=itp_type,
            project_itp_template_id=tid,
            created_by=current_user.id, status='draft',
            engineer_name=current_user.name,
            engineer_company=(current_user.company or ''),
        )
        db.session.add(record)
        db.session.flush()

    # Ensure per-criterion rows exist
    existing_keys = {(s.item_no, s.criterion_index) for s in record.item_statuses}
    for item in defn['items']:
        for ci, crit in enumerate(item.get('criteria', [])):
            if (item['no'], ci) not in existing_keys:
                row = item.get('rows', [])[ci] if ci < len(item.get('rows', [])) else {}
                db.session.add(ITPItemStatus(
                    itp_record_id=record.id, item_no=item['no'],
                    criterion_index=ci, activity=item['activity'],
                    criterion_text=crit,
                    inspection_code=row.get('inspection', ''),
                    frequency=row.get('frequency', ''),
                ))
    db.session.commit()

    statuses = {(s.item_no, s.criterion_index): s for s in record.item_statuses}

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'save_meta':
            if record.status == 'complete':
                flash('This ITP is complete and locked. Reopen it before editing.', 'danger')
                return redirect(url_for('project_itp_detail', pid=pid, tid=tid, eid=eid))
            record.lot_number       = request.form.get('lot_number', '').strip()
            record.location         = request.form.get('location', '').strip()
            record.engineer_name    = request.form.get('engineer_name', '').strip()
            record.engineer_company = request.form.get('engineer_company', '').strip()
            db.session.commit()
            flash('ITP details saved.', 'success')
            return redirect(url_for('project_itp_detail', pid=pid, tid=tid, eid=eid))

    now_dt = datetime.now().strftime('%Y-%m-%dT%H:%M')
    itp_links = DocumentLink.query.filter_by(link_type='itp_record', link_id=record.id).all()
    itp_doc_ids = [lnk.document_id for lnk in itp_links]
    itp_linked_docs = Document.query.filter(
        Document.id.in_(itp_doc_ids), Document.is_active == True).all() if itp_doc_ids else []

    return render_template('project_itp_detail.html',
                           proj=proj, template=template,
                           wtg=wtg, itp_type=itp_type,
                           defn=defn, record=record, statuses=statuses,
                           today=date.today().isoformat(),
                           now_dt=now_dt, linked_docs=itp_linked_docs,
                           pid=pid, tid=tid, eid=eid)


@app.route('/projects/<int:pid>/itp/<int:tid>/element/<int:eid>/save-meta', methods=['POST'])
@login_required
def api_project_itp_save_meta(pid, tid, eid):
    """AJAX — save ITP metadata (lot, engineer name/company). Location is read-only from WTG."""
    if not _user_in_project(pid):
        return jsonify({'error': 'Access denied.'}), 403
    # Verify template and element both belong to this project before touching the record
    ProjectITPTemplate.query.filter_by(id=tid, project_id=pid).first_or_404()
    WTG.query.filter_by(id=eid, project_id=pid).first_or_404()
    proj   = Project.query.get_or_404(pid)
    record = ITPRecord.query.filter_by(wtg_id=eid).filter(
        ITPRecord.project_itp_template_id == tid).first_or_404()
    if record.status == 'complete':
        return jsonify({'error': 'This ITP is complete and locked. Reopen it before editing.'}), 400
    data = request.get_json(silent=True) or {}
    record.lot_number       = (data.get('lot_number') or '').strip()
    record.engineer_name    = (data.get('engineer_name') or current_user.name).strip()
    record.engineer_company = (data.get('engineer_company') or current_user.company or '').strip()
    log_audit(
        'itp_metadata_changed',
        project_id  = pid,
        actor       = current_user,
        entity_type = 'itp_record',
        entity_id   = record.id,
        entity_label= f'{record.wtg.name if record.wtg else ""} (tid={tid})',
        detail      = {'lot_number': record.lot_number,
                       'engineer_name': record.engineer_name,
                       'engineer_company': record.engineer_company},
    )
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/projects/<int:pid>/itp/<int:tid>/element/<int:eid>/add-invite', methods=['POST'])
@login_required
def api_project_itp_add_invite(pid, tid, eid):
    """AJAX — add a client signatory to this ITP record."""
    if not _user_in_project(pid):
        return jsonify({'error': 'Access denied.'}), 403
    # Verify template and element both belong to this project before touching the record
    ProjectITPTemplate.query.filter_by(id=tid, project_id=pid).first_or_404()
    WTG.query.filter_by(id=eid, project_id=pid).first_or_404()
    record = ITPRecord.query.filter_by(wtg_id=eid).filter(
        ITPRecord.project_itp_template_id == tid).first_or_404()
    if record.status == 'complete':
        return jsonify({'error': 'This ITP is complete and locked. Reopen it before adding invites.'}), 400
    data    = request.get_json(silent=True) or {}
    name    = (data.get('name') or '').strip()
    company = (data.get('company') or '').strip()
    email   = (data.get('email') or '').strip()
    if not name:
        return jsonify({'error': 'Name is required.'}), 400

    # Ensure ONE canonical token exists for this ITP record
    is_new_token = False
    if not record.client_token:
        record.client_token      = uuid.uuid4().hex
        record.client_invited_at = datetime.now(timezone.utc)
        is_new_token = True
    if not record.client_name:
        record.client_name    = name
        record.client_company = company
        record.client_email   = email
    if record.status in ('draft', 'in_progress'):
        record.status = 'client_invited'

    # Each person gets their own unique token — the signing URL uses THIS token,
    # not the shared record.client_token (which is kept only for legacy fallback).
    invite_token = uuid.uuid4().hex
    invite = ITPClientInvite(
        record_id  = record.id,
        name       = name,
        company    = company,
        email      = email,
        token      = invite_token,
        expires_at = datetime.now(timezone.utc) + timedelta(days=14),
    )
    db.session.add(invite)
    db.session.flush()   # get invite.id before commit

    # Per-invitee signing URL — each person has their own unique link
    sign_url = url_for('itp_client_sign', token=invite_token, _external=True)

    log_audit(
        'itp_invite_created',
        project_id  = record.wtg.project_id if record.wtg else None,
        actor       = current_user,
        entity_type = 'itp_invite',
        entity_id   = invite.id,
        entity_label= f'{name} ({email or "no email"})',
        detail      = {'record_id': record.id, 'tid': tid, 'eid': eid},
    )
    db.session.commit()

    # Send invitation email if email provided
    wtg = record.wtg
    if email:
        itp_type = record.itp_type
        if itp_type.startswith('PROJ_'):
            tmpl = ProjectITPTemplate.query.get(record.project_itp_template_id)
            defn = tmpl.to_dict() if tmpl else {}
        else:
            defn = ITP_DEFINITIONS.get(itp_type, {})
        proj_name = wtg.project.name if wtg and wtg.project else 'Project'
        email_client_invitation(
            record=record, wtg_name=wtg.name if wtg else '',
            sign_url=sign_url, client_name=name, client_email=email,
            proj_name=proj_name, itp_name=defn.get('name', ''),
        )

    return jsonify({
        'ok':          True,
        'id':          invite.id,
        'name':        name,
        'company':     company,
        'email':       email,
        'sign_url':    sign_url,
        'is_new_link': is_new_token,
    })


@app.route('/projects/<int:pid>/itp/<int:tid>/element/<int:eid>/remove-invite/<int:inv_id>', methods=['POST'])
@login_required
def api_project_itp_remove_invite(pid, tid, eid, inv_id):
    """AJAX — revoke a client invite (marks as revoked, preserves audit trail)."""
    # Verify caller belongs to this project
    if not _user_in_project(pid):
        return jsonify({'error': 'Access denied.'}), 403

    invite = ITPClientInvite.query.get_or_404(inv_id)
    # Guard: ensure the invite belongs to this project
    if invite.record and invite.record.wtg and invite.record.wtg.project_id != pid:
        return jsonify({'error': 'Access denied.'}), 403

    invite.is_revoked = True
    invite.revoked_at = datetime.now(timezone.utc)

    log_audit(
        'itp_invite_revoked',
        project_id  = pid,
        actor       = current_user,
        entity_type = 'itp_invite',
        entity_id   = invite.id,
        entity_label= f'{invite.name} ({invite.email or "no email"})',
        detail      = {'record_id': invite.record_id, 'tid': tid, 'eid': eid},
    )
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/projects/<int:pid>/itp/<int:tid>', methods=['GET', 'POST'])
@login_required
def project_itp_template_view(pid, tid):
    """View/manage a single ITP template — shows applicable elements as cards."""
    proj     = Project.query.get_or_404(pid)
    if not _user_in_project(pid):
        abort(403)
    template = ProjectITPTemplate.query.filter_by(id=tid, project_id=pid).first_or_404()
    from flask import session as fsession
    fsession['active_project_id'] = pid

    itp_type = template.itp_type_key
    scope    = template.applicable_scope   # [{type, id, name}]

    # Collect applicable elements (respects element / wp / group scope types)
    all_elements = WTG.query.filter_by(project_id=pid).order_by(WTG.name).all()
    if not scope:
        elements = all_elements
    else:
        eids = set()
        for s in scope:
            stype = s.get('type')
            sid   = s.get('id')
            if stype == 'element':
                eids.add(sid)
            elif stype == 'wp':
                eids.update(e.id for e in all_elements if e.work_package_id == sid)
            elif stype == 'group':
                eids.update(e.id for e in all_elements if e.group_id == sid)
        elements = [e for e in all_elements if e.id in eids] if eids else all_elements

    records  = ITPRecord.query.filter_by(itp_type=itp_type).all()
    by_eid   = {r.wtg_id: r for r in records}

    return render_template('project_itp_template_view.html',
                           proj=proj, template=template,
                           elements=elements, by_eid=by_eid,
                           pid=pid, tid=tid)


@app.route('/projects/<int:pid>/itp/<int:tid>/delete', methods=['POST'])
@login_required
def project_itp_delete(pid, tid):
    """Soft-delete an ITP template. Requires manager or admin role."""
    if not _user_in_project(pid):
        abort(403)
    if current_user.role not in ('manager', 'admin'):
        abort(403)
    t = ProjectITPTemplate.query.filter_by(id=tid, project_id=pid).first_or_404()
    t.is_active = False
    db.session.commit()
    flash('ITP template deleted.', 'success')
    return redirect(url_for('project_itp_index', pid=pid))


@app.route('/projects/<int:pid>/itp/<int:tid>/element/<int:eid>/reopen', methods=['POST'])
@login_required
def itp_reopen(pid, tid, eid):
    """AJAX — reopen a complete ITP (increments revision, requires reason, admin/manager only)."""
    if not _user_in_project(pid):
        return jsonify({'error': 'Access denied.'}), 403
    if current_user.role not in ('manager', 'admin'):
        return jsonify({'error': 'Only project managers or admins can reopen a completed ITP.'}), 403
    # Verify template and element both belong to this project before touching the record
    ProjectITPTemplate.query.filter_by(id=tid, project_id=pid).first_or_404()
    WTG.query.filter_by(id=eid, project_id=pid).first_or_404()
    record = ITPRecord.query.filter_by(wtg_id=eid).filter(
        ITPRecord.project_itp_template_id == tid).first_or_404()
    if record.status not in ('complete', 'client_commented', 'client_signed'):
        return jsonify({'error': 'Only completed ITPs can be reopened.'}), 400
    data   = request.get_json(silent=True) or {}
    reason = (data.get('reason') or '').strip()
    if not reason:
        return jsonify({'error': 'A reason is required to reopen an ITP.'}), 400

    record.status          = 'reopened'
    record.revision        = (record.revision or 0) + 1
    record.reopened_at     = datetime.now(timezone.utc)
    record.reopened_by_id  = current_user.id
    record.reopen_reason   = reason

    log_audit(
        'itp_reopened',
        project_id  = pid,
        actor       = current_user,
        entity_type = 'itp_record',
        entity_id   = record.id,
        entity_label= f'{record.wtg.name if record.wtg else ""} (rev {record.revision})',
        detail      = {'reason': reason, 'new_revision': record.revision,
                       'tid': tid, 'eid': eid},
    )
    db.session.commit()
    return jsonify({'ok': True, 'revision': record.revision, 'status': record.status})


@app.route('/itp/<int:wtg_id>/<itp_type>', methods=['GET', 'POST'])
@login_required
def itp_detail(wtg_id, itp_type):
    """Full ITP checklist view + per-criterion sign-off."""
    if itp_type not in ITP_DEFINITIONS:
        abort(404)
    wtg  = WTG.query.get_or_404(wtg_id)
    if not _user_in_project(wtg.project_id):
        abort(403)
    defn = ITP_DEFINITIONS[itp_type]

    # Get or create ITPRecord
    record = ITPRecord.query.filter_by(wtg_id=wtg_id, itp_type=itp_type).first()
    if not record:
        record = ITPRecord(wtg_id=wtg_id, itp_type=itp_type,
                           created_by=current_user.id, status='draft',
                           engineer_name=current_user.name,
                           engineer_company=(current_user.company or ''))
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
            if record.status == 'complete':
                flash('This ITP is complete and locked. Reopen it before editing.', 'danger')
                return redirect(url_for('itp_detail', wtg_id=wtg_id, itp_type=itp_type))
            record.lot_number = request.form.get('lot_number', '').strip()
            record.location   = request.form.get('location', '').strip()
            record.engineer_name    = request.form.get('engineer_name', current_user.name or '').strip()
            record.engineer_company = request.form.get('engineer_company', current_user.company or '').strip()
            db.session.commit()
            flash('ITP details saved.', 'success')
            return redirect(url_for('itp_detail', wtg_id=wtg_id, itp_type=itp_type))

        # ── Invite client (legacy path — disabled; use project ITP invite API) ─
        elif action == 'invite_client':
            # The legacy CLIENTS list is KRWF-specific and this path is no longer
            # supported.  All client invites must go through the project ITP API
            # (/projects/<pid>/itp/<tid>/element/<eid>/add-invite) which uses
            # per-invitee tokens, expiry, and proper audit logging.
            flash('Client invites are managed via the project ITP page. '
                  'Please use the "Invite Client" panel there.', 'warning')
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


@app.route('/api/itp/client/<token>/item/<item_no>/<int:ci>', methods=['POST'])
def api_client_review_item(token, item_no, ci):
    """Public API — client per-item review (5 actions + legacy accept/concern)."""
    invite = ITPClientInvite.query.filter_by(token=token).first()
    if invite:
        # Enforce revocation
        if invite.is_revoked:
            return jsonify({'error': 'This signing link has been revoked.'}), 403
        # Enforce expiry
        if invite.expires_at and invite.expires_at < datetime.now(timezone.utc):
            return jsonify({'error': 'This signing link has expired.'}), 403
        record = invite.record
    else:
        # Legacy fallback — shared record.client_token
        record = ITPRecord.query.filter_by(client_token=token).first_or_404()

    # Allow review in any active state (ITP is open for the life of the project)
    if record.status not in ('client_invited', 'client_reviewing', 'in_progress'):
        return jsonify({'error': 'This ITP is not in a reviewable state.'}), 400

    s = ITPItemStatus.query.filter_by(
        itp_record_id   = record.id,
        item_no         = str(item_no),
        criterion_index = ci,
    ).first()
    if not s:
        return jsonify({'error': 'Item not found.'}), 404
    if not s.lucas_complete:
        return jsonify({'error': 'Item has not been signed by the engineer yet.'}), 400

    data   = request.get_json(silent=True) or {}
    action = data.get('action', '')

    # --- Approved / Accept (requires signature) ---
    if action in ('accept', 'approved'):
        sig = (data.get('signature') or '').strip()
        if not sig:
            return jsonify({'error': 'Please draw your signature before approving.'}), 400
        s.client_reviewed  = True
        s.client_accepted  = True
        s.client_action    = 'approved'
        s.client_comments  = ''
        s.client_signature = sig
        s.client_signed_at = datetime.now(timezone.utc)

    # --- Actions requiring a comment (no signature) ---
    elif action in ('concern', 'rejected', 'request_changes',
                    'request_clarification', 'not_accepted'):
        comment = (data.get('comment') or '').strip()
        if not comment:
            action_labels = {
                'concern':               'your concern',
                'rejected':              'the reason for rejection',
                'request_changes':       'what changes are required',
                'request_clarification': 'what clarification you need',
                'not_accepted':          'why this is not accepted',
            }
            return jsonify({'error': f'Please describe {action_labels.get(action, "the issue")} before submitting.'}), 400
        s.client_reviewed  = True
        s.client_accepted  = False
        s.client_action    = action if action != 'concern' else 'request_changes'
        s.client_comments  = comment
        s.client_signed_at = datetime.now(timezone.utc)

    elif action == 'reset':
        s.client_reviewed  = False
        s.client_accepted  = None
        s.client_action    = None
        s.client_comments  = ''
        s.client_signed_at = None
        s.client_signature = None

    else:
        return jsonify({'error': f'Unknown action: {action}'}), 400

    # Bump ITP status to client_reviewing so the engineer can see progress
    if record.status == 'client_invited':
        record.status = 'client_reviewing'

    # Log the audit event
    wtg_name = record.wtg.name if record.wtg else f'ITP #{record.id}'
    log_audit(
        'itp_item_client_reviewed',
        project_id   = record.wtg.project_id if record.wtg else None,
        actor        = None,  # public — no logged-in user
        entity_type  = 'itp_item',
        entity_id    = s.id,
        entity_label = f'{wtg_name} · {s.item_no}.{s.criterion_index + 1}',
        detail       = {'action': action, 'record_id': record.id,
                        'item_no': item_no, 'ci': ci},
    )

    db.session.commit()

    # Compute review progress totals
    signed_items = [x for x in record.item_statuses if x.lucas_complete]
    reviewed     = [x for x in signed_items if x.client_reviewed]
    accepted     = [x for x in reviewed if x.client_accepted]
    concerns     = [x for x in reviewed if not x.client_accepted]
    pending      = [x for x in signed_items if not x.client_reviewed]

    return jsonify({
        'ok':          True,
        'action':      s.client_action,
        'total':       len(signed_items),
        'reviewed':    len(reviewed),
        'accepted':    len(accepted),
        'concerns':    len(concerns),
        'pending':     len(pending),
        'all_reviewed': len(pending) == 0 and len(signed_items) > 0,
    })


@app.route('/itp/client/<token>', methods=['GET', 'POST'])
def itp_client_sign(token):
    """Public page for client to review + sign the ITP."""
    # Support per-invitee tokens (ITPClientInvite) and legacy shared record.client_token
    invite = ITPClientInvite.query.filter_by(token=token).first()
    link_error = None

    if invite:
        if invite.is_revoked:
            link_error = 'This signing link has been revoked by the project team.'
        elif invite.expires_at and invite.expires_at < datetime.now(timezone.utc):
            link_error = 'This signing link has expired. Please contact the project team for a new link.'
        record = invite.record
    else:
        # Legacy fallback — shared record.client_token
        record = ITPRecord.query.filter_by(client_token=token).first()
        if not record:
            abort(404)
        invite = None

    if link_error:
        wtg = record.wtg if record else None
        proj_name = wtg.project.name if wtg and wtg.project else 'Project'
        return render_template('itp_client_sign.html',
                               record=record, wtg=wtg, defn={},
                               statuses={}, proj_name=proj_name,
                               token=token, link_error=link_error,
                               today=date.today().isoformat()), 403

    wtg      = record.wtg
    # Resolve ITP definition (supports both KRWF and project-specific)
    if record.itp_type.startswith('PROJ_'):
        tmpl = ProjectITPTemplate.query.get(record.project_itp_template_id)
        defn = tmpl.to_dict() if tmpl else {}
    else:
        defn = ITP_DEFINITIONS.get(record.itp_type, {})
    statuses  = {(s.item_no, s.criterion_index): s for s in record.item_statuses}
    proj_name = wtg.project.name if wtg and wtg.project else 'Project'

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'client_sign':
            sig = request.form.get('client_signature', '').strip()
            if not sig:
                flash('Please draw your signature.', 'danger')
            else:
                # Ensure all engineer-signed items have been reviewed
                signed_items = [s for s in record.item_statuses if s.lucas_complete]
                unreviewed   = [s for s in signed_items if not s.client_reviewed]
                if unreviewed:
                    flash(f'Please review all {len(unreviewed)} remaining item(s) before signing.', 'danger')
                    return redirect(url_for('itp_client_sign', token=token))

                concerns = [s for s in signed_items if s.client_reviewed and not s.client_accepted]

                record.client_signature = sig
                record.client_signed_at = datetime.now(timezone.utc)
                record.status           = 'client_commented' if concerns else 'complete'

                # Audit: client signed the ITP
                log_audit(
                    'itp_client_signed',
                    project_id  = _itp_project_id(record),
                    actor       = None,   # public route — no authenticated user
                    entity_type = 'itp_record',
                    entity_id   = record.id,
                    entity_label= f'{wtg.name if wtg else ""} — {record.client_name or "Client"}',
                    detail      = {
                        'client_name':    record.client_name,
                        'client_company': record.client_company,
                        'status':         record.status,
                        'concerns':       len(concerns),
                    },
                )
                db.session.commit()

                # ── In-app notifications ────────────────────────────────────
                notify_users = User.query.filter(
                    User.role.in_(['engineer', 'supervisor', 'manager', 'admin'])
                ).all()
                itp_name  = defn.get('name', record.itp_type)
                notif_url = ''
                if record.project_itp_template_id and wtg:
                    try:
                        notif_url = url_for('project_itp_detail',
                                            pid=wtg.project_id,
                                            tid=record.project_itp_template_id,
                                            eid=wtg.id)
                    except Exception:
                        pass
                else:
                    try:
                        notif_url = url_for('itp_detail',
                                            wtg_id=wtg.id,
                                            itp_type=record.itp_type)
                    except Exception:
                        pass

                if concerns:
                    notif_type  = 'warning'
                    notif_title = f'Client Raised {len(concerns)} Concern(s) — {wtg.name}'
                    notif_msg   = (f'{record.client_name} ({record.client_company}) reviewed '
                                   f'"{itp_name}" for {wtg.name} · {proj_name} and raised '
                                   f'{len(concerns)} concern(s). Review their comments.')
                else:
                    notif_type  = 'itp_signed'
                    notif_title = f'ITP Approved by Client — {wtg.name}'
                    notif_msg   = (f'{record.client_name} ({record.client_company}) approved all '
                                   f'items in "{itp_name}" for {wtg.name} · {proj_name}')

                for u in notify_users:
                    db.session.add(Notification(
                        user_id = u.id,
                        type    = notif_type,
                        title   = notif_title,
                        message = notif_msg,
                        url     = notif_url,
                    ))
                db.session.commit()

                # ── Email notification to internal team ─────────────────────
                email_client_signed(
                    record       = record,
                    wtg_name     = wtg.name,
                    client_name  = record.client_name or 'Client',
                    notify_users = notify_users,
                    proj_name    = proj_name,
                    itp_name     = itp_name,
                )

                if concerns:
                    flash(f'Review submitted with {len(concerns)} concern(s). '
                          f'The inspection team has been notified.', 'success')
                else:
                    flash('ITP approved and signed. Thank you!', 'success')

            return redirect(url_for('itp_client_sign', token=token))

    return render_template('itp_client_sign.html',
                           record=record, wtg=wtg, defn=defn,
                           statuses=statuses, proj_name=proj_name,
                           token=token, invite=invite,
                           link_error=None,
                           today=date.today().isoformat())


@app.route('/api/itp/<int:record_id>/sign/<item_no>/<int:crit_idx>', methods=['POST'])
@login_required
def api_itp_sign_criterion(record_id, item_no, crit_idx):
    """AJAX: Save engineer signature for one criterion (with date + time)."""
    record = ITPRecord.query.get_or_404(record_id)
    project_id = _itp_project_id(record)

    if not _user_in_project(project_id):
        return jsonify({'error': 'Access denied — you are not a member of this project.'}), 403
    if not _user_can_sign(project_id):
        return jsonify({'error': 'You do not have permission to sign ITP criteria.'}), 403
    if record.status == 'complete':
        return jsonify({'error': 'This ITP is complete and locked. Ask the project admin to reopen it.'}), 400

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
        s.lucas_signed_at = datetime.strptime(dt_str[:16], '%Y-%m-%dT%H:%M')
    except (ValueError, TypeError):
        s.lucas_signed_at = datetime.now()

    # Update ITP record status from draft → in_progress, and auto-fill engineer name
    if record.status == 'draft':
        record.status = 'in_progress'
    if not record.engineer_name:
        record.engineer_name = current_user.name
    if not record.engineer_company:
        record.engineer_company = (current_user.company or '')

    log_audit(
        'itp_item_signed',
        project_id  = project_id,
        actor       = current_user,
        entity_type = 'itp_item',
        entity_id   = s.id,
        entity_label= f'{record.wtg.name if record.wtg else ""} · {item_no}.{crit_idx + 1}',
        detail      = {'record_id': record_id, 'item_no': item_no, 'ci': crit_idx},
    )

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
    record = ITPRecord.query.get_or_404(record_id)
    project_id = _itp_project_id(record)

    if not _user_in_project(project_id):
        return jsonify({'error': 'Access denied — you are not a member of this project.'}), 403
    if not _user_can_sign(project_id):
        return jsonify({'error': 'You do not have permission to modify ITP signatures.'}), 403
    if record.status == 'complete':
        return jsonify({'error': 'This ITP is complete and locked. Ask the project admin to reopen it.'}), 400

    s = ITPItemStatus.query.filter_by(
        itp_record_id=record_id, item_no=item_no, criterion_index=crit_idx
    ).first_or_404()
    s.lucas_complete   = False
    s.lucas_signature  = None
    s.lucas_signed_at  = None
    s.lucas_comments   = None

    log_audit(
        'itp_item_unsigned',
        project_id  = project_id,
        actor       = current_user,
        entity_type = 'itp_item',
        entity_id   = s.id,
        entity_label= f'{record.wtg.name if record.wtg else ""} · {item_no}.{crit_idx + 1}',
        detail      = {'record_id': record_id, 'item_no': item_no, 'ci': crit_idx},
    )

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
    record = ITPRecord.query.get_or_404(record_id)
    project_id = _itp_project_id(record)
    if not _user_in_project(project_id):
        return jsonify({'error': 'Access denied.'}), 403
    if record.status == 'complete':
        return jsonify({'error': 'This ITP is complete and locked. Reopen it before uploading.'}), 400
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
    db.session.flush()   # assign doc.id before log_audit reads it
    log_audit(
        'itp_document_uploaded',
        project_id  = project_id,
        actor       = current_user,
        entity_type = 'itp_document',
        entity_id   = doc.id,
        entity_label= f.filename,
        detail      = {'record_id': record_id, 'item_no': item_no, 'ci': crit_idx,
                       'filename': fname, 'doc_type': dtype},
    )
    db.session.commit()
    return jsonify({'id': doc.id, 'name': doc.original_name, 'url': doc.url, 'type': doc.doc_type})


@app.route('/api/itp/item-doc/<int:doc_id>/delete', methods=['POST'])
@login_required
def api_itp_item_doc_delete(doc_id):
    from models import ITPItemDocument
    doc = ITPItemDocument.query.get_or_404(doc_id)
    record = ITPRecord.query.get(doc.itp_record_id)
    project_id = _itp_project_id(record) if record else None
    if record and not _user_in_project(project_id):
        return jsonify({'error': 'Access denied.'}), 403
    if record and record.status == 'complete':
        return jsonify({'error': 'This ITP is complete and locked. Reopen it before deleting documents.'}), 400
    original_name = doc.original_name
    try:
        fpath = os.path.join(get_itp_docs_dir(), doc.filename)
        if os.path.exists(fpath):
            os.remove(fpath)
    except Exception:
        pass
    db.session.delete(doc)
    log_audit(
        'itp_document_deleted',
        project_id  = project_id,
        actor       = current_user,
        entity_type = 'itp_document',
        entity_id   = doc_id,
        entity_label= original_name,
        detail      = {'record_id': doc.itp_record_id if record else None,
                       'filename': doc.filename},
    )
    db.session.commit()
    return jsonify({'ok': True})


# ITP Print/PDF route
@app.route('/itp/<int:record_id>/print')
@login_required
def itp_print(record_id):
    """Render a print-friendly ITP for PDF download. Supports project-specific ITPs."""
    record = ITPRecord.query.get_or_404(record_id)
    if not _user_in_project(_itp_project_id(record)):
        abort(403)
    if record.itp_type.startswith('PROJ_'):
        tmpl = ProjectITPTemplate.query.get(record.project_itp_template_id)
        defn = tmpl.to_dict() if tmpl else None
    else:
        defn = ITP_DEFINITIONS.get(record.itp_type)
    if not defn:
        return 'ITP type not found', 404
    statuses  = {(s.item_no, s.criterion_index): s for s in record.item_statuses}
    proj_name = record.wtg.project.name if record.wtg and record.wtg.project else 'Project'
    return render_template('itp_print.html', record=record, defn=defn,
                           statuses=statuses, wtg=record.wtg, proj_name=proj_name)


# ITP bulk export page
@app.route('/itp/export')
@login_required
def itp_export():
    """Page to select and bulk-download ITPs as PDFs — scoped to active project."""
    from flask import g
    proj = getattr(g, 'project', None)
    if proj:
        if not _user_in_project(proj.id):
            abort(403)
        wtgs    = WTG.query.filter_by(project_id=proj.id).order_by(WTG.name).all()
        wtg_ids = [w.id for w in wtgs]
        records = (ITPRecord.query
                   .filter(ITPRecord.wtg_id.in_(wtg_ids))
                   .order_by(ITPRecord.wtg_id, ITPRecord.itp_type).all()
                   if wtg_ids else [])
    else:
        wtgs    = WTG.query.order_by(WTG.name).all()
        records = ITPRecord.query.order_by(ITPRecord.wtg_id, ITPRecord.itp_type).all()
    return render_template('itp_export.html', wtgs=wtgs, records=records,
                           itp_types=list(ITP_DEFINITIONS.keys()))


@app.route('/itp/export-zip', methods=['POST'])
@login_required
def itp_export_zip():
    """Generate a ZIP of self-contained HTML print pages for selected ITP records."""
    from flask import g, send_file
    import zipfile, io as _io
    data       = request.get_json() or {}
    record_ids = data.get('ids', [])
    if not record_ids:
        return jsonify({'error': 'No ITP records selected'}), 400

    zip_buf = _io.BytesIO()
    exported_ids = []
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for rid in record_ids:
            record = ITPRecord.query.get(rid)
            if not record:
                continue
            # Per-record authorization — skip records the user cannot access
            rec_pid = _itp_project_id(record)
            if not _user_in_project(rec_pid):
                continue
            if record.itp_type.startswith('PROJ_'):
                tmpl = ProjectITPTemplate.query.get(record.project_itp_template_id)
                defn = tmpl.to_dict() if tmpl else None
            else:
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
            exported_ids.append(rid)

    zip_buf.seek(0)
    proj = getattr(g, 'project', None)
    safe_name = (proj.name.replace(' ', '_') if proj else 'Project')

    # Audit: log the export action
    log_audit(
        'itp_exported_zip',
        project_id  = proj.id if proj else None,
        actor       = current_user,
        entity_type = 'itp_export',
        entity_id   = None,
        entity_label= safe_name,
        detail      = {'record_ids': exported_ids, 'count': len(exported_ids)},
    )
    db.session.commit()

    return send_file(
        zip_buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'{safe_name}_ITPs.zip'
    )


# ══════════════════════════════════════════════════════════════════════════════
# PROJECT ITP BACKUP (ZIP)
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/projects/<int:pid>/itp/backup', methods=['GET', 'POST'])
@login_required
def project_itp_backup(pid):
    """Page + ZIP generator for project-specific ITP backup."""
    proj      = Project.query.get_or_404(pid)
    if not _user_in_project(pid):
        abort(403)
    templates = (ProjectITPTemplate.query
                 .filter_by(project_id=pid, is_active=True)
                 .order_by(ProjectITPTemplate.id).all())

    if request.method == 'POST':
        import zipfile, io as _io
        data = request.get_json(silent=True) or {}
        raw_tids = [int(x) for x in data.get('template_ids', [])]
        if not raw_tids:
            return jsonify({'error': 'No templates selected'}), 400

        # Only accept template_ids that actually belong to this project —
        # prevents a caller from injecting ids from other projects.
        valid_tids = {
            t.id for t in ProjectITPTemplate.query.filter(
                ProjectITPTemplate.id.in_(raw_tids),
                ProjectITPTemplate.project_id == pid,
            ).all()
        }
        tids = [t for t in raw_tids if t in valid_tids]
        if not tids:
            return jsonify({'error': 'No valid templates found for this project.'}), 400

        # Collect all records for these validated templates
        all_records = ITPRecord.query.filter(
            ITPRecord.project_itp_template_id.in_(tids)
        ).all()

        zip_buf = _io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for record in all_records:
                tmpl = ProjectITPTemplate.query.get(record.project_itp_template_id)
                if not tmpl:
                    continue
                defn     = tmpl.to_dict()
                statuses = {(s.item_no, s.criterion_index): s for s in record.item_statuses}
                html_content = render_template(
                    'itp_print.html',
                    record=record, defn=defn, statuses=statuses,
                    wtg=record.wtg, proj_name=proj.name
                )
                html_content = html_content.replace(
                    "window.addEventListener('load', () => setTimeout(() => window.print(), 900));",
                    "// Auto-print disabled in ZIP export"
                )
                el_name  = record.wtg.name if record.wtg else f'el{record.wtg_id}'
                itp_name = tmpl.name.replace(' ', '_').replace('/', '-')[:40]
                fname    = f"{itp_name}_{el_name}.html"
                zf.writestr(fname, html_content)

        if zip_buf.tell() == 0:
            return jsonify({'error': 'No printable records found'}), 400

        zip_buf.seek(0)
        from flask import send_file
        safe_proj = proj.name.replace(' ', '_')
        return send_file(zip_buf, mimetype='application/zip', as_attachment=True,
                         download_name=f'{safe_proj}_ITPs_Backup.zip')

    return render_template('project_itp_backup.html', proj=proj, templates=templates)


# ══════════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/notifications')
@login_required
def notifications_list():
    notifs = (Notification.query
              .filter_by(user_id=current_user.id)
              .order_by(Notification.created_at.desc())
              .limit(100).all())
    # Mark all as read on page visit
    for n in notifs:
        n.is_read = True
    db.session.commit()
    return render_template('notifications.html', notifications=notifs)


@app.route('/api/notifications/unread-count')
@login_required
def api_notif_unread_count():
    count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return jsonify({'count': count})


@app.route('/api/notifications/<int:nid>/read', methods=['POST'])
@login_required
def api_notif_mark_read(nid):
    n = Notification.query.filter_by(id=nid, user_id=current_user.id).first_or_404()
    n.is_read = True
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/notifications/read-all', methods=['POST'])
@login_required
def api_notif_read_all():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    return jsonify({'ok': True})


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
    from flask import g
    proj = getattr(g, 'project', None)
    q = WTG.query.order_by(WTG.name)
    if proj:
        q = q.filter_by(project_id=proj.id)
    wtgs = q.all()

    active_stages = get_active_stages()
    wtg_ids = [w.id for w in wtgs]
    existing = {(s.wtg_id, s.stage_key) for s in
                FoundationStage.query.filter(FoundationStage.wtg_id.in_(wtg_ids)).all()
                if wtg_ids} if wtg_ids else set()
    new_stages = []
    for wtg in wtgs:
        for key, label in active_stages:
            if (wtg.id, key) not in existing:
                new_stages.append(FoundationStage(wtg_id=wtg.id, stage_key=key, stage_label=label))
    if new_stages:
        db.session.add_all(new_stages)
        db.session.commit()
    all_stages = (FoundationStage.query
                  .filter(FoundationStage.wtg_id.in_(wtg_ids)).all()
                  if wtg_ids else [])
    stage_map = {}
    for s in all_stages:
        stage_map.setdefault(s.wtg_id, {})[s.stage_key] = s
    active_keys = {k for k, _ in active_stages}
    active_all  = [s for s in all_stages if s.stage_key in active_keys]
    total_stages       = len(active_all)
    complete_stages    = sum(1 for s in active_all if s.status == 'complete')
    in_progress_stages = sum(1 for s in active_all if s.status == 'in_progress')
    not_started_stages = sum(1 for s in active_all if s.status == 'not_started')

    # Build grouped structure
    groups_map = {}
    ungrouped  = []
    for wtg in wtgs:
        if wtg.group:
            if wtg.group.id not in groups_map:
                groups_map[wtg.group.id] = {'group': wtg.group, 'wtgs': []}
            groups_map[wtg.group.id]['wtgs'].append(wtg)
        else:
            ungrouped.append(wtg)
    grouped = sorted(groups_map.values(), key=lambda x: (x['group'].sort_order, x['group'].name))

    return render_template('foundation_index.html', wtgs=wtgs, stage_map=stage_map,
                           stages=active_stages, grouped=grouped, ungrouped=ungrouped,
                           proj=proj,
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
    # Create FoundationStage rows for all WTGs across all projects (global template)
    wtgs = WTG.query.all()
    for wtg in wtgs:
        exists = FoundationStage.query.filter_by(wtg_id=wtg.id, stage_key=key).first()
        if not exists:
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
    from flask import g
    proj = getattr(g, 'project', None)
    widgets = ProgressWidget.query.order_by(ProgressWidget.sort_order).all()
    q = WTG.query.order_by(WTG.name)
    if proj:
        q = q.filter_by(project_id=proj.id)
    wtgs = q.all()
    return render_template('progress_tracker.html', widgets=widgets, wtgs=wtgs)


@app.route('/api/progress/data/<source>')
@login_required
def api_progress_data(source):
    """Return JSON data for a given chart data source (scoped to active project)."""
    from flask import g
    proj    = getattr(g, 'project', None)
    wtg_q   = WTG.query.order_by(WTG.name)
    if proj:
        wtg_q = wtg_q.filter_by(project_id=proj.id)

    if source == 'wtg_completion':
        wtgs = wtg_q.all()
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
        wtgs    = wtg_q.all()
        wtg_ids = [w.id for w in wtgs]
        complete_counts = []
        total = len(wtgs)
        for key, _ in FOUNDATION_STAGES:
            q = FoundationStage.query.filter_by(stage_key=key, status='complete')
            if wtg_ids:
                q = q.filter(FoundationStage.wtg_id.in_(wtg_ids))
            complete_counts.append(q.count())
        return jsonify({
            'labels': [lbl.split('–')[-1].strip()[:25] for _, lbl in FOUNDATION_STAGES],
            'datasets': [{
                'label': 'Elements Complete',
                'data': complete_counts,
                'backgroundColor': '#7c3aed'
            }, {
                'label': 'Total Elements',
                'data': [total] * len(FOUNDATION_STAGES),
                'backgroundColor': '#ede9fe',
                'type': 'line',
                'borderColor': '#7c3aed',
                'fill': False
            }]
        })

    elif source == 'status_breakdown':
        wtgs = wtg_q.all()
        complete    = sum(1 for w in wtgs if w.completion_pct == 100)
        in_prog     = sum(1 for w in wtgs if 0 < w.completion_pct < 100)
        not_started = sum(1 for w in wtgs if w.completion_pct == 0)
        return jsonify({
            'labels': ['Complete', 'In Progress', 'Not Started'],
            'datasets': [{'data': [complete, in_prog, not_started],
                          'backgroundColor': ['#22c55e','#f59e0b','#fca5a5']}]
        })

    elif source == 'area_completion':
        wtg_ids = [w.id for w in wtg_q.all()]
        # Get unique area types for this project
        if wtg_ids:
            all_areas = Area.query.filter(Area.wtg_id.in_(wtg_ids)).all()
        else:
            all_areas = []
        area_types = list(dict.fromkeys(a.area_type for a in all_areas))[:8]  # max 8
        labels = [at.replace('_', ' ').title() for at in area_types]
        data   = []
        for at in area_types:
            type_areas = [a for a in all_areas if a.area_type == at]
            avg = sum(a.completion_pct for a in type_areas) / len(type_areas) if type_areas else 0
            data.append(round(avg, 1))
        colors = ['#fca5a5','#86efac','#fde047','#93c5fd','#c4b5fd','#fdba74','#6ee7b7','#f9a8d4']
        return jsonify({
            'labels': labels,
            'datasets': [{'data': data,
                          'backgroundColor': colors[:len(labels)]}]
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

# ─── ITP access-control helpers ──────────────────────────────────────────────
def _itp_project_id(record):
    """Return the project_id for an ITPRecord (or None for legacy records)."""
    if record.wtg and record.wtg.project_id:
        return record.wtg.project_id
    return None


def _user_in_project(project_id):
    """True if the current authenticated user is a member of project_id."""
    if project_id is None:
        return True   # legacy records without project — allow
    if current_user.role in ('manager', 'admin'):
        return True
    return ProjectMember.query.filter_by(
        project_id=project_id, user_id=current_user.id
    ).first() is not None


def _user_can_sign(project_id):
    """True if the current authenticated user may sign ITP criteria.

    Lookup order:
      1. manager/admin — always allowed if they're in the project.
      2. PTM matched by user_id — honours can_sign exactly.
      3. PTM matched by email (when user_id not yet linked) — honours can_sign.
         Also back-fills user_id so future lookups are faster.
      4. No PTM found for this project → fall back to global can_enter_data().
    """
    if not _user_in_project(project_id):
        return False
    if current_user.role in ('manager', 'admin'):
        return True
    if project_id is not None:
        # Primary lookup — by user_id
        tm = ProjectTeamMember.query.filter_by(
            project_id=project_id, user_id=current_user.id
        ).first()
        if tm is not None:
            return bool(tm.can_sign)
        # Secondary lookup — by email (unlinked PTM row)
        if current_user.email:
            tm_email = ProjectTeamMember.query.filter_by(
                project_id=project_id, email=current_user.email
            ).filter(ProjectTeamMember.user_id == None).first()
            if tm_email is not None:
                # Back-fill user_id for faster future lookups
                try:
                    tm_email.user_id = current_user.id
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                return bool(tm_email.can_sign)
    return current_user.can_enter_data()


# ─── Safe column migrations ───────────────────────────────────────────────────
def run_migrations():
    """Add missing columns to existing tables.

    Compatible with both SQLite (local dev) and PostgreSQL (production).
    Never uses ADD COLUMN IF NOT EXISTS — SQLite rejects that syntax.
    Instead, each column is checked via SQLAlchemy Inspector before being added.
    """
    try:
        with app.app_context():
            from sqlalchemy import inspect as _sa_inspect, text as _sa_text

            insp           = _sa_inspect(db.engine)
            existing_tables = set(insp.get_table_names())

            # (table, new_column_name, column_type_sql)
            # No database-side DEFAULTs here — Python-model defaults cover new rows;
            # explicit UPDATE backfills (below) cover pre-existing rows.
            cols_to_add = [
                # ── users ───────────────────────────────────────────────────────
                ("users", "position",            "VARCHAR(100)"),
                ("users", "avatar_color",        "VARCHAR(20)"),
                # Phase 1 identity / security fields
                ("users", "is_active",           "BOOLEAN"),   # backfilled → 1 below
                ("users", "email_verified",      "BOOLEAN"),
                ("users", "email_verified_at",   "TIMESTAMP"),
                ("users", "last_login_at",       "TIMESTAMP"),
                ("users", "password_changed_at", "TIMESTAMP"),
                ("users", "failed_login_count",  "INTEGER"),   # backfilled → 0 below
                ("users", "locked_until",        "TIMESTAMP"),
                # ── itp_item_statuses ────────────────────────────────────────────
                ("itp_item_statuses",  "client_action", "VARCHAR(50)"),
                # ── itp_client_invites ────────────────────────────────────────────
                ("itp_client_invites", "expires_at",  "TIMESTAMP"),
                ("itp_client_invites", "is_revoked",  "BOOLEAN"),
                ("itp_client_invites", "revoked_at",  "TIMESTAMP"),
                # ── itp_records ──────────────────────────────────────────────────
                ("itp_records", "revision",         "INTEGER"),
                ("itp_records", "reopened_at",      "TIMESTAMP"),
                ("itp_records", "reopened_by_id",   "INTEGER"),
                ("itp_records", "reopen_reason",    "TEXT"),
            ]

            with db.engine.connect() as conn:
                for table, col, typedef in cols_to_add:
                    if table not in existing_tables:
                        continue   # table not yet created; db.create_all() handles it
                    existing_cols = {c["name"] for c in insp.get_columns(table)}
                    if col in existing_cols:
                        continue   # already present — nothing to do
                    try:
                        conn.execute(_sa_text(
                            f"ALTER TABLE {table} ADD COLUMN {col} {typedef}"
                        ))
                        conn.commit()
                    except Exception:
                        try: conn.rollback()
                        except Exception: pass

                # ── Backfill is_active → 1 for every pre-existing user row ──────
                # Flask-Login evaluates user.is_active before allowing login.
                # A NULL value is falsy and would silently block every existing account.
                # Use integer 1 (not TRUE) so the statement works on SQLite and Postgres.
                if 'users' in existing_tables:
                    try:
                        conn.execute(_sa_text(
                            "UPDATE users SET is_active = 1 WHERE is_active IS NULL"
                        ))
                        conn.commit()
                    except Exception:
                        try: conn.rollback()
                        except Exception: pass

                    # ── Backfill failed_login_count NULL → 0 ────────────────────
                    try:
                        conn.execute(_sa_text(
                            "UPDATE users SET failed_login_count = 0"
                            " WHERE failed_login_count IS NULL"
                        ))
                        conn.commit()
                    except Exception:
                        try: conn.rollback()
                        except Exception: pass

    except Exception:
        pass  # migration errors must never crash startup


# ─── Audit event helper ───────────────────────────────────────────────────────
def log_audit(event_type, *, project_id=None, actor=None, entity_type='',
              entity_id=None, entity_label='', detail=None, request=None):
    """Write one row to audit_events. Never raises — errors are swallowed."""
    try:
        from flask import request as _req
        req = request or _req
        ip  = ''
        try:
            ip = req.headers.get('X-Forwarded-For', req.remote_addr or '')
            if ip:
                ip = ip.split(',')[0].strip()
        except Exception:
            pass

        ev = AuditEvent(
            project_id    = project_id,
            event_type    = event_type,
            actor_user_id = actor.id      if actor else None,
            actor_name    = (actor.name   if actor else ''),
            actor_email   = (actor.email  if actor else ''),
            actor_company = (actor.company if actor else ''),
            actor_role    = (actor.role   if actor else ''),
            entity_type   = entity_type,
            entity_id     = entity_id,
            entity_label  = entity_label or '',
            detail_json   = json.dumps(detail or {}),
            ip_address    = ip,
        )
        db.session.add(ev)
        # Do NOT commit here — let the caller commit their own transaction.
        # The audit event will be committed together with the main change.
    except Exception as exc:
        print(f"[AUDIT] log_audit failed: {exc}")

# ─── Startup: create tables, dirs, seed ──────────────────────────────────────
def startup():
    create_dirs()
    with app.app_context():
        db.create_all()
    run_migrations()
    seed(app)
    with app.app_context():
        db.session.remove()   # clean up any sessions left open by seed

# ─── Run ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    startup()
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    port  = int(os.environ.get('PORT', 5000))
    app.run(debug=debug, host='0.0.0.0', port=port)
