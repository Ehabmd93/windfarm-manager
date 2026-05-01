"""
Email notifications via SendGrid.
Set SENDGRID_API_KEY and MAIL_FROM environment variables to enable.
If not set, emails are logged to console (no crash).
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
        from_email = os.environ.get('MAIL_FROM', 'noreply@kingrockswindfarm.com')
        msg = Mail(from_email=from_email, to_emails=to_email,
                   subject=subject, html_content=html_content)
        sg = SendGridAPIClient(api_key)
        resp = sg.send(msg)
        print(f"[EMAIL] Sent to {to_email} — status {resp.status_code}")
        return True
    except Exception as e:
        print(f"[EMAIL] Failed to send to {to_email}: {e}")
        return False


def email_client_invitation(record, wtg_name, sign_url, client_name, client_email):
    """
    Send client a magic-link email to review and sign the ITP.
    No login required — the link goes straight to the signing page.
    """
    itp_label = record.itp_type.upper().replace('_', ' ')
    subject = f"Action Required: Please Sign ITP — {wtg_name} {itp_label}"
    html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#f4f4f4;padding:20px;">
  <div style="background:white;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1);">

    <!-- Header -->
    <div style="background:#1e3a5f;padding:30px;text-align:center;">
      <h1 style="color:white;margin:0;font-size:22px;">King Rocks Wind Farm</h1>
      <p style="color:#93c5fd;margin:8px 0 0;">QA Manager &mdash; Signature Required</p>
    </div>

    <!-- Body -->
    <div style="padding:30px;">
      <p style="font-size:16px;color:#333;">Dear {client_name},</p>
      <p style="color:#555;">You have been invited to review and sign the following
         Inspection &amp; Test Plan:</p>

      <div style="background:#f0f7ff;border-left:4px solid #2563eb;border-radius:8px;padding:20px;margin:20px 0;">
        <table style="width:100%;border-collapse:collapse;">
          <tr><td style="color:#666;padding:6px 0;width:40%">WTG:</td>
              <td style="font-weight:bold;color:#1e3a5f;">{wtg_name}</td></tr>
          <tr><td style="color:#666;padding:6px 0;">ITP Type:</td>
              <td style="font-weight:bold;color:#1e3a5f;">{itp_label}</td></tr>
          <tr><td style="color:#666;padding:6px 0;">Lot Number:</td>
              <td style="font-weight:bold;color:#1e3a5f;">{record.lot_number or 'N/A'}</td></tr>
        </table>
      </div>

      <p style="color:#555;">Click the button below to review and sign.
         <strong>No login required</strong> — the link takes you directly to the ITP.</p>

      <div style="text-align:center;margin:30px 0;">
        <a href="{sign_url}"
           style="background:#2563eb;color:white;padding:16px 32px;border-radius:8px;
                  text-decoration:none;font-weight:bold;font-size:16px;display:inline-block;">
          ✍️ Review &amp; Sign ITP
        </a>
      </div>

      <p style="color:#888;font-size:13px;border-top:1px solid #eee;padding-top:20px;">
        If the button doesn&rsquo;t work, copy this link into your browser:<br>
        <a href="{sign_url}" style="color:#2563eb;word-break:break-all;">{sign_url}</a>
      </p>
    </div>

    <!-- Footer -->
    <div style="background:#f8f8f8;padding:15px;text-align:center;border-top:1px solid #eee;">
      <p style="color:#aaa;font-size:12px;margin:0;">
        King Rocks Wind Farm QA Manager &middot; Automated Notification
      </p>
    </div>
  </div>
</div>
"""
    return send_email(client_email, subject, html)


def email_client_signed(record, wtg_name, client_name, notify_users):
    """
    Notify engineer / supervisor / manager that the client has signed the ITP.
    notify_users: list of User objects.
    """
    app_url = os.environ.get('APP_URL',
                             'https://windfarm-manager-production.up.railway.app')
    itp_label = record.itp_type.upper().replace('_', ' ')
    itp_url   = f"{app_url}/itp/{record.wtg_id}/{record.itp_type}"
    signed_at = (record.client_signed_at.strftime('%d %b %Y %H:%M')
                 if record.client_signed_at else 'Just now')

    subject = f"\u2705 ITP Signed by Client \u2014 {wtg_name} {itp_label}"
    html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#f4f4f4;padding:20px;">
  <div style="background:white;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1);">

    <!-- Header -->
    <div style="background:#166534;padding:30px;text-align:center;">
      <h1 style="color:white;margin:0;font-size:22px;">&#10003; ITP Signed by Client</h1>
      <p style="color:#86efac;margin:8px 0 0;">King Rocks Wind Farm QA Manager</p>
    </div>

    <!-- Body -->
    <div style="padding:30px;">
      <p style="font-size:16px;color:#333;">The following ITP has been signed by the client:</p>

      <div style="background:#f0fdf4;border-left:4px solid #16a34a;border-radius:8px;padding:20px;margin:20px 0;">
        <table style="width:100%;border-collapse:collapse;">
          <tr><td style="color:#666;padding:6px 0;width:40%">WTG:</td>
              <td style="font-weight:bold;color:#166534;">{wtg_name}</td></tr>
          <tr><td style="color:#666;padding:6px 0;">ITP Type:</td>
              <td style="font-weight:bold;color:#166534;">{itp_label}</td></tr>
          <tr><td style="color:#666;padding:6px 0;">Lot Number:</td>
              <td style="font-weight:bold;color:#166534;">{record.lot_number or 'N/A'}</td></tr>
          <tr><td style="color:#666;padding:6px 0;">Signed by:</td>
              <td style="font-weight:bold;color:#166534;">{client_name} (Client)</td></tr>
          <tr><td style="color:#666;padding:6px 0;">Signed at:</td>
              <td style="font-weight:bold;color:#166534;">{signed_at}</td></tr>
        </table>
      </div>

      <div style="text-align:center;margin:30px 0;">
        <a href="{itp_url}"
           style="background:#2563eb;color:white;padding:16px 32px;border-radius:8px;
                  text-decoration:none;font-weight:bold;font-size:16px;display:inline-block;">
          View Signed ITP
        </a>
      </div>
    </div>

    <!-- Footer -->
    <div style="background:#f8f8f8;padding:15px;text-align:center;border-top:1px solid #eee;">
      <p style="color:#aaa;font-size:12px;margin:0;">
        King Rocks Wind Farm QA Manager &middot; Automated Notification
      </p>
    </div>
  </div>
</div>
"""
    sent = 0
    for user in notify_users:
        if send_email(user.email, subject, html):
            sent += 1
    return sent
