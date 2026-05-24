"""
Email notifications via SendGrid.
Set SENDGRID_API_KEY and MAIL_FROM environment variables to enable.
If not set, emails are logged to console (no crash).

Required env vars:
  SENDGRID_API_KEY   — your SendGrid API key
  MAIL_FROM          — sender address (e.g. noreply@yourproject.com)
  APP_URL            — public app URL (e.g. https://yourapp.up.railway.app)

Public API
----------
Existing (ITP client flow — unchanged):
  send_email(to, subject, html, plain_text_content=None, reply_to=None)
  email_client_invitation(record, wtg_name, sign_url, client_name,
                          client_email, proj_name='', itp_name='')
  email_client_signed(record, wtg_name, client_name, notify_users,
                      proj_name='', itp_name='')

New (Phase 2 — invite / auth flow):
  email_project_invitation(to_email, invitee_name, inviter_name,
                           project_name, role_label, invite_url,
                           expires_at, company_name='')
  email_invite_accepted(to_email, inviter_name, invitee_name,
                        project_name, role_label='')
  email_password_reset(to_email, user_name, reset_url, expires_at)
  email_password_changed(to_email, user_name)
  email_role_changed(to_email, user_name, project_name,
                     old_role, new_role, changed_by_name)
"""
import os
import html as _html_lib
from datetime import datetime, timezone


# ══════════════════════════════════════════════════════════════════════════════
# LOW-LEVEL TRANSPORT
# ══════════════════════════════════════════════════════════════════════════════

def send_email(to_email, subject, html_content,
               plain_text_content=None, reply_to=None):
    """Send a single transactional email via SendGrid.

    Returns True on success, False on failure.
    Existing callers using positional (to_email, subject, html_content) args
    continue to work without modification.
    """
    api_key = os.environ.get('SENDGRID_API_KEY', '')
    if not api_key:
        print(f"[EMAIL] No SENDGRID_API_KEY — skipping email to {to_email}: {subject}")
        return False
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        from_email = os.environ.get('MAIL_FROM', 'noreply@qamanager.app')
        msg = Mail(
            from_email   = from_email,
            to_emails    = to_email,
            subject      = subject,
            html_content = html_content,
        )
        if plain_text_content:
            try:
                from sendgrid.helpers.mail import Content, MimeType
                msg.add_content(Content(MimeType.text, plain_text_content))
            except Exception:
                pass   # plain text is optional — don't fail the send
        if reply_to:
            try:
                from sendgrid.helpers.mail import ReplyTo
                msg.reply_to = ReplyTo(reply_to)
            except Exception:
                pass   # reply_to is optional — don't fail the send
        sg   = SendGridAPIClient(api_key)
        resp = sg.send(msg)
        print(f"[EMAIL] Sent to {to_email} — status {resp.status_code}")
        return True
    except Exception as e:
        print(f"[EMAIL] Failed to send to {to_email}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# PRIVATE HELPERS  (used by all email builders below)
# ══════════════════════════════════════════════════════════════════════════════

def _app_url():
    """Return APP_URL with no trailing slash, or empty string."""
    return os.environ.get('APP_URL', '').rstrip('/')


def _safe(text):
    """HTML-escape a value so it is safe to embed in email markup."""
    return _html_lib.escape(str(text or ''))


def _fmt_dt(dt, default='Not specified'):
    """Format a datetime as '14 Jun 2025 at 09:30 UTC'.  Handles None gracefully."""
    if dt is None:
        return default
    try:
        return dt.strftime('%d %b %Y at %H:%M UTC')
    except Exception:
        return str(dt)


def _action_button(label, url):
    """Return HTML for a centred primary CTA button."""
    return f"""
      <div style="text-align:center;margin:32px 0;">
        <a href="{url}"
           style="display:inline-block;background:#2563eb;color:#ffffff;
                  padding:14px 38px;border-radius:6px;text-decoration:none;
                  font-weight:600;font-size:15px;letter-spacing:0.01em;">
          {_safe(label)}
        </a>
      </div>"""


def _fallback_link(url):
    """Return HTML for the 'button not working?' fallback URL block."""
    return f"""
      <p style="color:#94a3b8;font-size:12px;margin:20px 0 0;
                border-top:1px solid #f1f5f9;padding-top:16px;line-height:1.8;">
        If the button does not work, copy this link into your browser:<br/>
        <a href="{url}" style="color:#2563eb;word-break:break-all;">{url}</a>
      </p>"""


def _detail_card(rows):
    """Return HTML for an info card.
    rows — list of (label, value) tuples; both are HTML-escaped automatically.
    """
    rows_html = ''.join(
        f"""<tr>
              <td style="color:#64748b;font-size:13px;padding:5px 16px 5px 0;
                         width:36%;vertical-align:top;">{_safe(label)}</td>
              <td style="font-size:13px;font-weight:600;color:#1e2d3d;
                         padding:5px 0;vertical-align:top;">{_safe(value)}</td>
            </tr>"""
        for label, value in rows
    )
    return f"""
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
                  padding:18px 20px 12px;margin:20px 0;">
        <table style="width:100%;border-collapse:collapse;">
          {rows_html}
        </table>
      </div>"""


def _security_note(text):
    """Return HTML for a small muted security/notice block."""
    return f"""
      <p style="color:#94a3b8;font-size:12px;margin:24px 0 0;line-height:1.7;">
        {_safe(text)}
      </p>"""


def _email_shell(title, body_html, preheader=''):
    """Wrap body_html in the standard professional email chrome.

    Produces a clean, mobile-friendly layout with no project-specific branding.
    """
    year     = datetime.now().year
    app_name = 'QA Manager'
    tagline  = 'Construction Quality Management Platform'
    pre_tag  = (
        f'<div style="display:none;max-height:0;overflow:hidden;'
        f'color:#f4f6f9;">{_safe(preheader)}&nbsp;</div>'
        if preheader else ''
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>{_safe(title)}</title>
</head>
<body style="margin:0;padding:0;background:#f4f6f9;
             font-family:Arial,Helvetica,sans-serif;">
{pre_tag}
<!-- Outer wrapper -->
<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#f4f6f9;">
  <tr><td align="center" style="padding:40px 16px 60px;">

    <!-- Card -->
    <table width="600" cellpadding="0" cellspacing="0" border="0"
           style="max-width:600px;width:100%;background:#ffffff;
                  border-radius:10px;overflow:hidden;
                  box-shadow:0 2px 12px rgba(0,0,0,.07);">

      <!-- Top accent bar -->
      <tr>
        <td style="background:#2563eb;height:4px;font-size:0;line-height:0;">&nbsp;</td>
      </tr>

      <!-- Header -->
      <tr>
        <td style="background:#1e2d3d;padding:26px 36px;">
          <p style="color:#ffffff;font-size:17px;font-weight:700;
                    margin:0;letter-spacing:0.04em;">{app_name}</p>
          <p style="color:#94a3b8;font-size:11px;margin:4px 0 0;
                    letter-spacing:0.03em;">{tagline}</p>
        </td>
      </tr>

      <!-- Body -->
      <tr>
        <td style="padding:36px 36px 28px;">
          {body_html}
        </td>
      </tr>

      <!-- Footer -->
      <tr>
        <td style="background:#f8fafc;border-top:1px solid #e9ecef;
                   padding:18px 36px;text-align:center;">
          <p style="color:#94a3b8;font-size:11px;margin:0;line-height:1.6;">
            &copy; {year} {app_name} &mdash; {tagline}<br/>
            This is an automated message. Please do not reply directly to this email.
          </p>
        </td>
      </tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
# EXISTING ITP CLIENT EMAILS  (unchanged — kept for backward compatibility)
# ══════════════════════════════════════════════════════════════════════════════

def email_client_invitation(record, wtg_name, sign_url,
                             client_name, client_email,
                             proj_name='', itp_name=''):
    """
    Send client a magic-link email to review and sign the ITP.
    No login required — the link goes straight to the signing page.
    """
    proj_label = proj_name or 'Project'
    itp_label  = itp_name  or record.itp_type.upper().replace('_', ' ')
    engineer   = record.engineer_name or 'Engineer'
    company    = record.engineer_company or ''

    subject = f"Action Required: Please Sign ITP — {wtg_name} · {itp_label}"
    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:Arial,Helvetica,sans-serif;">
<div style="max-width:600px;margin:30px auto;background:#ffffff;border-radius:16px;
            overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.1);">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#1e3a5f,#2563eb);padding:36px 32px;text-align:center;">
    <div style="width:56px;height:56px;background:rgba(255,255,255,.15);border-radius:14px;
                display:inline-flex;align-items:center;justify-content:center;margin-bottom:14px;">
      <span style="font-size:26px;">&#x1F4CB;</span>
    </div>
    <h1 style="color:#ffffff;margin:0;font-size:22px;font-weight:800;">{proj_label}</h1>
    <p style="color:#93c5fd;margin:6px 0 0;font-size:14px;">Quality Assurance — Signature Required</p>
  </div>

  <!-- Body -->
  <div style="padding:32px;">
    <p style="font-size:16px;color:#1e293b;font-weight:600;margin:0 0 8px;">Dear {client_name},</p>
    <p style="color:#64748b;font-size:14px;line-height:1.6;margin:0 0 24px;">
      <strong>{engineer}</strong> ({company}) has completed their inspection and invited you to
      review and sign the following Inspection &amp; Test Plan (ITP).
    </p>

    <!-- ITP summary card -->
    <div style="background:#f0f7ff;border:1px solid #bfdbfe;border-radius:12px;
                padding:20px;margin-bottom:24px;">
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <tr>
          <td style="color:#64748b;padding:5px 0;width:38%;vertical-align:top;">Project</td>
          <td style="font-weight:700;color:#1e3a5f;">{proj_label}</td>
        </tr>
        <tr>
          <td style="color:#64748b;padding:5px 0;">Element / Location</td>
          <td style="font-weight:700;color:#1e3a5f;">{wtg_name}</td>
        </tr>
        <tr>
          <td style="color:#64748b;padding:5px 0;">ITP Name</td>
          <td style="font-weight:700;color:#1e3a5f;">{itp_label}</td>
        </tr>
        <tr>
          <td style="color:#64748b;padding:5px 0;">Lot / Location</td>
          <td style="font-weight:700;color:#1e3a5f;">{record.lot_number or record.location or 'N/A'}</td>
        </tr>
        <tr>
          <td style="color:#64748b;padding:5px 0;">Inspected by</td>
          <td style="font-weight:700;color:#1e3a5f;">{engineer}{' · ' + company if company else ''}</td>
        </tr>
      </table>
    </div>

    <p style="color:#64748b;font-size:14px;margin:0 0 28px;line-height:1.6;">
      The inspection checklist is complete. Click the button below to
      <strong>review each signed criterion and provide your approval signature.</strong>
      No login required — the link takes you directly to the ITP.
    </p>

    <!-- CTA button -->
    <div style="text-align:center;margin:28px 0;">
      <a href="{sign_url}"
         style="display:inline-block;background:linear-gradient(135deg,#1d4ed8,#2563eb);
                color:#ffffff;padding:16px 40px;border-radius:10px;
                text-decoration:none;font-weight:700;font-size:16px;
                box-shadow:0 4px 14px rgba(37,99,235,.4);">
        &#x270D;&#xFE0F; &nbsp;Review &amp; Sign ITP
      </a>
    </div>

    <p style="color:#94a3b8;font-size:12px;margin:28px 0 0;border-top:1px solid #f1f5f9;
              padding-top:16px;line-height:1.8;">
      If the button doesn't work, copy this link into your browser:<br>
      <a href="{sign_url}" style="color:#2563eb;word-break:break-all;">{sign_url}</a>
    </p>
  </div>

  <!-- Footer -->
  <div style="background:#f8fafc;border-top:1px solid #f1f5f9;padding:16px 32px;text-align:center;">
    <p style="color:#94a3b8;font-size:12px;margin:0;">
      {proj_label} · QA Manager Platform &middot; Automated Notification
    </p>
  </div>
</div>
</body>
</html>
"""
    return send_email(client_email, subject, html)


def email_client_signed(record, wtg_name, client_name, notify_users,
                        proj_name='', itp_name=''):
    """
    Notify internal team that the client has signed the ITP.
    notify_users: list of User objects.
    """
    app_url   = os.environ.get('APP_URL', 'https://windfarm-manager-production.up.railway.app')
    proj_label = proj_name or 'Project'
    itp_label  = itp_name  or record.itp_type.upper().replace('_', ' ')
    signed_at  = (record.client_signed_at.strftime('%d %b %Y at %H:%M')
                  if record.client_signed_at else 'Just now')

    # Build ITP link
    if record.project_itp_template_id and record.wtg:
        itp_url = (f"{app_url}/projects/{record.wtg.project_id}"
                   f"/itp/{record.project_itp_template_id}"
                   f"/element/{record.wtg_id}")
    else:
        itp_url = f"{app_url}/itp/{record.wtg_id}/{record.itp_type}"

    subject = f"ITP Signed by Client — {wtg_name} · {itp_label}"
    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:Arial,Helvetica,sans-serif;">
<div style="max-width:600px;margin:30px auto;background:#ffffff;border-radius:16px;
            overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.1);">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#14532d,#16a34a);padding:36px 32px;text-align:center;">
    <div style="font-size:48px;margin-bottom:10px;">&#x2705;</div>
    <h1 style="color:#ffffff;margin:0;font-size:22px;font-weight:800;">ITP Signed by Client</h1>
    <p style="color:#86efac;margin:6px 0 0;font-size:14px;">{proj_label} · QA Manager</p>
  </div>

  <!-- Body -->
  <div style="padding:32px;">
    <p style="font-size:15px;color:#1e293b;line-height:1.6;margin:0 0 24px;">
      <strong>{client_name}</strong> has reviewed and signed the following ITP.
      The record is now marked as <strong style="color:#16a34a;">Complete</strong>.
    </p>

    <!-- ITP details card -->
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:12px;padding:20px;margin-bottom:24px;">
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <tr>
          <td style="color:#64748b;padding:5px 0;width:38%;">Project</td>
          <td style="font-weight:700;color:#166534;">{proj_label}</td>
        </tr>
        <tr>
          <td style="color:#64748b;padding:5px 0;">Element / Location</td>
          <td style="font-weight:700;color:#166534;">{wtg_name}</td>
        </tr>
        <tr>
          <td style="color:#64748b;padding:5px 0;">ITP</td>
          <td style="font-weight:700;color:#166534;">{itp_label}</td>
        </tr>
        <tr>
          <td style="color:#64748b;padding:5px 0;">Signed by</td>
          <td style="font-weight:700;color:#166534;">{client_name} (Client)</td>
        </tr>
        <tr>
          <td style="color:#64748b;padding:5px 0;">Signed at</td>
          <td style="font-weight:700;color:#166534;">{signed_at}</td>
        </tr>
      </table>
    </div>

    <div style="text-align:center;margin:28px 0;">
      <a href="{itp_url}"
         style="display:inline-block;background:linear-gradient(135deg,#166534,#16a34a);
                color:#ffffff;padding:14px 36px;border-radius:10px;
                text-decoration:none;font-weight:700;font-size:15px;
                box-shadow:0 4px 14px rgba(22,163,74,.35);">
        &#x1F4CB; &nbsp;View Signed ITP
      </a>
    </div>
  </div>

  <!-- Footer -->
  <div style="background:#f8fafc;border-top:1px solid #f1f5f9;padding:16px 32px;text-align:center;">
    <p style="color:#94a3b8;font-size:12px;margin:0;">
      {proj_label} · QA Manager Platform &middot; Automated Notification
    </p>
  </div>
</div>
</body>
</html>
"""
    sent = 0
    for user in notify_users:
        if user.email and send_email(user.email, subject, html):
            sent += 1
    return sent


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — INVITE / AUTH EMAILS
# ══════════════════════════════════════════════════════════════════════════════

def email_project_invitation(
    to_email,
    invitee_name,
    inviter_name,
    project_name,
    role_label,
    invite_url,
    expires_at,
    company_name='',
):
    """Notify a person that they have been invited to join a project.

    The invite_url must already contain the raw token as a query/path parameter;
    this function never generates or sees a token.
    """
    subject = f"You are invited to join {project_name} on QA Manager"

    rows = [
        ('Project',  project_name),
        ('Your role', role_label),
    ]
    if company_name:
        rows.append(('Company', company_name))
    rows.append(('Invited by', inviter_name))
    rows.append(('Invite expires', _fmt_dt(expires_at)))

    body = f"""
      <h2 style="color:#1e2d3d;font-size:20px;font-weight:700;margin:0 0 8px;">
        You have been invited
      </h2>
      <p style="color:#334155;font-size:14px;line-height:1.7;margin:0 0 4px;">
        <strong>{_safe(inviter_name)}</strong> has invited you to join
        <strong>{_safe(project_name)}</strong> on QA Manager as
        <strong>{_safe(role_label)}</strong>.
      </p>
      <p style="color:#64748b;font-size:13px;line-height:1.6;margin:0 0 20px;">
        Accept the invite to access the project, collaborate with your team,
        and view quality records.
      </p>

      {_detail_card(rows)}

      {_action_button('Accept Invitation', invite_url)}

      <p style="color:#64748b;font-size:13px;line-height:1.7;
                margin:20px 0 0;padding:16px;background:#fffbeb;
                border:1px solid #fde68a;border-radius:6px;">
        <strong>Security notice:</strong> This invitation was sent to
        <strong>{_safe(to_email)}</strong>. Do not forward this email or share
        the link &mdash; the invite is intended for you only.
      </p>

      {_fallback_link(invite_url)}"""

    return send_email(
        to_email, subject,
        _email_shell(subject, body, preheader=f"Invitation to join {project_name}"),
    )


def email_invite_accepted(
    to_email,
    inviter_name,
    invitee_name,
    project_name,
    role_label='',
):
    """Notify the inviter/project admin that an invitation was accepted."""
    subject = f"{invitee_name} accepted the invitation to {project_name}"

    rows = [
        ('Project',      project_name),
        ('Accepted by',  invitee_name),
    ]
    if role_label:
        rows.append(('Role assigned', role_label))

    login_url = _app_url() or '#'

    body = f"""
      <h2 style="color:#1e2d3d;font-size:20px;font-weight:700;margin:0 0 8px;">
        Invitation accepted
      </h2>
      <p style="color:#334155;font-size:14px;line-height:1.7;margin:0 0 20px;">
        <strong>{_safe(invitee_name)}</strong> has accepted your invitation and
        joined <strong>{_safe(project_name)}</strong>.
        {(' They have been assigned the role of <strong>' + _safe(role_label) + '</strong>.') if role_label else ''}
      </p>

      {_detail_card(rows)}

      {_action_button('Open QA Manager', login_url)}"""

    return send_email(
        to_email, subject,
        _email_shell(subject, body, preheader=f"{invitee_name} joined {project_name}"),
    )


def email_password_reset(
    to_email,
    user_name,
    reset_url,
    expires_at,
):
    """Send a password reset link to the user.

    The reset_url must already contain the raw token; this function never
    generates or stores tokens.
    """
    subject = "Reset your QA Manager password"

    body = f"""
      <h2 style="color:#1e2d3d;font-size:20px;font-weight:700;margin:0 0 8px;">
        Password reset request
      </h2>
      <p style="color:#334155;font-size:14px;line-height:1.7;margin:0 0 4px;">
        Hi <strong>{_safe(user_name)}</strong>,
      </p>
      <p style="color:#334155;font-size:14px;line-height:1.7;margin:0 0 20px;">
        We received a request to reset the password for your QA Manager account.
        Click the button below to choose a new password. This link expires on
        <strong>{_fmt_dt(expires_at)}</strong>.
      </p>

      {_action_button('Reset Password', reset_url)}

      <p style="color:#64748b;font-size:13px;line-height:1.7;
                margin:20px 0 0;padding:16px;background:#fffbeb;
                border:1px solid #fde68a;border-radius:6px;">
        <strong>Did not request this?</strong> You can safely ignore this email.
        Your password will not change unless you click the button above.
        This link will expire automatically after one hour.
      </p>

      {_fallback_link(reset_url)}"""

    return send_email(
        to_email, subject,
        _email_shell(subject, body, preheader="Reset your QA Manager password"),
    )


def email_password_changed(
    to_email,
    user_name,
):
    """Notify a user that their account password was just changed."""
    subject = "Your QA Manager password was changed"

    changed_at = _fmt_dt(datetime.now(timezone.utc))
    login_url  = _app_url() or '#'

    body = f"""
      <h2 style="color:#1e2d3d;font-size:20px;font-weight:700;margin:0 0 8px;">
        Password changed
      </h2>
      <p style="color:#334155;font-size:14px;line-height:1.7;margin:0 0 4px;">
        Hi <strong>{_safe(user_name)}</strong>,
      </p>
      <p style="color:#334155;font-size:14px;line-height:1.7;margin:0 0 20px;">
        The password for your QA Manager account was changed successfully
        on <strong>{changed_at}</strong>.
      </p>
      <p style="color:#334155;font-size:14px;line-height:1.7;margin:0 0 20px;">
        If you made this change, no further action is required.
      </p>

      <p style="color:#991b1b;font-size:13px;line-height:1.7;
                margin:20px 0 0;padding:16px;background:#fef2f2;
                border:1px solid #fecaca;border-radius:6px;">
        <strong>Did not make this change?</strong> Your account may have been
        accessed by someone else. Contact your project administrator immediately
        and do not log in until the issue has been investigated.
      </p>

      {_action_button('Log in to QA Manager', login_url)}"""

    return send_email(
        to_email, subject,
        _email_shell(subject, body, preheader="Your QA Manager password was changed"),
    )


def email_role_changed(
    to_email,
    user_name,
    project_name,
    old_role,
    new_role,
    changed_by_name,
):
    """Notify a user that their project role has been updated."""
    subject = f"Your role on {project_name} has been updated"

    rows = [
        ('Project',    project_name),
        ('Previous role', old_role),
        ('New role',   new_role),
        ('Updated by', changed_by_name),
        ('Updated at', _fmt_dt(datetime.now(timezone.utc))),
    ]

    login_url = _app_url() or '#'

    body = f"""
      <h2 style="color:#1e2d3d;font-size:20px;font-weight:700;margin:0 0 8px;">
        Project role updated
      </h2>
      <p style="color:#334155;font-size:14px;line-height:1.7;margin:0 0 4px;">
        Hi <strong>{_safe(user_name)}</strong>,
      </p>
      <p style="color:#334155;font-size:14px;line-height:1.7;margin:0 0 20px;">
        Your role on <strong>{_safe(project_name)}</strong> has been updated
        by <strong>{_safe(changed_by_name)}</strong>.
        Your access level and available features may have changed.
      </p>

      {_detail_card(rows)}

      <p style="color:#64748b;font-size:13px;line-height:1.7;margin:16px 0 0;">
        Log in to QA Manager to see your updated project access.
        If you believe this change was made in error, contact your
        project administrator.
      </p>

      {_action_button('Open QA Manager', login_url)}"""

    return send_email(
        to_email, subject,
        _email_shell(subject, body, preheader=f"Your role on {project_name} has changed"),
    )
