import sys
from pathlib import Path

_TEMPLATE_PATH = Path(__file__).parent.parent / "Email_template" / "report_ready_template.html"
_HTML_TEMPLATE: str = _TEMPLATE_PATH.read_text(encoding="utf-8")


def send_report_ready_email(
    to: str,
    name: str,
    product: str,
    service_account_file: str,
    sender_email: str = "info@nucleotide.life",
) -> dict:
    invoice_gen_path = str(Path(__file__).parent)
    if invoice_gen_path not in sys.path:
        sys.path.insert(0, invoice_gen_path)

    from nucleotide_invoice_sender_wo_file import InvoiceSender

    display_name = name or "there"
    display_product = product or "your test"

    html = _HTML_TEMPLATE.replace("{name}", display_name).replace("{product}", display_product)

    plain = (
        f"Hi {display_name},\n\n"
        f"Your {display_product} results are in.\n\n"
        "Head over to the My Reports section on nucleotide.life to view a detailed breakdown "
        "of your key health markers and what they mean for you.\n\n"
        "View your report: https://www.nucleotide.life\n\n"
        "If you have any questions, feel free to reach out at info@nucleotide.life\n\n"
        "Warm regards,\n"
        "The Nucleotide Healthcare Pvt Ltd Team\n"
        "www.nucleotide.life"
    )

    sender = InvoiceSender(service_account_file, sender_email)
    return sender.send_invoice(
        to=to,
        subject="Your Blood Test Report is Ready – Nucleotide",
        body=plain,
        html_body=html,
    )
