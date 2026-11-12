"""Turn a messy uploaded file into clean rows.

Three input types, three parsers:
  - bank CSV      -> fuzzy column matching (every bank names columns differently)
  - bank PDF      -> text extraction + regex per line
  - order history -> Swiggy/Zomato exports, which give FREQUENCY, not just totals

KNOWN LIMIT (say this openly in an interview): the PDF parser reads text
INSIDE the PDF. A scanned statement is an image — there is no text to read.
That needs OCR (Tesseract), which is not implemented here.
"""

from __future__ import annotations

import io
import re
from datetime import datetime

import pandas as pd

# ---------------------------------------------------------------- merchant


# Noise that Indian bank narrations wrap around the actual merchant name.
_NOISE = (
    "UPI/", "UPI-", "POS/", "POS-", "NEFT/", "IMPS/", "RTGS/", "ACH/",
    "/PAY", "/PAYMENT", "TXN", "REF", "MPS", "ATM",
)


def clean_merchant(description: str) -> str:
    """UPI/SWIGGY BANGALORE/428193847/PAY  ->  SWIGGY BANGALORE

    This is regex and string cleaning — NOT spaCy, NOT NER. Indian bank
    narrations follow predictable patterns once you strip the UPI/POS
    prefixes and reference numbers, so regex was more reliable and far
    faster than NER for this specific format.
    """
    if not description:
        return "UNKNOWN"
    text = description.upper()
    for noise in _NOISE:
        text = text.replace(noise, " ")
    text = re.sub(r"\b\d{6,}\b", " ", text)        # long reference numbers
    text = re.sub(r"[^A-Z0-9 &.-]", " ", text)     # odd symbols
    return " ".join(text.split()[:4]) or "UNKNOWN"


# ---------------------------------------------------------------- dates

_DATE_FORMATS = (
    "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y",
    "%d-%b-%Y", "%d %b %Y", "%m/%d/%Y",
)


def parse_date(value) -> datetime | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return pd.to_datetime(text, dayfirst=True).to_pydatetime()
    except Exception:
        return None


def _to_float(value) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    text = str(value).replace(",", "").replace("₹", "").replace("INR", "").strip()
    if not text or text in {"-", "--"}:
        return 0.0
    try:
        return abs(float(text))
    except ValueError:
        return 0.0


# ---------------------------------------------------------------- CSV


def parse_bank_csv(content: bytes) -> list[dict]:
    """Fuzzy column matching.

    Banks call the same column "Narration", "Description", "Particulars",
    "Remarks". Hard-coding names breaks on the next bank. So we search for
    a SUBSTRING: "Withdrawal Amt" contains "withdrawal" -> matched.
    """
    df = pd.read_csv(io.BytesIO(content))
    cols = {str(c).strip().lower(): c for c in df.columns}

    def find(*candidates: str):
        for cand in candidates:
            for low, original in cols.items():
                if cand in low:
                    return original
        return None

    date_col = find("date", "txn date", "value date")
    desc_col = find("narration", "description", "particulars", "remarks", "details")
    debit_col = find("withdrawal", "debit")
    credit_col = find("deposit", "credit")
    amount_col = find("amount")

    if date_col is None or desc_col is None:
        raise ValueError(
            "Could not find a date and a description column in this CSV. "
            f"Columns seen: {list(df.columns)}"
        )

    rows: list[dict] = []
    for _, row in df.iterrows():
        when = parse_date(row.get(date_col))
        if when is None:
            continue

        debit = _to_float(row.get(debit_col)) if debit_col else 0.0
        credit = _to_float(row.get(credit_col)) if credit_col else 0.0

        if debit == 0.0 and credit == 0.0 and amount_col:
            # Single-amount format: sign (or a Dr/Cr column) carries direction.
            raw = str(row.get(amount_col, "")).strip()
            value = _to_float(raw)
            if raw.startswith("-") or "DR" in raw.upper():
                debit = value
            else:
                credit = value

        if debit == 0.0 and credit == 0.0:
            continue

        description = str(row.get(desc_col, "")).strip()
        rows.append(
            {
                "date": when,
                "amount": debit or credit,
                "txn_type": "debit" if debit else "credit",
                "merchant": clean_merchant(description),
                "raw_description": description,
                "source": "bank",
            }
        )
    return rows


# ---------------------------------------------------------------- PDF

# A regex is a pattern for finding text SHAPES.
# (?P<date>...) names a captured piece so we can read it back by name.
PDF_LINE = re.compile(
    r"(?P<date>\d{2}[/-]\d{2}[/-]\d{2,4})\s+"      # 12/03/2025
    r"(?P<desc>.+?)\s+"                             # SWIGGY BANGALORE
    r"(?P<amount>[\d,]+\.\d{2})\s*"                 # 450.00
    r"(?P<dir>Dr|Cr)?",                             # Dr
    re.IGNORECASE,
)


def parse_bank_pdf(content: bytes) -> list[dict]:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise ValueError("PyMuPDF is not installed; cannot read PDF statements.") from exc

    text = ""
    with fitz.open(stream=content, filetype="pdf") as doc:
        for page in doc:
            text += page.get_text()

    if not text.strip():
        raise ValueError(
            "No text found in this PDF. It is probably a scanned image — "
            "OCR is not supported in this version."
        )

    rows: list[dict] = []
    for line in text.splitlines():
        match = PDF_LINE.search(line.strip())
        if not match:
            continue
        when = parse_date(match.group("date"))
        if when is None:
            continue
        description = match.group("desc").strip()
        direction = (match.group("dir") or "DR").upper()
        rows.append(
            {
                "date": when,
                "amount": _to_float(match.group("amount")),
                "txn_type": "credit" if direction.startswith("CR") else "debit",
                "merchant": clean_merchant(description),
                "raw_description": description,
                "source": "bank",
            }
        )
    return rows


# ---------------------------------------------------------------- orders


def parse_order_history(content: bytes, source: str) -> list[dict]:
    """Swiggy / Zomato export.

    Why this earns its place: a bank statement shows one line, SWIGGY 412.00.
    An order export shows date, restaurant and amount for EVERY order.
    Bank data gives you a TOTAL. Order history gives you FREQUENCY — which is
    the difference between "you spend a lot on food" and "41 orders in 3
    months at Rs.384 average; halving that frees Rs.31,498 a year".
    """
    df = pd.read_csv(io.BytesIO(content))
    cols = {str(c).strip().lower(): c for c in df.columns}

    def find(*candidates: str):
        for cand in candidates:
            for low, original in cols.items():
                if cand in low:
                    return original
        return None

    date_col = find("date", "order date", "placed")
    amount_col = find("total", "amount", "order value", "bill")
    name_col = find("restaurant", "store", "merchant", "outlet", "item")

    if date_col is None or amount_col is None:
        raise ValueError(f"Order file needs a date and an amount column. Saw: {list(df.columns)}")

    category = "food_delivery" if source in {"swiggy", "zomato"} else "quick_commerce"

    rows: list[dict] = []
    for _, row in df.iterrows():
        when = parse_date(row.get(date_col))
        amount = _to_float(row.get(amount_col))
        if when is None or amount == 0.0:
            continue
        name = str(row.get(name_col, source)).strip() if name_col else source
        rows.append(
            {
                "date": when,
                "amount": amount,
                "txn_type": "debit",
                "merchant": source.upper(),
                "raw_description": f"{source.upper()} ORDER - {name}",
                "source": source,
                "category": category,   # already known; skips categorisation
            }
        )
    return rows


# ---------------------------------------------------------------- router


def parse_upload(filename: str, content: bytes) -> list[dict]:
    """Pick a parser from the filename."""
    lower = filename.lower()
    if "swiggy" in lower:
        return parse_order_history(content, "swiggy")
    if "zomato" in lower:
        return parse_order_history(content, "zomato")
    if lower.endswith(".pdf"):
        return parse_bank_pdf(content)
    if lower.endswith((".csv", ".txt")):
        return parse_bank_csv(content)
    if lower.endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(content))
        buffer = io.StringIO()
        df.to_csv(buffer, index=False)
        return parse_bank_csv(buffer.getvalue().encode())
    raise ValueError(f"Unsupported file type: {filename}")
