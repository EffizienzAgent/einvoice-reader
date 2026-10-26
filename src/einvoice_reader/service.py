from dataclasses import dataclass

from lxml import etree

from einvoice_reader import __version__
from einvoice_reader.detect import FormatInfo, detect
from einvoice_reader.errors import ReadError, ServiceBusy
from einvoice_reader.isolation import PoolCrashed, PoolTimeout, PoolUnavailable, WorkerPool
from einvoice_reader.loading import canonical_xml, load
from einvoice_reader.mapping import map_invoice
from einvoice_reader.model import FormatOut, Invoice, Message, Meta, Result, Validation
from einvoice_reader.schematron import SchematronRunner
from einvoice_reader.validation import finish_validation, xsd_messages


@dataclass
class Parsed:
    format: FormatInfo
    invoice: Invoice
    warnings: list[str]
    xml_text: str
    canonical: bytes | None
    xsd: list[Message]


def _xml_text(source) -> str:
    encoding = source.root.getroottree().docinfo.encoding or "utf-8"
    try:
        return source.xml.decode(encoding, errors="replace")
    except LookupError:
        return etree.tostring(source.root, encoding="unicode")


def parse_document(data: bytes, validate: bool) -> Parsed:
    """Everything that touches the untrusted document; runs inside a worker process."""
    try:
        return _parse_document(data, validate)
    except ReadError:
        raise
    except MemoryError:
        raise ReadError(
            "PROCESSING_FAILED", "The document needs too much memory to read; do not retry"
        ) from None
    except Exception as e:
        raise ReadError("READ_FAILED", f"Could not read document ({type(e).__name__})") from None


def _parse_document(data: bytes, validate: bool) -> Parsed:
    source = load(data)
    fmt = detect(source)
    try:
        invoice, warnings = map_invoice(source, fmt)
    except MemoryError:
        raise
    except Exception as e:
        format_out = FormatOut.from_info(fmt).model_dump(by_alias=True)
        raise ReadError("READ_FAILED", f"Could not read invoice: {e}", format_out) from e
    return Parsed(
        format=fmt,
        invoice=invoice,
        warnings=source.warnings + warnings,
        xml_text=_xml_text(source),
        canonical=canonical_xml(source.root) if validate else None,
        xsd=xsd_messages(source, fmt) if validate else [],
    )


def read_invoice(
    data: bytes,
    *,
    validate: bool,
    include_xml: bool,
    runner: SchematronRunner | None,
    parser: WorkerPool | None = None,
) -> Result:
    if parser is None:
        parsed = parse_document(data, validate)
    else:
        try:
            parsed = parser.call(parse_document, data, validate)
        except PoolTimeout as e:
            raise ReadError(
                "PROCESSING_TIMEOUT", f"Reading the document took longer than {parser.timeout_s}s"
            ) from e
        except PoolCrashed as e:
            raise ReadError(
                "PROCESSING_FAILED", "Reading this document crashed the reader; do not retry"
            ) from e
        except PoolUnavailable as e:
            raise ServiceBusy from e
    validation = Validation(performed=False)
    warnings = parsed.warnings
    if validate and runner is not None:
        validation, validation_warnings = finish_validation(
            parsed.format, parsed.xsd, parsed.canonical, runner
        )
        warnings += validation_warnings
    return Result(
        format=FormatOut.from_info(parsed.format),
        invoice=parsed.invoice,
        validation=validation,
        warnings=warnings,
        xml=parsed.xml_text if include_xml else None,
        meta=Meta(service_version=__version__),
    )
