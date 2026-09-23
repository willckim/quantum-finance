"""SampleCo Finance -- monthly budget-vs-actual variance report (synthetic).

Contains no cryptography. It is here as a negative control: words such as
DESCRIPTION, addresses, parsable, Transaction, TLSv1.3, a French title with Des and
des, a variable named dh, EXPORT_BITS and OpenSSL 3.0 must NOT be flagged.
"""

DESCRIPTION_FIELD = "line_description"
REPORT_TITLE_FR = "Des chiffres et des lettres"
EXPORT_BITS = 1024           # bits per packed ledger flag, not a key size
MAILING_ADDRESSES = ["billing", "remittance"]


def parsable_transaction(row: dict) -> bool:
    return bool(row.get("Transaction")) and row.get("transport") == "TLSv1.3"


def chart_box(page_size: tuple[int, int]) -> int:
    width, dh = page_size        # dh = detail-row height
    return width * dh


def checksum_note() -> str:
    return "Export files are hashed with sha256 before upload (OpenSSL 3.0 or later)."
