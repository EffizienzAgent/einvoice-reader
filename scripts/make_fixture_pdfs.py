"""Generate the PDF fixtures in tests/fixtures/pdf from the XML fixtures."""

import io
from pathlib import Path

import facturx
from pypdf import PdfWriter

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
OUT = FIXTURES / "pdf"
XRECHNUNG_CII = FIXTURES / "kosit/business-cases/standard/01.01a-INVOICE_uncefact.xml"


def blank_pdf(attachments: dict[str, bytes] | None = None) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    for name, data in (attachments or {}).items():
        writer.add_attachment(name, data)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def factur_x_pdf(xml: bytes, level: str = "autodetect") -> bytes:
    return facturx.generate_from_binary(
        blank_pdf(), xml, flavor="factur-x", level=level, check_xsd=False
    )


def main() -> None:
    OUT.mkdir(exist_ok=True)
    invoice = XRECHNUNG_CII.read_bytes()
    pdfs = {
        "en16931.pdf": factur_x_pdf(invoice),
        "zugferd_xrechnung_name.pdf": blank_pdf({"xrechnung.xml": invoice}),
        "two_attachments.pdf": blank_pdf({"notes.xml": b"<notes/>", "factur-x.xml": invoice}),
        "minimum.pdf": factur_x_pdf((FIXTURES / "own/minimum.xml").read_bytes(), "minimum"),
        "plain.pdf": blank_pdf(),
        "broken.pdf": b"%PDF-1.7\n" + bytes(range(100)),
    }
    for name, data in pdfs.items():
        (OUT / name).write_bytes(data)
        print(f"{name}: {len(data)} bytes")


if __name__ == "__main__":
    main()
