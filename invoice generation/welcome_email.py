import sys
from pathlib import Path

_TEMPLATE_PATH = Path(__file__).parent.parent / "Email_template" / "welcome_email_template.html"
_HTML_TEMPLATE: str = _TEMPLATE_PATH.read_text(encoding="utf-8")


def send_welcome_email(
    to: str,
    name: str,
    service_account_file: str,
    sender_email: str = "billing@nucleotide.life",
) -> dict:
    invoice_gen_path = str(Path(__file__).parent)
    if invoice_gen_path not in sys.path:
        sys.path.insert(0, invoice_gen_path)

    from nucleotide_invoice_sender_wo_file import InvoiceSender

    display_name = name or "there"
    html = _HTML_TEMPLATE.replace("{name}", display_name)

    plain = (
        f"Hi {display_name},\n\n"
        "Welcome to Nucleotide — your personalized Digital Health Twin.\n\n"
        "We're excited to begin building a dynamic health profile that evolves with you—"
        "bringing together your biology, lifestyle, and future health insights to guide "
        "smarter decisions on prevention, nutrition, and care.\n\n"
        "Explore our genetic tests here: https://www.nucleotide.life\n\n"
        "If you have any questions, feel free to reach out at info@nucleotide.life\n\n"
        "Warm regards,\n"
        "The Nucleotide Healthcare Pvt Ltd Team\n"
        "www.nucleotide.life"
    )

    sender = InvoiceSender(service_account_file, sender_email)
    return sender.send_invoice(
        to=to,
        subject="Welcome to Nucleotide – Start Building Your Digital Health Twin",
        body=plain,
        html_body=html,
    )
