from __future__ import annotations

from pathlib import Path
from typing import Optional


def extract_pdf_markdown(file_bytes: bytes) -> str:
    """
    Extract text/markdown from PDF bytes using Docling.
    Kept standalone so it can be imported without FastAPI/auth dependencies.
    """
    from docling.document_converter import DocumentConverter
    import tempfile

    if not file_bytes or not file_bytes.startswith(b"%PDF"):
        raise ValueError("Not a valid PDF")

    converter = DocumentConverter()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(file_bytes)
        tmp.flush()
        tmp_path = Path(tmp.name)
    try:
        result = converter.convert(str(tmp_path))
        doc = result.document
        md = None
        if hasattr(doc, "export_to_markdown"):
            md = doc.export_to_markdown()
        if not md or not str(md).strip():
            md = getattr(doc, "text", "") or ""
        return str(md)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)  # py3.11+
        except Exception:
            pass


def extract_pdf_text_pymupdf(file_bytes: bytes) -> str:
    """
    Extract plain text from PDF using PyMuPDF (fitz).
    This is often more stable for “digital text” PDFs than OCR-based pipelines.
    """
    import fitz  # PyMuPDF
    from io import BytesIO

    doc = fitz.open(stream=BytesIO(file_bytes), filetype="pdf")
    try:
        parts: list[str] = []
        for i, page in enumerate(doc):
            t = page.get_text("text")
            if t and t.strip():
                parts.append(t)
        return "\n".join(parts)
    finally:
        doc.close()


def parse_uploaded_lab_lines(text: str) -> list[dict]:
    """
    Best-effort parser for lab report text.
    Extracts: description, value, units, normal_range, group_name.

    Notes:
    - Many PDFs (including Thyrocare) do not expose a stable test_code in the visible text.
      We intentionally leave test_code as None; the frontend matching falls back to name/description.
    """
    import re

    if not text:
        return []

    lines = text.splitlines()
    current_group: Optional[str] = None
    in_table = False
    out: list[dict] = []

    group_re = re.compile(r"^(?:#+\s*)?(?:DEPARTMENT OF|DEPARTMENT|PROFILE|PANEL)\s*[:\-]?\s*(.+)$", re.I)
    caps_group_re = re.compile(r"^[A-Z][A-Z\s&()\-]{2,60}$")
    # Lines that look like group headings but are actually technologies/methods.
    _IGNORE_CAPS_GROUP = {
        "PHOTOMETRY",
        "CALCULATED",
        "IMMUNOTURBIDIMETRY",
        "E.C.L.I.A",
        "ECLIA",
        "ICP-MS",
        "H.P.L.C",
        "HPLC",
        "FLOW CYTOMETRY",
        "UNITS",
        "VALUE",
        "TECHNOLOGY",
        "BIO. REF. INTERVAL",
        "BIO. REF. INTERVAL.",
    }

    # Row formats can be either single-line or columnar (each cell on its own line).
    # We'll parse columnar first, then fall back to single-line regex.
    header_tokens = {
        "TEST NAME",
        "TECHNOLOGY",
        "VALUE",
        "UNITS",
        "BIO. REF. INTERVAL",
        "BIO. REF. INTERVAL.",
        "METHODOLOGY",
        "METHOD",
    }

    range_re = re.compile(r"^(?:<|<=|>|>=)?\s*[\d.]+(?:\s*[-–—]\s*[\d.]+)?\s*$")
    unit_re = re.compile(r"^[A-Za-zμµ/%0-9 .^×x\-]+$")
    value_re = re.compile(r"^[-+]?\d+(?:\.\d+)?\s*$")
    tech_re = re.compile(r"^[A-Za-z0-9 .()/\-]{2,}$")
    name_re = re.compile(r"^[A-Za-z][A-Za-z0-9 \-()/.,]{1,}$")
    bad_name_tokens = {"male", "female", "adults", "adult", "children", "deficiency", "insufficiency", "sufficiency", "toxicity"}
    tech_stop = {"photometry", "calculated", "immunoturbidimetry", "e.c.l.i.a", "icp-ms", "h.p.l.c", "hplc"}

    def _is_range(s: str) -> bool:
        return bool(range_re.match(s))

    def _is_unit(s: str) -> bool:
        if not unit_re.match(s):
            return False
        t = s.strip()
        # Common unit markers
        if any(x in t for x in ("/", "^", "%", "μ")):
            return True
        # Common standalone units/labels used in reports
        if t.lower() in {"ratio", "fl", "pg", "u/l", "iu/ml", "ng/ml", "pg/ml", "mg/dl", "mg/l", "mmol/l", "gm/dl", "g/dl"}:
            return True
        return False

    def _is_value(s: str) -> bool:
        return bool(value_re.match(s))

    def _is_tech(s: str) -> bool:
        return bool(tech_re.match(s)) and not _is_unit(s) and not _is_range(s) and not _is_value(s)

    def _is_name(s: str) -> bool:
        if not (bool(name_re.match(s)) and not _is_unit(s) and not _is_range(s) and not _is_value(s)):
            return False
        if s.strip().lower() in bad_name_tokens:
            return False
        return True

    # Single-line regex fallbacks
    p_units_value_tech_name = re.compile(
        r"^(?P<unit>[A-Za-zμµ/%0-9 .^×x\-]+)\s+(?P<value>[-+]?\d+(?:\.\d+)?)\s+(?P<tech>[A-Za-z0-9 .()/\-]+)\s+(?P<name>[A-Za-z][A-Za-z0-9 \-()/.,]{2,})(?:\s+.*)?$"
    )
    p_range_units_value_tech_name = re.compile(
        r"^(?P<range>(?:<|<=|>|>=)?\s*[\d.]+(?:\s*[-–—]\s*[\d.]+)?)\s+(?P<unit>[A-Za-zμµ/%0-9 .^×x\-]+)\s+(?P<value>[-+]?\d+(?:\.\d+)?)\s+(?P<tech>[A-Za-z0-9 .()/\-]+)\s+(?P<name>[A-Za-z][A-Za-z0-9 \-()/.,]{2,})(?:\s+.*)?$"
    )
    p_units_value_range_name = re.compile(
        r"^(?P<unit>[A-Za-zμµ/%0-9 .^×x\-]+)\s+(?P<value>[-+]?\d+(?:\.\d+)?)\s+(?P<range>[\d.]+\s*[-–—]\s*[\d.]+)\s+(?P<name>[A-Za-z][A-Za-z0-9 \-()/.,]{2,})(?:\s+.*)?$"
    )
    p_value_name_range_unit = re.compile(
        r"^(?P<value>[-+]?\d+(?:\.\d+)?)\s+(?P<name>[A-Za-z][A-Za-z0-9 \-()/.,]{2,}?)\s+(?P<range>[\d.]+\s*[-–—]\s*[\d.]+)\s+(?P<unit>[A-Za-zμµ/%0-9 .^×x\-]+)(?:\s+.*)?$"
    )
    p_value_name_range = re.compile(
        r"^(?P<value>[-+]?\d+(?:\.\d+)?)\s+(?P<name>[A-Za-z][A-Za-z0-9 \-()/.,]{2,}?)\s+(?P<range>[\d.]+\s*[-–—]\s*[\d.]+)(?:\s+.*)?$"
    )
    p_units_value_name = re.compile(
        r"^(?P<unit>[A-Za-zμµ/%0-9 .^×x\-]+)\s+(?P<value>[-+]?\d+(?:\.\d+)?)\s+(?P<name>[A-Za-z][A-Za-z0-9 \-()/.,]{2,})(?:\s+.*)?$"
    )

    col_buf: list[str] = []

    for raw in lines:
        s = (raw or "").strip()
        # Normalize common PDF extraction artifacts (replacement char for μ).
        s = s.replace("�", "μ")
        if not s:
            # blank line tends to separate blocks
            in_table = False
            col_buf.clear()
            continue

        upper = s.upper()
        # Table headers may be split across lines in extracted text.
        if "TEST NAME" in upper:
            in_table = True
            col_buf.clear()
            continue
        # Table terminators / non-result sections
        if upper.startswith("PLEASE CORRELATE") or upper.startswith("METHOD") or upper.startswith("CLINICAL SIGNIFICANCE"):
            in_table = False
            col_buf.clear()
            continue

        gm = group_re.match(s)
        if gm:
            current_group = gm.group(1).strip()[:200]
            continue
        if (not in_table) and caps_group_re.match(s) and not any(ch.isdigit() for ch in s):
            if "TEST NAME" in s or "TECHNOLOGY" in s or "BIO." in s or "METHODOLOGY" in s:
                continue
            if s.strip() in _IGNORE_CAPS_GROUP:
                continue
            # Avoid treating address/header lines as groups.
            if any(tok in s for tok in ("ROAD", "NAGAR", "BLOCK", "FLOOR", "BENGALURU", "BANGLORE", "MUMBAI")):
                continue
            current_group = s.title().strip()[:200]
            continue

        if not in_table:
            continue

        # Skip pure header labels that sometimes get split line-by-line.
        if upper in header_tokens:
            continue

        # Columnar parsing (Thyrocare: each cell on its own line).
        col_buf.append(s)
        # Keep buffer bounded
        if len(col_buf) > 6:
            col_buf = col_buf[-6:]

        # Try 5-column: range, unit, value, tech, name
        if len(col_buf) >= 5:
            a, b, c, d, e = col_buf[-5:]
            if _is_range(a) and _is_unit(b) and _is_value(c) and _is_tech(d) and _is_name(e):
                out.append(
                    {
                        "test_code": None,
                        "description": " ".join(e.split()).strip(),
                        "test_value": c.strip(),
                        "normal_val": a.strip(),
                        "units": b.strip() or None,
                        "group_name": current_group,
                        "raw_text": " | ".join(col_buf[-5:])[:2000],
                    }
                )
                col_buf.clear()
                continue

        # Try 4-column: unit, value, tech, name
        if len(col_buf) >= 4:
            a, b, c, d = col_buf[-4:]
            # Also seen: unit, value, name, technology (e.g. mg/dL | 81.3 | FASTING BLOOD SUGAR | PHOTOMETRY)
            if _is_unit(a) and _is_value(b) and _is_name(c) and (_is_tech(d) or d.strip().lower() in tech_stop):
                out.append(
                    {
                        "test_code": None,
                        "description": " ".join(c.split()).strip(),
                        "test_value": b.strip(),
                        "normal_val": None,
                        "units": a.strip() or None,
                        "group_name": current_group,
                        "raw_text": " | ".join(col_buf[-4:])[:2000],
                    }
                )
                col_buf.clear()
                continue
            # Alternative: unit, value, technology, name
            if _is_unit(a) and _is_value(b) and _is_tech(c) and _is_name(d):
                out.append(
                    {
                        "test_code": None,
                        "description": " ".join(d.split()).strip(),
                        "test_value": b.strip(),
                        "normal_val": None,
                        "units": a.strip() or None,
                        "group_name": current_group,
                        "raw_text": " | ".join(col_buf[-4:])[:2000],
                    }
                )
                col_buf.clear()
                continue
            # Summary style: value, name, range, unit
            if _is_value(a) and _is_name(b) and _is_range(c) and _is_unit(d):
                out.append(
                    {
                        "test_code": None,
                        "description": " ".join(b.split()).strip(),
                        "test_value": a.strip(),
                        "normal_val": c.strip(),
                        "units": d.strip() or None,
                        "group_name": current_group,
                        "raw_text": " | ".join(col_buf[-4:])[:2000],
                    }
                )
                col_buf.clear()
                continue

        m = p_range_units_value_tech_name.match(s)
        if m:
            out.append(
                {
                    "test_code": None,
                    "description": " ".join(m.group("name").split()).strip(),
                    "test_value": m.group("value").strip(),
                    "normal_val": m.group("range").strip(),
                    "units": (m.group("unit") or "").strip() or None,
                    "group_name": current_group,
                    "raw_text": s[:2000],
                }
            )
            continue

        m = p_units_value_tech_name.match(s)
        if m:
            out.append(
                {
                    "test_code": None,
                    "description": " ".join(m.group("name").split()).strip(),
                    "test_value": m.group("value").strip(),
                    "normal_val": None,
                    "units": (m.group("unit") or "").strip() or None,
                    "group_name": current_group,
                    "raw_text": s[:2000],
                }
            )
            continue

        m = p_units_value_range_name.match(s)
        if m:
            out.append(
                {
                    "test_code": None,
                    "description": " ".join(m.group("name").split()).strip(),
                    "test_value": m.group("value").strip(),
                    "normal_val": m.group("range").strip(),
                    "units": (m.group("unit") or "").strip() or None,
                    "group_name": current_group,
                    "raw_text": s[:2000],
                }
            )
            continue

        m = p_value_name_range_unit.match(s)
        if m:
            out.append(
                {
                    "test_code": None,
                    "description": " ".join(m.group("name").split()).strip(),
                    "test_value": m.group("value").strip(),
                    "normal_val": m.group("range").strip(),
                    "units": (m.group("unit") or "").strip() or None,
                    "group_name": current_group,
                    "raw_text": s[:2000],
                }
            )
            continue

        m = p_value_name_range.match(s)
        if m:
            out.append(
                {
                    "test_code": None,
                    "description": " ".join(m.group("name").split()).strip(),
                    "test_value": m.group("value").strip(),
                    "normal_val": m.group("range").strip(),
                    "units": None,
                    "group_name": current_group,
                    "raw_text": s[:2000],
                }
            )
            continue

        m = p_units_value_name.match(s)
        if m:
            # Avoid capturing guideline lines like "70 to 100 mg/dl" where the "name" is actually a unit.
            if _is_unit(m.group("name")):
                continue
            out.append(
                {
                    "test_code": None,
                    "description": " ".join(m.group("name").split()).strip(),
                    "test_value": m.group("value").strip(),
                    "normal_val": None,
                    "units": (m.group("unit") or "").strip() or None,
                    "group_name": current_group,
                    "raw_text": s[:2000],
                }
            )
            continue

    return out


def extract_and_parse_pdf(file_bytes: bytes) -> list[dict]:
    """
    Extract + parse lab lines from PDF bytes.
    Tries PyMuPDF text extraction first (fast for digital PDFs);
    falls back to Docling if needed.
    """
    # 1) Fast path: plain text via PyMuPDF
    try:
        txt = extract_pdf_text_pymupdf(file_bytes)
        rows = parse_uploaded_lab_lines(txt)
        if len(rows) >= 10:
            return rows
    except Exception:
        rows = []

    # 2) Fallback: Docling (can invoke OCR; slower)
    try:
        md = extract_pdf_markdown(file_bytes)
        rows2 = parse_uploaded_lab_lines(md)
        if len(rows2) > len(rows):
            rows = rows2
    except Exception:
        pass

    return rows

