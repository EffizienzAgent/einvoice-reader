import re
from dataclasses import dataclass
from typing import Literal

import facturx
from lxml import etree

from einvoice_reader.errors import ReadError
from einvoice_reader.loading import INVOICE_ROOTS, Source

E_INVOICE_NAMESPACES = (
    "urn:un:unece:uncefact:",
    "urn:oasis:names:specification:ubl:",
    "urn:ferd:",
)
XMP_NAMESPACES = {
    "urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#": "Factur-X",
    "urn:zugferd:pdfa:CrossIndustryDocument:invoice:2p0#": "ZUGFeRD",
}

_BT24_PATHS = {
    "CII": "/*/*[local-name()='ExchangedDocumentContext']"
    "/*[local-name()='GuidelineSpecifiedDocumentContextParameter']/*[local-name()='ID']/text()",
    "UBL": "/*/*[local-name()='CustomizationID']/text()",
}


@dataclass
class FormatInfo:
    syntax: Literal["CII", "UBL"]
    container: Literal["PDF", "XML"]
    document_type: Literal["Invoice", "CreditNote"]
    standard: Literal["ZUGFeRD", "Factur-X", "XRechnung", "EN16931", "Peppol", "unknown"]
    profile: str | None
    version: str | None
    customization_id: str | None
    embedded_file_name: str | None


ZUGFERD_20 = "urn:zugferd.de:2p0:"
_PROFILE_SUFFIXES = {
    "minimum": "MINIMUM",
    "basicwl": "BASIC WL",
    "basic": "BASIC",
    "extended": "EXTENDED",
}
_FACTURX_LEVELS = {
    "MINIMUM": "minimum",
    "BASIC WL": "basicwl",
    "BASIC": "basic",
    "EXTENDED": "extended",
}


def _standard_profile_version(bt24: str | None) -> tuple[str, str | None, str | None]:
    value = (bt24 or "").strip().lower()
    if "xrechnung" in value:
        version = re.search(r"xrechnung_(\d+\.\d+)", value)
        return "XRechnung", "XRECHNUNG", version.group(1) if version else None
    if "peppol" in value:
        return "Peppol", None, None
    if ZUGFERD_20 in value:
        suffix = value.rsplit(ZUGFERD_20, 1)[1]
        return "ZUGFeRD", _PROFILE_SUFFIXES.get(suffix), "2.0"
    for suffix, profile in _PROFILE_SUFFIXES.items():
        if value.endswith(f"urn:factur-x.eu:1p0:{suffix}"):
            return "Factur-X", profile, None
    if value == "urn:cen.eu:en16931:2017":
        return "EN16931", "EN16931", None
    return "unknown", None, None


def facturx_options(source: Source, fmt: "FormatInfo") -> dict:
    """Explicit flavor and level for factur-x when its own BT-24 detection would fail."""
    try:
        facturx.get_level(source.root)
        return {}
    except Exception:
        return {
            "flavor": facturx.get_flavor(source.root),
            "level": _FACTURX_LEVELS.get(fmt.profile, "en16931"),
        }


def _xmp_standard_and_version(xmp: etree._Element | None) -> tuple[str | None, str | None]:
    if xmp is None:
        return None, None
    for element in xmp.iter():
        candidates = [(element.tag, element.text)] if isinstance(element.tag, str) else []
        candidates += list(element.attrib.items())
        for key, value in candidates:
            namespace, _, local = key[1:].partition("}") if key.startswith("{") else ("", "", key)
            if namespace in XMP_NAMESPACES and local == "Version":
                return XMP_NAMESPACES[namespace], (value or "").strip() or None
    return None, None


def detect(source: Source) -> FormatInfo:
    kind = INVOICE_ROOTS.get(source.root.tag)
    if kind is None:
        qname = etree.QName(source.root)
        if (qname.namespace or "").startswith(E_INVOICE_NAMESPACES):
            raise ReadError(
                "UNSUPPORTED_FORMAT",
                f"Unsupported e-invoice document {qname.localname!r}",
                {"container": source.container},
            )
        raise ReadError("NO_EMBEDDED_XML", f"Not an e-invoice: XML root {qname.localname!r}")
    syntax = "CII" if kind == "CII" else "UBL"
    bt24 = next(iter(source.root.xpath(_BT24_PATHS[syntax])), None)
    bt24 = str(bt24) if bt24 is not None else None
    standard, profile, version = _standard_profile_version(bt24)
    if source.container == "PDF" and standard != "XRechnung":
        xmp_standard, _ = _xmp_standard_and_version(source.xmp)
        standard = xmp_standard or standard
    return FormatInfo(
        syntax=syntax,
        container=source.container,
        document_type="CreditNote" if kind == "UBL-CreditNote" else "Invoice",
        standard=standard,
        profile=profile,
        version=version,
        customization_id=bt24,
        embedded_file_name=source.embedded_file_name,
    )
