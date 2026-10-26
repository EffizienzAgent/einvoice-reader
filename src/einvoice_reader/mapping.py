from decimal import Decimal, InvalidOperation

import facturx
from lxml import etree

from einvoice_reader.detect import FormatInfo, facturx_options
from einvoice_reader.loading import Source
from einvoice_reader.model import (
    Account,
    Address,
    AllowanceCharge,
    Attachment,
    Card,
    Classification,
    Contact,
    Delivery,
    DirectDebit,
    Identifier,
    Invoice,
    ItemAttribute,
    Line,
    Note,
    Party,
    Payee,
    Payment,
    Period,
    PrecedingInvoice,
    TaxRepresentative,
    Totals,
    VatBreakdown,
)
from einvoice_reader.normalize import normalize_dates, sanitize_currencies

CREDIT_NOTE_CODES = {"81", "83", "261", "262", "296", "308", "381", "396", "420", "458", "532"}
TYPE_CODES = {
    **dict.fromkeys(CREDIT_NOTE_CODES, "credit_note"),
    **dict.fromkeys(
        "71 80 82 84 102 130 202 203 204 211 218 219 295 326 331 380 382 383 385 387 388 "
        "390 393 394 395 456 457 553 575 623 633 751 780 817 870 875 876 877 935".split(),
        "invoice",
    ),
    "384": "corrected",
    "386": "prepayment",
    "389": "self_billed",
    "527": "self_billed",
}
_OWNERS = {
    "BG-4": "Seller",
    "BG-7": "Buyer",
    "BG-10": "Payee",
    "BG-11": "Tax representative",
    "BG-13": "Delivery",
    "BG-25": "Line",
}
_MULTI_VALUE_JOINED = {"BT-20", "BT-22"}
_OWN_XPATH = {"BT-81", "BT-84", "BT-85", "BT-86", "BT-32"}


def _text(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _amount(value) -> Decimal | None:
    value = _text(value)
    if value is None:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _identifier(value, scheme) -> Identifier | None:
    value = _text(value)
    return Identifier(value=value, scheme=_text(scheme)) if value else None


def _identifiers(data: dict | None) -> list[Identifier]:
    return [
        Identifier(value=value, scheme=_text(scheme))
        for scheme, value in (data or {}).items()
        if _text(value)
    ]


def _address(data: dict) -> Address:
    return Address(
        line1=_text(data.get("addr_l1")),
        line2=_text(data.get("addr_l2")),
        line3=_text(data.get("addr_l3")),
        city=_text(data.get("city")),
        postal_code=_text(data.get("postcode")),
        country_subdivision=_text(data.get("country_subdivision")),
        country_code=_text(data.get("country_code")),
    )


def _period(start, end) -> Period | None:
    start, end = _text(start), _text(end)
    return Period(start=start, end=end) if start or end else None


def _party(data: dict | None, label: str, warnings: list[str]) -> Party:
    data = data or {}
    name, trading_name = _text(data.get("name")), _text(data.get("biz_name"))
    if not name and trading_name:
        name = trading_name
        warnings.append(f"{label} legal name missing, used trading name")
    contacts = data.get("contacts") or [{}]
    contact = Contact(**{k: _text(contacts[0].get(k)) for k in ("name", "phone", "email")})
    return Party(
        name=name,
        trading_name=trading_name,
        identifiers=_identifiers(data.get("identifiers")),
        legal_registration_id=_identifier(
            data.get("legal_identifier"), data.get("legal_identifier_schemeid")
        ),
        vat_id=_text(data.get("vat_identifier")),
        tax_number=_text(data.get("tax_identifier")),
        electronic_address=_identifier(
            data.get("einvoicing_addr"), data.get("einvoicing_addr_schemeid")
        ),
        address=_address(data),
        contact=contact if any(vars(contact).values()) else None,
    )


def _allowances_charges(bt: dict, allowance_key: str, charge_key: str) -> list[AllowanceCharge]:
    items = [(False, item) for item in bt.get(allowance_key) or []]
    items += [(True, item) for item in bt.get(charge_key) or []]
    return [
        AllowanceCharge(
            is_charge=is_charge,
            amount=_amount(item.get("amount")),
            base_amount=_amount(item.get("base_amount")),
            percent=_amount(item.get("rate")),
            reason=_text(item.get("reason")),
            reason_code=_text(item.get("reason_code")),
            vat_category_code=_text(item.get("vat_category_code")),
            vat_rate=_amount(item.get("vat_rate")),
        )
        for is_charge, item in items
    ]


def _flatten(value, key: str, warnings: list[str], owner: str | None = None):
    if isinstance(value, dict):
        return {
            k: _flatten(v, str(k), warnings, _OWNERS.get(str(k), owner)) for k, v in value.items()
        }
    if not isinstance(value, list):
        return value
    if all(isinstance(item, dict) for item in value):
        return [_flatten(item, key, warnings, owner) for item in value]
    if key in _MULTI_VALUE_JOINED:
        return "\n".join(str(item).strip() for item in value if _text(item))
    if key not in _OWN_XPATH:
        field = f"{owner} {key}" if owner else key
        warnings.append(f"{field} appears {len(value)} times, used the first")
    return value[0] if value else None


def _xpath_text(node: etree._Element, path: str) -> str | None:
    return _text(next(iter(node.xpath(path)), None))


def _accounts(root: etree._Element, syntax: str) -> list[Account]:
    if syntax == "CII":
        means = root.xpath("//*[local-name()='SpecifiedTradeSettlementPaymentMeans']")
        paths = (
            "*[local-name()='PayeePartyCreditorFinancialAccount']/*[local-name()='IBANID'"
            " or local-name()='ProprietaryID']/text()",
            "*[local-name()='PayeeSpecifiedCreditorFinancialInstitution']"
            "/*[local-name()='BICID']/text()",
            "*[local-name()='PayeePartyCreditorFinancialAccount']/*[local-name()='AccountName']/text()",
        )
    else:
        means = root.xpath("/*/*[local-name()='PaymentMeans']")
        paths = (
            "*[local-name()='PayeeFinancialAccount']/*[local-name()='ID']/text()",
            "*[local-name()='PayeeFinancialAccount']/*[local-name()='FinancialInstitutionBranch']"
            "/*[local-name()='ID']/text()",
            "*[local-name()='PayeeFinancialAccount']/*[local-name()='Name']/text()",
        )
    accounts = []
    for node in means:
        iban, bic, name = (_xpath_text(node, path) for path in paths)
        if iban:
            accounts.append(Account(iban=iban, bic=bic, name=name))
    return accounts


def _ubl_seller_tax_number(root: etree._Element) -> str | None:
    return _xpath_text(
        root,
        "/*/*[local-name()='AccountingSupplierParty']/*[local-name()='Party']"
        "/*[local-name()='PartyTaxScheme'][*[local-name()='TaxScheme']/*[local-name()='ID']"
        " != 'VAT']/*[local-name()='CompanyID']/text()",
    )


def _line_hierarchy(root: etree._Element, syntax: str) -> dict[str, tuple[str | None, str | None]]:
    hierarchy: dict[str, tuple[str | None, str | None]] = {}
    if syntax == "CII":
        for doc in root.xpath(
            "//*[local-name()='IncludedSupplyChainTradeLineItem']"
            "/*[local-name()='AssociatedDocumentLineDocument']"
        ):
            line_id = _xpath_text(doc, "*[local-name()='LineID']/text()")
            hierarchy.setdefault(
                line_id,
                (
                    _xpath_text(doc, "*[local-name()='ParentLineID']/text()"),
                    _xpath_text(doc, "*[local-name()='LineStatusReasonCode']/text()"),
                ),
            )
    return hierarchy


def _first_identifier(data) -> Identifier | None:
    if isinstance(data, dict):
        identifiers = _identifiers(data)
        return identifiers[0] if identifiers else None
    value = _text(data)
    return Identifier(value=value) if value else None


def _classifications(data) -> list[Classification]:
    if not isinstance(data, dict):
        return []
    result = []
    for scheme, versions in data.items():
        items = versions.items() if isinstance(versions, dict) else [(None, versions)]
        for version, value in items:
            if _text(value):
                result.append(
                    Classification(value=_text(value), scheme=_text(scheme), version=_text(version))
                )
    return result


def _line(data: dict, hierarchy: dict) -> Line:
    notes = [_text(n.get("BT-127")) for n in data.get("BT-127-00") or []]
    parent_line_id, status = hierarchy.get(_text(data.get("BT-126")), (None, None))
    return Line(
        id=_text(data.get("BT-126")),
        name=_text(data.get("BT-153")),
        description=_text(data.get("BT-154")),
        note="\n".join(n for n in notes if n) or None,
        quantity=_amount(data.get("BT-129")),
        unit_code=_text(data.get("BT-130")),
        net_price=_amount(data.get("BT-146")),
        gross_price=_amount(data.get("BT-148")),
        price_base_quantity=_amount(data.get("BT-149")),
        net_amount=_amount(data.get("BT-131")),
        vat_category_code=_text(data.get("BT-151")),
        vat_rate=_amount(data.get("BT-152")),
        seller_item_id=_text(data.get("BT-155")),
        buyer_item_id=_text(data.get("BT-156")),
        standard_item_id=_text(data.get("BT-157")),
        order_line_reference=_text(data.get("BT-132")),
        period=_period(data.get("BT-134"), data.get("BT-135")),
        allowances_charges=_allowances_charges(data, "BG-27", "BG-28"),
        parent_line_id=parent_line_id,
        line_status_reason_code=status,
        accounting_reference=_text(data.get("BT-133")),
        object_identifier=_first_identifier(data.get("BT-128")),
        attributes=[
            ItemAttribute(name=_text(a.get("BT-160")), value=_text(a.get("BT-161")))
            for a in data.get("BG-32") or []
        ],
        classifications=_classifications(data.get("BT-158")),
    )


def _payment(bt: dict, accounts: list[Account]) -> Payment:
    card = Card(last_digits=_text(bt.get("BT-87")), holder=_text(bt.get("BT-88")))
    debit = DirectDebit(
        mandate_reference=_text(bt.get("BT-89")),
        creditor_id=_text(bt.get("BT-90")),
        debited_account=_text(bt.get("BT-91")),
    )
    return Payment(
        means_code=_text(bt.get("BT-81")),
        means_text=_text(bt.get("BT-82")),
        remittance_information=_text(bt.get("BT-83")),
        accounts=accounts,
        card=card if card.last_digits or card.holder else None,
        direct_debit=debit if any(vars(debit).values()) else None,
    )


def _payee(data: dict | None) -> Payee | None:
    if not data:
        return None
    identifiers = _identifiers(data.get("identifiers"))
    return Payee(
        name=_text(data.get("name")) or _text(data.get("biz_name")),
        identifier=identifiers[0].value if identifiers else None,
        legal_registration_id=_identifier(
            data.get("legal_identifier"), data.get("legal_identifier_schemeid")
        ),
    )


def _tax_representative(data: dict | None) -> TaxRepresentative | None:
    if not data:
        return None
    return TaxRepresentative(
        name=_text(data.get("name")) or _text(data.get("biz_name")),
        vat_id=_text(data.get("vat_identifier")),
        address=_address(data),
    )


def _delivery(data: dict | None) -> Delivery | None:
    if not data:
        return None
    identifiers = _identifiers(data.get("identifiers"))
    return Delivery(
        party_name=_text(data.get("name")),
        location_id=identifiers[0].value if identifiers else None,
        address=_address(data),
    )


def _totals(bt: dict, warnings: list[str]) -> Totals:
    keys = {
        "line_net_total": "BT-106",
        "allowance_total": "BT-107",
        "charge_total": "BT-108",
        "net_total": "BT-109",
        "vat_total": "BT-110",
        "vat_total_tax_currency": "BT-111",
        "gross_total": "BT-112",
        "prepaid_amount": "BT-113",
        "rounding_amount": "BT-114",
        "amount_due": "BT-115",
    }
    for key in keys.values():
        if _text(bt.get(key)) is not None and _amount(bt.get(key)) is None:
            warnings.append(f"{key} is not a number: {_text(bt.get(key))!r}")
    totals = Totals(**{field: _amount(bt.get(key)) for field, key in keys.items()})
    if totals.gross_total is not None and totals.amount_due is not None:
        expected = totals.gross_total - (totals.prepaid_amount or 0) + (totals.rounding_amount or 0)
        if expected != totals.amount_due:
            warnings.append(
                f"BT-115 {totals.amount_due} differs from BT-112 - BT-113 + BT-114 = {expected}"
            )
    return totals


def map_invoice(source: Source, fmt: FormatInfo) -> tuple[Invoice, list[str]]:
    normalized, warnings = normalize_dates(source.root)
    currency_warnings = sanitize_currencies(normalized)
    warnings += currency_warnings
    raw = facturx.parse_ubl_cii_xml(
        etree.tostring(normalized),
        check_xsd=False,
        float_as="string",
        date_as="string",
        bytes_as="string",
        **facturx_options(source, fmt),
    )
    bt = _flatten(raw, "", warnings)
    hierarchy = _line_hierarchy(source.root, fmt.syntax)
    type_code = _text(bt.get("BT-3"))
    invoice = Invoice(
        number=_text(bt.get("BT-1")),
        type_code=type_code,
        type=TYPE_CODES.get(type_code, "other"),
        is_credit_note=type_code in CREDIT_NOTE_CODES,
        issue_date=_text(bt.get("BT-2")),
        due_date=_text(bt.get("BT-9")),
        tax_point_date=_text(bt.get("BT-7")),
        currency=_text(bt.get("BT-5")),
        tax_currency=_text(bt.get("BT-6")),
        buyer_reference=_text(bt.get("BT-10")),
        order_reference=_text(bt.get("BT-13")),
        sales_order_reference=_text(bt.get("BT-14")),
        contract_reference=_text(bt.get("BT-12")),
        project_reference=_text(bt.get("BT-11")),
        buyer_accounting_reference=_text(bt.get("BT-19")),
        invoiced_object_identifier=_first_identifier(bt.get("BT-18")),
        receiving_advice_reference=_text(bt.get("BT-15")),
        despatch_advice_reference=_text(bt.get("BT-16")),
        tender_reference=_text(bt.get("BT-17")),
        business_process=_text(bt.get("BT-23")),
        preceding_invoices=[
            PrecedingInvoice(number=_text(p.get("BT-25")), issue_date=_text(p.get("BT-26")))
            for p in bt.get("BG-3") or []
        ],
        delivery_date=_text(bt.get("BT-72")),
        invoicing_period=_period(bt.get("BT-73"), bt.get("BT-74")),
        notes=[
            Note(subject_code=_text(n.get("BT-21")), text=_text(n.get("BT-22")))
            for n in bt.get("BG-1") or []
        ],
        payment_terms=_text(bt.get("BT-20")),
        payment=_payment(bt, _accounts(source.root, fmt.syntax)),
        seller=_party(bt.get("BG-4"), "Seller", warnings),
        buyer=_party(bt.get("BG-7"), "Buyer", warnings),
        payee=_payee(bt.get("BG-10")),
        tax_representative=_tax_representative(bt.get("BG-11")),
        delivery=_delivery(bt.get("BG-13")),
        totals=_totals(bt, warnings),
        vat_breakdown=[
            VatBreakdown(
                category_code=_text(v.get("BT-118")),
                rate=_amount(v.get("BT-119")),
                taxable_amount=_amount(v.get("BT-116")),
                tax_amount=_amount(v.get("BT-117")),
                exemption_reason=_text(v.get("BT-120")),
                exemption_reason_code=_text(v.get("BT-121")),
            )
            for v in bt.get("BG-23") or []
        ],
        allowances_charges=_allowances_charges(bt, "BG-20", "BG-21"),
        lines=[_line(line, hierarchy) for line in bt.get("BG-25") or []],
        attachments=[
            Attachment(
                id=_text(a.get("BT-122")),
                description=_text(a.get("BT-123")),
                file_name=_text(a.get("BT-125-2")),
                mime_type=_text(a.get("BT-125-1")),
                uri=_text(a.get("BT-124")),
            )
            for a in bt.get("BG-24") or []
        ],
    )
    if any(w.startswith(("BT-5 is not", "BT-5 missing")) for w in currency_warnings):
        invoice.currency = None
    if any(w.startswith("BT-6 is not") for w in currency_warnings):
        invoice.tax_currency = None
    if fmt.syntax == "UBL" and not invoice.seller.tax_number:
        invoice.seller.tax_number = _ubl_seller_tax_number(source.root)
    if any(line.parent_line_id for line in invoice.lines):
        warnings.append(
            "Invoice contains sub-lines (parentLineId); sum only top-level lines or use totals"
        )
    if fmt.syntax == "UBL" and source.root.xpath("//*[local-name()='SubInvoiceLine']"):
        warnings.append("UBL sub-lines (SubInvoiceLine) are not listed in lines; use totals")
    if invoice.totals.vat_total is None and "BT-110" not in " ".join(warnings):
        warnings.append("BT-110 missing")
    return invoice, warnings
