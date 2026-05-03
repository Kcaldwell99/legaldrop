import os
import logging
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

logger       = logging.getLogger(__name__)
SG_KEY       = os.environ.get("SENDGRID_API_KEY", "")
FROM_EMAIL   = os.environ.get("SENDGRID_FROM_EMAIL", "noreply@legaldrop.com")
FROM_NAME    = os.environ.get("SENDGRID_FROM_NAME", "LegalDrop")
BASE_URL     = os.environ.get("BASE_URL", "http://localhost:8000")


def _send(to_email: str, subject: str, html: str):
    if not SG_KEY:
        logger.warning("SENDGRID_API_KEY not set — email not sent to %s", to_email)
        return
    msg = Mail(
        from_email=(FROM_EMAIL, FROM_NAME),
        to_emails=to_email,
        subject=subject,
        html_content=html,
    )
    try:
        sg = SendGridAPIClient(SG_KEY)
        sg.send(msg)
        logger.info("Email sent: %s → %s", subject, to_email)
    except Exception as e:
        logger.error("SendGrid error: %s", e)


def _base(body: str) -> str:
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;
                background:#0f1012;color:#e8e9ec;padding:32px;border-radius:12px">
      <div style="margin-bottom:24px">
        <span style="font-size:22px;font-weight:800;color:#3b82f6;
                     letter-spacing:-0.02em">Legal<span style="color:#e8e9ec">Drop</span></span>
      </div>
      {body}
      <div style="margin-top:32px;padding-top:16px;border-top:1px solid #2e2f35;
                  font-size:12px;color:#878a95">
        LegalDrop — Certified Legal Document Delivery<br/>
        Evidence verification powered by
        <a href="https://evidenceanalyzer.com" style="color:#8b5cf6">Evidentix™</a>
      </div>
    </div>
    """


def recipient_delivery_link(
    *,
    to_email: str,
    recipient_name: str,
    sender_name: str,
    firm_name: str,
    subject: str,
    message: str,
    access_url: str,
    expires_hours: int,
    filename: str,
):
    body = f"""
    <h2 style="color:#e8e9ec;font-size:20px">You have received a legal document</h2>
    <p style="color:#878a95">
      <strong style="color:#e8e9ec">{sender_name}</strong>
      {f'at <strong style="color:#e8e9ec">{firm_name}</strong>' if firm_name else ''}
      has sent you a certified legal document via LegalDrop.
    </p>
    <div style="background:#18191d;border:1px solid #2e2f35;border-radius:8px;padding:16px;margin:20px 0">
      <p style="margin:0 0 4px;font-size:12px;color:#878a95;text-transform:uppercase;
                letter-spacing:0.05em">Subject</p>
      <p style="margin:0;font-weight:600">{subject}</p>
      {f'<p style="margin:12px 0 0;color:#878a95;font-size:14px">{message}</p>' if message else ''}
    </div>
    <p style="color:#878a95;font-size:14px">📎 <strong style="color:#e8e9ec">{filename}</strong></p>
    <div style="margin:24px 0">
      <a href="{access_url}"
         style="display:inline-block;padding:14px 28px;background:#3b82f6;
                color:#fff;text-decoration:none;border-radius:8px;font-weight:700;font-size:16px">
        Access Document →
      </a>
    </div>
    <p style="color:#878a95;font-size:13px">
      🔒 This link expires in <strong style="color:#e8e9ec">{expires_hours} hours</strong>.
      Your access will be logged and certified by Evidentix.
    </p>
    """
    _send(to_email, f"Legal Document from {sender_name}: {subject}", _base(body))


def recipient_account_invite(
    *,
    to_email: str,
    recipient_name: str,
    sender_name: str,
    firm_name: str,
    subject: str,
    register_url: str,
    filename: str,
):
    body = f"""
    <h2 style="color:#e8e9ec;font-size:20px">Legal document waiting for you</h2>
    <p style="color:#878a95">
      <strong style="color:#e8e9ec">{sender_name}</strong>
      {f'at <strong style="color:#e8e9ec">{firm_name}</strong>' if firm_name else ''}
      has sent you a certified legal document. You must create a free LegalDrop account to access it.
    </p>
    <div style="background:#18191d;border:1px solid #2e2f35;border-radius:8px;padding:16px;margin:20px 0">
      <p style="margin:0 0 4px;font-size:12px;color:#878a95;text-transform:uppercase">Subject</p>
      <p style="margin:0;font-weight:600">{subject}</p>
    </div>
    <div style="margin:24px 0">
      <a href="{register_url}"
         style="display:inline-block;padding:14px 28px;background:#3b82f6;
                color:#fff;text-decoration:none;border-radius:8px;font-weight:700;font-size:16px">
        Create Account & Access Document →
      </a>
    </div>
    """
    _send(to_email, f"Legal Document from {sender_name} — Account Required", _base(body))


def sender_delivery_confirmed(
    *,
    to_email: str,
    sender_name: str,
    recipient_email: str,
    subject: str,
    filename: str,
    cert_url: str,
    sha256: str,
    delivery_url: str,
    tier: str,
):
    tier_labels = {"basic": "Basic Delivery", "certified": "Certified Delivery", "custody": "Custody Package"}
    body = f"""
    <h2 style="color:#e8e9ec;font-size:20px">✅ Document delivered successfully</h2>
    <p style="color:#878a95">
      Your document has been sent to
      <strong style="color:#e8e9ec">{recipient_email}</strong> and certified by Evidentix.
    </p>
    <div style="background:#18191d;border:1px solid #2e2f35;border-radius:8px;padding:16px;margin:20px 0">
      <table style="width:100%;font-size:14px">
        <tr><td style="color:#878a95;padding:4px 0">Subject</td><td style="text-align:right">{subject}</td></tr>
        <tr><td style="color:#878a95;padding:4px 0">File</td><td style="text-align:right">{filename}</td></tr>
        <tr><td style="color:#878a95;padding:4px 0">Tier</td><td style="text-align:right">{tier_labels.get(tier, tier)}</td></tr>
        <tr><td style="color:#878a95;padding:4px 0">SHA-256</td>
            <td style="color:#a78bfa;text-align:right;font-family:monospace;font-size:11px">{sha256[:32]}…</td></tr>
      </table>
    </div>
    <a href="{cert_url}" target="_blank"
       style="display:inline-block;padding:10px 20px;background:#8b5cf6;color:#fff;
              text-decoration:none;border-radius:8px;font-weight:600;font-size:14px;margin-right:12px">
      🔒 View Integrity Certificate
    </a>
    <a href="{delivery_url}"
       style="display:inline-block;padding:10px 20px;background:#18191d;border:1px solid #2e2f35;
              color:#e8e9ec;text-decoration:none;border-radius:8px;font-weight:600;font-size:14px">
      View Delivery →
    </a>
    """
    _send(to_email, f"Document Delivered: {subject}", _base(body))


def sender_receipt_confirmed(
    *,
    to_email: str,
    recipient_email: str,
    subject: str,
    opened_at: str,
    acknowledged_at: str,
    delivery_url: str,
    custody_record_url: str = None,
):
    custody_section = ""
    if custody_record_url:
        custody_section = f"""
        <a href="{custody_record_url}" target="_blank"
           style="display:inline-block;padding:10px 20px;background:#8b5cf6;color:#fff;
                  text-decoration:none;border-radius:8px;font-weight:600;font-size:14px;margin-bottom:12px">
          📄 Download Custody Record PDF
        </a>
        """
    body = f"""
    <h2 style="color:#e8e9ec;font-size:20px">✅ Document opened and acknowledged</h2>
    <p style="color:#878a95">
      <strong style="color:#e8e9ec">{recipient_email}</strong>
      has opened and acknowledged receipt. This has been logged in the Evidentix chain-of-custody record.
    </p>
    <div style="background:#18191d;border:1px solid #2e2f35;border-radius:8px;padding:16px;margin:20px 0">
      <table style="width:100%;font-size:14px">
        <tr><td style="color:#878a95;padding:4px 0">Subject</td><td style="text-align:right">{subject}</td></tr>
        <tr><td style="color:#878a95;padding:4px 0">Opened</td><td style="color:#22c55e;text-align:right">{opened_at}</td></tr>
        <tr><td style="color:#878a95;padding:4px 0">Acknowledged</td><td style="color:#22c55e;text-align:right">{acknowledged_at}</td></tr>
      </table>
    </div>
    {custody_section}
    <a href="{delivery_url}"
       style="display:inline-block;padding:10px 20px;background:#18191d;border:1px solid #2e2f35;
              color:#e8e9ec;text-decoration:none;border-radius:8px;font-weight:600;font-size:14px">
      View Delivery & Certificate →
    </a>
    """
    _send(to_email, f"Receipt Confirmed: {subject}", _base(body))