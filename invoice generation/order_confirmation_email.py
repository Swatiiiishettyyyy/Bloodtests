import sys
import os
from pathlib import Path

_TEMPLATE_PATH = Path(__file__).parent.parent / "Email_template" / "order_confirmation_template.html"
_HTML_TEMPLATE: str = _TEMPLATE_PATH.read_text(encoding="utf-8")


def _build_products_text(items: list) -> str:
    names = []
    for item in items:
        name = item.get("name") if isinstance(item, dict) else str(item)
        if name:
            names.append(name)
    if not names:
        return "your test(s)"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " & " + names[-1]


def send_order_confirmation_email(
    to: str,
    customer_name: str,
    items: list,
    service_account_file: str,
    sender_email: str = "billing@nucleotide.life",
    gif_url: str = "",
    order_number: str = "",
) -> dict:
    invoice_gen_path = str(Path(__file__).parent)
    if invoice_gen_path not in sys.path:
        sys.path.insert(0, invoice_gen_path)

    from nucleotide_invoice_sender_wo_file import InvoiceSender

    html = (
        _HTML_TEMPLATE
        .replace("{customer_name}", customer_name or "Valued Customer")
        .replace("{products_text}", _build_products_text(items))
        .replace("{gif_url}", gif_url or "")
        .replace("{order_id}", order_number or "")
    )

    order_line = f"Order ID: {order_number}\n\n" if order_number else ""
    plain = (
        f"Hi {customer_name or 'Valued Customer'},\n\n"
        f"Your order for {_build_products_text(items)} has been received.\n\n"
        f"{order_line}"
        "A phlebotomist will call you within 24-48 hours to schedule your home visit.\n\n"
        "Haven't heard from us within 48 hours? Email info@nucleotide.life\n\n"
        "Warm regards,\nThe Nucleotide Healthcare Team\nwww.nucleotide.life"
    )

    sender = InvoiceSender(service_account_file, sender_email)
    return sender.send_invoice(
        to=to,
        subject="Your Nucleotide Sample Collection is Scheduled",
        body=plain,
        html_body=html,
    )
