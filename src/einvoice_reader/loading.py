import io
import itertools
import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from lxml import etree
from pypdf import PdfReader, apply_configuration
from pypdf.errors import PdfReadError

from einvoice_reader.errors import ReadError

INVOICE_ROOTS = {
    "{urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100}CrossIndustryInvoice": "CII",
    "{urn:oasis:names:specification:ubl:schema:xsd:Invoice-2}Invoice": "UBL-Invoice",
    "{urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2}CreditNote": "UBL-CreditNote",
}

INVOICE_MARKERS = (b"CrossIndustryInvoice", b"urn:oasis:names:specification:ubl")
_BOM = b"\xef\xbb\xbf"
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
KNOWN_NAMES = {"factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml"}
MAX_ATTACHMENTS = 200
MAX_STREAM_BYTES = 25 * 2**20
MAX_ATTACHMENT_BUDGET = 60 * 2**20
MAX_XMP_BYTES = 2**20

_PDF_LIMITS = {
    name: MAX_STREAM_BYTES
    for name in (
        "maximum_declared_stream_length",
        "array_based_stream_maximum_output_length",
        "jbig2_maximum_output_length",
        "lzw_maximum_output_length",
        "run_length_maximum_output_length",
        "zlib_maximum_output_length",
        "brotli_maximum_output_length",
    )
}
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)

logging.getLogger("factur-x").setLevel(logging.CRITICAL)


@dataclass
class Source:
    container: Literal["PDF", "XML"]
    xml: bytes
    root: etree._Element
    embedded_file_name: str | None = None
    xmp: etree._Element | None = None
    warnings: list[str] = field(default_factory=list)


def parse_xml(data: bytes) -> etree._Element:
    try:
        root = etree.fromstring(data, _PARSER)
    except etree.XMLSyntaxError as e:
        raise ReadError("MALFORMED_XML", f"Not well-formed XML: {e}") from e
    docinfo = root.getroottree().docinfo
    if docinfo.doctype or docinfo.internalDTD is not None or docinfo.system_url:
        raise ReadError("MALFORMED_XML", "DOCTYPE declarations are not allowed")
    return root


def canonical_xml(root: etree._Element) -> bytes:
    return etree.tostring(root.getroottree(), encoding="UTF-8", xml_declaration=True)


def _strip(data: bytes) -> bytes:
    data = data.lstrip()
    if data.startswith(_BOM):
        data = data[len(_BOM) :].lstrip()
    return data


def load(data: bytes) -> Source:
    data = _strip(data)
    if not data:
        raise ReadError("EMPTY_FILE", "The file is empty")
    pdf_start = data.find(b"%PDF", 0, 1024)
    if pdf_start >= 0:
        return _load_pdf(data[pdf_start:])
    if not data.startswith((b"<", *_UTF16_BOMS)):
        raise ReadError("NO_EMBEDDED_XML", "Neither a PDF nor an XML file")
    try:
        root = parse_xml(data)
    except ReadError as e:
        if e.code == "MALFORMED_XML" and not _looks_like_invoice_xml(data):
            raise ReadError("NO_EMBEDDED_XML", "Not an e-invoice: unreadable markup") from e
        raise
    return Source(container="XML", xml=data, root=root)


def _looks_like_invoice_xml(data: bytes) -> bool:
    head = data[:2048]
    return head.startswith(b"<?xml") or any(marker in head for marker in INVOICE_MARKERS)


def _load_pdf(data: bytes) -> Source:
    with apply_configuration(**_PDF_LIMITS):
        return _load_pdf_limited(data)


def _open_pdf(data: bytes) -> PdfReader:
    try:
        return PdfReader(io.BytesIO(data))
    except PdfReadError as e:
        repaired = _crypt_filter_length_in_bytes(data)
        if "encryption key length" not in str(e) or repaired == data:
            raise
        return PdfReader(io.BytesIO(repaired))


def _crypt_filter_length_in_bytes(data: bytes) -> bytes:
    """Some generators (e.g. Compart DocBridge) write the AES crypt filter /Length in bits."""

    def fix(match: re.Match) -> bytes:
        return re.sub(
            rb"(/Length\s+)(128|256)\b",
            lambda m: m.group(1) + {b"128": b"16 ", b"256": b"32 "}[m.group(2)],
            match.group(0),
        )

    return re.sub(rb"<<[^<>]*?/CFM\s*/AESV[23][^<>]*?>>", fix, data)


def _load_pdf_limited(data: bytes) -> Source:
    try:
        reader = _open_pdf(data)
        encrypted = reader.is_encrypted
    except Exception as e:
        raise ReadError("MALFORMED_PDF", f"Not a readable PDF: {e}") from e
    try:
        if encrypted:
            reader.decrypt("")
        embedded = list(itertools.islice(reader.attachment_list, MAX_ATTACHMENTS))
    except Exception as e:
        raise ReadError("NO_EMBEDDED_XML", f"PDF attachments are not readable: {e}") from e

    found, broken = _find_invoice_xml(embedded)
    if not found:
        detail = f"; {', '.join(broken)} could not be parsed" if broken else ""
        raise ReadError("NO_EMBEDDED_XML", f"The PDF contains no e-invoice XML{detail}")
    found.sort(key=lambda item: item[2])
    name, xml, _ = found[0]
    root = parse_xml(xml)
    warnings = [
        f"PDF contains a further invoice XML {other!r}, used {name!r}" for other, _, _ in found[1:]
    ]
    return Source(
        container="PDF",
        xml=xml,
        root=root,
        embedded_file_name=name,
        xmp=_read_xmp(reader),
        warnings=warnings,
    )


def _is_minimal_profile(root: etree._Element) -> bool:
    ids = root.xpath(
        "/*/*[local-name()='ExchangedDocumentContext']"
        "/*[local-name()='GuidelineSpecifiedDocumentContextParameter']/*[local-name()='ID']/text()"
        " | /*/*[local-name()='CustomizationID']/text()"
    )
    value = " ".join(ids).lower()
    return "minimum" in value or "basicwl" in value


def _find_invoice_xml(embedded: list) -> tuple[list[tuple[str, bytes, bool]], list[str]]:
    """Candidate invoices as (name, bytes, is_minimal); parsed trees are not kept."""
    ordered = sorted(embedded, key=lambda e: (e.name or "").lower() not in KNOWN_NAMES)
    found, broken, budget = [], [], MAX_ATTACHMENT_BUDGET
    for attachment in ordered:
        if budget <= 0:
            break
        try:
            content = attachment.content
        except Exception:
            budget -= MAX_STREAM_BYTES
            continue
        budget -= len(content)
        xml = _strip(content)
        if not xml.startswith((b"<", *_UTF16_BOMS)):
            continue
        try:
            root = parse_xml(xml)
        except ReadError:
            if (attachment.name or "").lower() in KNOWN_NAMES:
                broken.append(attachment.name)
            continue
        if root.tag in INVOICE_ROOTS:
            found.append((attachment.name, xml, _is_minimal_profile(root)))
        del root
    return found, broken


def _read_xmp(reader: PdfReader) -> etree._Element | None:
    try:
        metadata = reader.trailer["/Root"]["/Metadata"].get_object().get_data()
        return parse_xml(metadata) if len(metadata) <= MAX_XMP_BYTES else None
    except Exception:
        return None
