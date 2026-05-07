import sys
from pathlib import Path

_TEMPLATE_PATH = Path(__file__).parent.parent / "Email_template" / "report_ready_email_template.html"
_HTML_TEMPLATE: str = _TEMPLATE_PATH.read_text(encoding="utf-8")


def send_report_ready_email(
    to: str,
    name: str,
    service_account_file: str,
    sender_email: str = "info@nucleotide.life",
) -> dict:
    invoice_gen_path = str(Path(__file__).parent)
    if invoice_gen_path not in sys.path:
        sys.path.insert(0, invoice_gen_path)

    from nucleotide_invoice_sender_wo_file import InvoiceSender

    display_name = name or "there"
    html = _HTML_TEMPLATE.replace("{name}", display_name)

    plain = (
        f"Hi {display_name},\n\n"
        "Great news — your Nucleotide blood test report is ready.\n\n"
        "Your results are now available. Log in to your account to view your personalized "
        "health report, understand your biomarkers, and explore recommendations tailored "
        "to your biology.\n\n"
        "View your report here: https://www.nucleotide.life/blood-test\n\n"
        "If you have any questions about your results, feel free to reach out at info@nucleotide.life\n\n"
        "Warm regards,\n"
        "The Nucleotide Healthcare Pvt Ltd Team\n"
        "www.nucleotide.life"
    )

    sender = InvoiceSender(service_account_file, sender_email)
    return sender.send_invoice(
        to=to,
        subject="Your Nucleotide Report is Ready",
        body=plain,
        html_body=html,
    )
