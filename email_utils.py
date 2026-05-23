"""
Email notifications via SendGrid.
Set SENDGRID_API_KEY and MAIL_FROM environment variables to enable.
If not set, emails are logged to console (no crash).

Required env vars:
  SENDGRID_API_KEY   — your SendGrid API key
  MAIL_FROM          — sender address (e.g. noreply@yourproject.com)
  APP_URL            — public app URL (e.g. https://yourapp.up.railway.app)
"""
import os


def send_email(to_email, subject, html_content):
    """Send a single email. Returns True on success, False on failure."""
    api_key = os.environ.get('SENDGRID_API_KEY', '')
    if not api_key:
        print(f"[EMAIL] No SENDGRID_API_KEY — skipping email to {to_email}: {subject}")
        return False
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        from_email = os.environ.get('MAIL_FROM', 'noreply@qamanager.app')
        msg = Mail(from_email=from_email, to_emails=to_email,
                   subject=subject, html_content=html_content)
        sg = SendGridAPIClient(api_key)
        resp = sg.send(msg)
        print(f"[EMAIL] Sent to {to_email} — status {resp.status_code}")
        return True
    except Exception as e:
        print(f"[EMAIL] Failed to send to {to_email}: {e}")
        return False


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
    company    = record.engineer_company or 'TCS'

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
      <span style="font-size:26px;">📋</span>
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
          <td style="font-weight:700;color:#1e3a5f;">{engineer} · {company}</td>
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
        ✍️ &nbsp;Review &amp; Sign ITP
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

    subject = f"✅ ITP Signed by Client — {wtg_name} · {itp_label}"
    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:Arial,Helvetica,sans-serif;">
<div style="max-width:600px;margin:30px auto;background:#ffffff;border-radius:16px;
            overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.1);">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#14532d,#16a34a);padding:36px 32px;text-align:center;">
    <div style="font-size:48px;margin-bottom:10px;">✅</div>
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
        📋 &nbsp;View Signed ITP
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
