"""
Nucleotide Invoice Sender — Zero-Disk Integration
==================================================
Generates an invoice PDF in memory and attaches it to a Gmail
message without ever writing to disk.

This module patches the existing InvoiceSender with two additions:
  1. generate_invoice_bytes() — returns PDF as raw bytes (BytesIO)
  2. send_invoice() now accepts `pdf_bytes` as an alternative to `pdf_path`

Usage:
    sender = InvoiceSender(service_account_file="...", sender_email="...")
    
    pdf_bytes = generate_invoice_bytes(invoice_data, logo_path="logo.png")
    
    sender.send_invoice(
        to="customer@example.com",
        subject="Invoice #042",
        body="Please find your invoice attached.",
        pdf_bytes=pdf_bytes,
        pdf_filename="INV-042.pdf",
    )
"""

import io
import base64
import email.message
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

# Import the invoice generator from your existing script
from nucleotide_invoice import generate_invoice as _generate_invoice_to_file, fmt_currency

# We also need the internals to build a BytesIO version
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate


SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


# ─────────────────────────────────────────────────────────────
# IN-MEMORY PDF GENERATION
# ─────────────────────────────────────────────────────────────
def generate_invoice_bytes(data: dict, logo_path: str = None) -> bytes:
    """
    Generate a Nucleotide invoice PDF entirely in memory.

    Returns raw PDF bytes — no file is written to disk.

    Parameters are identical to nucleotide_invoice.generate_invoice().
    """
    buffer = io.BytesIO()
    _generate_invoice_to_file(data, output_path=buffer, logo_path=logo_path)
    buffer.seek(0)
    return buffer.read()


# ─────────────────────────────────────────────────────────────
# ENHANCED INVOICE SENDER
# ─────────────────────────────────────────────────────────────
class InvoiceSender:
    """Sends invoice emails from billing@nucleotide.life via Gmail API.
    
    Enhanced to accept in-memory PDF bytes alongside file paths.
    """

    def __init__(
        self,
        service_account_file: str,
        sender_email: str = "billing@nucleotide.life",
    ):
        self.sender_email = sender_email

        credentials = service_account.Credentials.from_service_account_file(
            service_account_file, scopes=SCOPES
        )
        delegated_credentials = credentials.with_subject(sender_email)
        self.service = build("gmail", "v1", credentials=delegated_credentials)

    def send_invoice(
        self,
        to: str,
        subject: str,
        body: str,
        pdf_path: str | None = None,
        pdf_bytes: bytes | None = None,
        pdf_filename: str = "invoice.pdf",
        cc: str | None = None,
        bcc: str | None = None,
        html_body: str | None = None,
    ) -> dict:
        """
        Send an invoice email with a PDF attachment.

        The PDF can come from either:
          - pdf_path:  a file on disk (your existing flow), OR
          - pdf_bytes: raw bytes in memory (zero-disk flow)
        
        If both are provided, pdf_bytes takes priority.
        """
        message = self._create_message(
            to, subject, body,
            pdf_path=pdf_path,
            pdf_bytes=pdf_bytes,
            pdf_filename=pdf_filename,
            cc=cc, bcc=bcc, html_body=html_body,
        )
        result = (
            self.service.users()
            .messages()
            .send(userId="me", body=message)
            .execute()
        )
        print(f"✓ Invoice sent to {to} — Message ID: {result['id']}")
        return result

    # ── Internal helpers ──────────────────────────────────────

    def _create_message(
        self, to, subject, body,
        pdf_path=None, pdf_bytes=None, pdf_filename="invoice.pdf",
        cc=None, bcc=None, html_body=None,
    ) -> dict:
        msg = email.message.EmailMessage()
        msg["From"] = self.sender_email
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc

        if html_body:
            msg.set_content(body)
            msg.add_alternative(html_body, subtype="html")
        else:
            msg.set_content(body)

        if pdf_bytes:
            msg.add_attachment(
                pdf_bytes, maintype="application", subtype="pdf", filename=pdf_filename
            )
        elif pdf_path:
            path = Path(pdf_path)
            if not path.exists():
                raise FileNotFoundError(f"Attachment not found: {pdf_path}")
            with open(path, "rb") as f:
                data = f.read()
            msg.add_attachment(
                data, maintype="application", subtype="pdf", filename=path.name
            )

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return {"raw": raw}


# ─────────────────────────────────────────────────────────────
# EMAIL BODY BUILDER
# ─────────────────────────────────────────────────────────────
def build_email_body(invoice_data: dict) -> tuple[str, str]:
    """
    Build plain-text and HTML email bodies from invoice data.

    Returns (plain_body, html_body).
    """
    customer_name       = invoice_data.get("customer_name", "Valued Customer")
    company_name        = invoice_data.get("company_name", "Nucleotide Healthcare Pvt Ltd")
    customer_care_email = invoice_data.get("customer_care_email", "info@nucleotide.life")
    website             = invoice_data.get("website", "www.nucleotide.life")
    order_number        = invoice_data.get("order_number", "")
    paid_amount         = invoice_data.get("paid_amount", invoice_data.get("grand_total", 0))
    items               = invoice_data.get("items", [])

    names      = [item.get("name", "") for item in items if item.get("name")]
    amount_str = fmt_currency(paid_amount)

    # ── PLAIN TEXT ────────────────────────────────────────────
    plain_body = (
        f"Hi {customer_name},\n\n"
        f"Welcome to Nucleotide — your personalized Digital Health Twin.\n\n"
        f"We're excited to begin building a dynamic health profile that evolves with you—bringing together "
        f"your biology, lifestyle, and future health insights to guide smarter decisions on prevention, "
        f"nutrition, and care. Your twin is designed to make complex health data simple, actionable, and "
        f"truly personal.\n\n"
        f"Your Order Details:\n"
        f"- Order ID: {order_number}\n"
        f"- Amount Paid: {amount_str}\n\n"
        f"Your invoice is attached for your reference.\n\n"
        f"You can explore more about your journey here: {website}\n\n"
        f"If you have any questions, feel free to reach out at {customer_care_email}\n\n"
        f"We're glad to have you with us as you take the first step toward a smarter, more personalized "
        f"health journey.\n\n"
        f"Warm regards,\n"
        f"The {company_name} Team\n"
        f"{website}\n"
    )

    # ── HTML ──────────────────────────────────────────────────
    table_rows_html = "\n".join(
        f'        <tr style="background:{("#ffffff" if i % 2 == 0 else "#f0faf9")};">'
        f'<td style="padding:8px 12px; border: 1px solid #e2e8f0;">{name}</td></tr>'
        for i, name in enumerate(names)
    ) if names else '<tr><td style="padding:8px 12px; border: 1px solid #e2e8f0; color:#a0aec0;">No items</td></tr>'

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; color: #2d3748; max-width: 600px; margin: 0; padding: 24px;">

  <p>Hi {customer_name},</p>

  <p>Welcome to Nucleotide — your personalized Digital Health Twin.</p>

  <p>
    We're excited to begin building a dynamic health profile that evolves with you—bringing together
    your biology, lifestyle, and future health insights to guide smarter decisions on prevention,
    nutrition, and care. Your twin is designed to make complex health data simple, actionable, and
    truly personal.
  </p>

  <p><strong>Your Order Details:</strong></p>
  <ul style="padding-left: 20px; line-height: 1.8;">
      <li>Order ID: {order_number}</li>
      <li>Amount Paid: {amount_str}</li>
  </ul>

  <table style="border-collapse: collapse; width: 100%; margin: 16px 0; border: 1px solid #e2e8f0;">
    <thead>
      <tr style="background: #1A9E8F;">
        <th style="padding: 10px 12px; text-align: left; color: #ffffff; font-size: 13px; letter-spacing: 0.5px; border: 1px solid #e2e8f0;">
          Product Name
        </th>
      </tr>
    </thead>
    <tbody>
{table_rows_html}
    </tbody>
  </table>

  <p>Your invoice is attached for your reference.</p>

  <p>
    You can explore more about your journey here:
    <a href="https://{website}" style="color: #1A9E8F;">{website}</a>
  </p>

  <p>
    If you have any questions, feel free to reach out at
    <a href="mailto:{customer_care_email}" style="color: #1A9E8F;">{customer_care_email}</a>
  </p>

  <p>
    We're glad to have you with us as you take the first step toward a smarter, more personalized
    health journey.
  </p>

  <p>
    Warm regards,<br>
    <strong>The {company_name} Team</strong><br>
    <a href="https://{website}" style="color: #1A9E8F;">{website}</a>
  </p>

</body>
</html>"""

    return plain_body, html_body


# ─────────────────────────────────────────────────────────────
# CONVENIENCE: GENERATE + SEND IN ONE CALL
# ─────────────────────────────────────────────────────────────
def generate_and_send_invoice(
    invoice_data: dict,
    logo_path: str,
    to: str,
    subject: str,
    body: str,
    pdf_filename: str = "invoice.pdf",
    service_account_file: str = "nucleotide-billing-489209-4459130f2a39.json",
    sender_email: str = "billing@nucleotide.life",
    cc: str | None = None,
    bcc: str | None = None,
    html_body: str | None = None,
) -> dict:
    """
    One-shot: generate invoice PDF in memory and email it immediately.

    No file is written to disk at any point.

    Returns the Gmail API response dict.
    """
    # Step 1: Generate PDF bytes in memory
    pdf_bytes = generate_invoice_bytes(invoice_data, logo_path=logo_path)

    # Step 2: Send email with bytes attached
    sender = InvoiceSender(
        service_account_file=service_account_file,
        sender_email=sender_email,
    )
    return sender.send_invoice(
        to=to,
        subject=subject,
        body=body,
        pdf_bytes=pdf_bytes,
        pdf_filename=pdf_filename,
        cc=cc,
        bcc=bcc,
        html_body=html_body,
    )


_invoice_data = {
                    # ── Header & Meta ──
                    "invoice_number": "NUC-2024-0045",
                    "invoice_date": "March 16, 2026",
                    "order_number": "ORD-10563014629",
                    "sac_code": "999312",

                    # ── Company Details (Bill From) ──
                    "company_name": "Nucleotide Healthcare Pvt Ltd",
                    "company_address": "Bangalore, Karnataka, India",
                    "gst_number": None,

                    # ── Customer Details ──
                    "customer_name": "Fazil Ahamed",
                    "customer_address": "Plot No.50, VOC St 1st Cross, Periyakalapet, Puducherry 605014",

                    # ── Description (optional free text) ──
                    "description": [],

                    # ── Product-level items (simple mode) ──
                    "items": [
                        {"name": "Genetic One - Single", "amount": 45000},
                        {"name": "Genetic One - Couple", "amount": 89000}
                    ],

                    # ── Detailed per-person test items ──
                    "detailed_items": [],

                    # ── Totals ──
                    "total_amount": 134000,
                    "discount_coupon": {
                        "code": "CC2500",
                        "amount": 2500,
                    },
                    "grand_total": 131500,
                    "paid_amount": 131500,

                    # ── Payment Info ──
                    "payment_info": [
                        {"mode": "Razorpay", "reference": "pay_NxYzAbCdEfGh", "amount": 131500},
                    ],

                    # ── Subtle / Legal ──
                    "pan_number": "AADCE5479M",

                    # ── Footer ──
                    "customer_care_phone": "+91 9403891587",
                    "customer_care_email": "info@nucleotide.life",
                    "website": "www.nucleotide.life"
                }

if __name__ == "__main__":
    _plain_body, _html_body = build_email_body(_invoice_data)

    generate_and_send_invoice(
        service_account_file=str(Path(__file__).parent / "billing.json"),
        to="fazilahamed@gmail.com",
        subject=f"Order Confirmation & Invoice – {_invoice_data['order_number']}",
        body=_plain_body,
        html_body=_html_body,
        logo_path=str(Path(__file__).parent / "logo.png"),
        pdf_filename=f"{_invoice_data['order_number']}.pdf",
        #cc=["shettyswati711@gmail.com","nucleotide.life@gmail.com"],
        bcc=["chetan@nucleotide.life", "darshan.s@nucleotide.life"],
        invoice_data=_invoice_data,
    )
