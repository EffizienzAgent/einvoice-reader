"""Repair date values that factur-x cannot parse, so one odd date never loses a whole invoice."""

import copy
import re
from datetime import date

from lxml import etree

_UBL_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
_CII = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
_CII_RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _valid(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def _cii_date(value: str, fmt: str | None) -> str | None:
    value = value.strip()
    if fmt == "610" and re.fullmatch(r"\d{6}", value):
        return value + "01" if _valid(int(value[:4]), int(value[4:]), 1) else None
    if fmt == "616" and re.fullmatch(r"\d{6}", value):
        try:
            return date.fromisocalendar(int(value[:4]), int(value[4:]), 1).strftime("%Y%m%d")
        except ValueError:
            return None
    if match := _ISO.fullmatch(value):
        value = "".join(match.groups())
    if re.fullmatch(r"\d{8}", value) and _valid(int(value[:4]), int(value[4:6]), int(value[6:])):
        return value
    return None


def _ubl_date(value: str) -> str | None:
    match = _ISO.match(value.strip())
    rest = value.strip()[10:]
    if not match or not re.fullmatch(r"|Z|[+-]\d{2}:\d{2}", rest):
        return None
    return match.group(0) if _valid(*map(int, match.groups())) else None


def normalize_dates(root: etree._Element) -> tuple[etree._Element, list[str]]:
    root = copy.deepcopy(root)
    warnings: list[str] = []
    for node in root.xpath("//*[local-name()='DateTimeString' or local-name()='DateString']"):
        original, fmt = node.text or "", node.get("format")
        fixed = _cii_date(original, fmt)
        if fixed is None:
            container = node.getparent()
            warnings.append(
                f"Unreadable date {original!r} in {etree.QName(container).localname}, ignored"
            )
            container.getparent().remove(container)
        elif fixed != original or fmt != "102":
            if fmt in ("610", "616"):
                iso = f"{fixed[:4]}-{fixed[4:6]}-{fixed[6:]}"
                warnings.append(f"Date {original!r} in format {fmt} read as {iso}")
            node.text, node.attrib["format"] = fixed, "102"
    for node in root.iter(f"{{{_UBL_CBC}}}*"):
        if not etree.QName(node).localname.endswith("Date"):
            continue
        original = node.text or ""
        fixed = _ubl_date(original)
        if fixed is None:
            warnings.append(
                f"Unreadable date {original!r} in {etree.QName(node).localname}, ignored"
            )
            node.getparent().remove(node)
        else:
            node.text = fixed
    return root, warnings


INVALID_CURRENCY = "XXX"
_CURRENCY_ELEMENTS = {
    "InvoiceCurrencyCode": "BT-5",
    "TaxCurrencyCode": "BT-6",
    "DocumentCurrencyCode": "BT-5",
}


def sanitize_currencies(root: etree._Element) -> list[str]:
    """factur-x puts currency codes into XPath expressions; only ISO 4217 shapes may pass."""
    warnings = []
    seen: set[str] = set()
    for node in list(root.iter()):
        if not isinstance(node.tag, str):
            continue
        name = etree.QName(node).localname
        label = _CURRENCY_ELEMENTS.get(name)
        if label and name in seen:
            warnings.append(f"{label} appears more than once, used the first")
            node.getparent().remove(node)
            continue
        if label:
            seen.add(name)
        if label and not re.fullmatch(r"[A-Z]{3}", (node.text or "").strip()):
            warnings.append(f"{label} is not a valid currency code, ignored")
            node.text = INVALID_CURRENCY
    for node in root.xpath("//*[@currencyID]"):
        if not re.fullmatch(r"[A-Z]{3}", node.get("currencyID").strip()):
            del node.attrib["currencyID"]
    if _add_missing_currency(root):
        warnings.append("BT-5 missing")
    return warnings


def _add_missing_currency(root: etree._Element) -> bool:
    """factur-x fails without BT-5; insert a placeholder so the rest of the invoice is read."""
    if root.xpath("//*[local-name()='InvoiceCurrencyCode' or local-name()='DocumentCurrencyCode']"):
        return False
    used = root.xpath("//*[local-name()='TaxTotalAmount' or local-name()='TaxAmount']/@currencyID")
    placeholder = used[0] if used else INVALID_CURRENCY
    namespace = etree.QName(root).namespace
    if namespace == _CII:
        parents = root.xpath("//*[local-name()='ApplicableHeaderTradeSettlement']")
        if not parents:
            return False
        element = etree.SubElement(parents[0], f"{{{_CII_RAM}}}InvoiceCurrencyCode")
    else:
        element = etree.SubElement(root, f"{{{_UBL_CBC}}}DocumentCurrencyCode")
    element.text = placeholder
    return True
