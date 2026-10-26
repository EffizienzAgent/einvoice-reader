import json
from decimal import Decimal

import pytest
from conftest import KOSIT, fixture
from lxml import etree

from einvoice_reader.detect import detect
from einvoice_reader.loading import load
from einvoice_reader.mapping import map_invoice
from einvoice_reader.model import FormatOut, Meta, Result, Validation

COMPREHENSIVE = "kosit/technical-cases/cius/01.03_comprehensive_test_{}.xml"
UBL = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}


def read(data: bytes):
    source = load(data)
    return map_invoice(source, detect(source))


def cii_amount(xml: bytes, name: str) -> Decimal:
    root = etree.fromstring(xml)
    return Decimal(root.xpath(f"//*[local-name()='{name}']/text()")[0])


def test_cii_core(xrechnung_cii):
    invoice, _ = read(xrechnung_cii)
    root = etree.fromstring(xrechnung_cii)
    issue = root.xpath("//*[local-name()='IssueDateTime']/*/text()")[0]
    assert (
        invoice.number
        == root.xpath("/*/*[local-name()='ExchangedDocument']/*[local-name()='ID']/text()")[0]
    )
    assert invoice.issue_date == f"{issue[:4]}-{issue[4:6]}-{issue[6:]}"
    assert invoice.currency == "EUR"
    assert invoice.payment.accounts[0].iban == "DE79000000001234567890"
    assert invoice.totals.net_total == cii_amount(xrechnung_cii, "TaxBasisTotalAmount")
    assert invoice.totals.vat_total == cii_amount(xrechnung_cii, "TaxTotalAmount")
    assert invoice.totals.gross_total == cii_amount(xrechnung_cii, "GrandTotalAmount")
    assert invoice.totals.amount_due == cii_amount(xrechnung_cii, "DuePayableAmount")


def test_ubl_matches_cii(xrechnung_cii, xrechnung_ubl):
    cii, _ = read(xrechnung_cii)
    ubl, _ = read(xrechnung_ubl)
    assert (ubl.number, ubl.issue_date, ubl.totals, ubl.seller.name) == (
        cii.number,
        cii.issue_date,
        cii.totals,
        cii.seller.name,
    )


def test_ubl_seller_name_is_registration_name():
    xml = fixture(COMPREHENSIVE.format("ubl"))
    root = etree.fromstring(xml)
    seller = "//cac:AccountingSupplierParty/cac:Party"
    invoice, _ = read(xml)
    assert (
        invoice.seller.name
        == root.xpath(f"{seller}/cac:PartyLegalEntity/cbc:RegistrationName/text()", namespaces=UBL)[
            0
        ]
    )
    assert (
        invoice.seller.trading_name
        == root.xpath(f"{seller}/cac:PartyName/cbc:Name/text()", namespaces=UBL)[0]
    )


@pytest.mark.parametrize("case", ["03.01a", "03.04a", "03.05a"])
def test_mustang_regressions(case):
    xml = (KOSIT / f"{case}-INVOICE_uncefact.xml").read_bytes()
    invoice, _ = read(xml)
    assert invoice.totals.gross_total == cii_amount(xml, "GrandTotalAmount")


def test_credit_note():
    invoice, _ = read(fixture("own/credit_note_ubl.xml"))
    assert invoice.type == "credit_note"
    assert invoice.type_code == "381"
    assert invoice.lines[0].quantity == 1
    assert invoice.totals.gross_total > 0


def test_minimum_has_no_lines():
    invoice, _ = read(fixture("own/minimum.xml"))
    assert invoice.lines == []
    assert invoice.vat_breakdown == []
    assert invoice.totals.gross_total == Decimal("119.00")
    assert invoice.issue_date == "2026-01-05"
    assert invoice.seller.vat_id == "DE111111111"
    assert invoice.buyer_reference == "REF-1"


def test_comprehensive_details():
    invoice, _ = read(fixture(COMPREHENSIVE.format("uncefact")))
    assert invoice.payment.card.last_digits == "6789"
    assert invoice.preceding_invoices[0].number == "PIR1234567890"
    assert invoice.attachments[0].mime_type == "application/pdf"
    assert invoice.lines[0].vat_rate == 19
    assert invoice.seller.contact.email == "tim.tester@test.com"
    assert invoice.seller.electronic_address.scheme == "EM"


EXPECTED_KEYS = {
    "": ["format", "invoice", "validation", "warnings", "xml", "meta"],
    "format": [
        "syntax",
        "container",
        "documentType",
        "standard",
        "profile",
        "version",
        "customizationId",
        "embeddedFileName",
    ],
    "invoice": [
        "number",
        "typeCode",
        "type",
        "issueDate",
        "dueDate",
        "taxPointDate",
        "currency",
        "taxCurrency",
        "buyerReference",
        "orderReference",
        "salesOrderReference",
        "contractReference",
        "projectReference",
        "precedingInvoices",
        "deliveryDate",
        "invoicingPeriod",
        "notes",
        "paymentTerms",
        "payment",
        "seller",
        "buyer",
        "payee",
        "taxRepresentative",
        "delivery",
        "totals",
        "vatBreakdown",
        "allowancesCharges",
        "lines",
        "attachments",
    ],
    "invoice.payment": ["meansCode", "remittanceInformation", "accounts", "card", "directDebit"],
    "invoice.seller": [
        "name",
        "tradingName",
        "identifiers",
        "legalRegistrationId",
        "vatId",
        "taxNumber",
        "electronicAddress",
        "address",
        "contact",
    ],
    "invoice.seller.address": [
        "line1",
        "line2",
        "line3",
        "city",
        "postalCode",
        "countrySubdivision",
        "countryCode",
    ],
    "invoice.totals": [
        "lineNetTotal",
        "allowanceTotal",
        "chargeTotal",
        "netTotal",
        "vatTotal",
        "vatTotalTaxCurrency",
        "grossTotal",
        "prepaidAmount",
        "roundingAmount",
        "amountDue",
    ],
    "validation": ["performed", "valid", "isEInvoice", "ruleSets", "messages"],
    "meta": ["serviceVersion"],
}


def minimum_result() -> Result:
    source = load(fixture("own/minimum.xml"))
    fmt = detect(source)
    invoice, warnings = map_invoice(source, fmt)
    return Result(
        format=FormatOut.from_info(fmt),
        invoice=invoice,
        validation=Validation(performed=False),
        warnings=warnings,
        xml=None,
        meta=Meta(service_version="0.1.0"),
    )


def test_all_keys_present():
    dumped = json.loads(minimum_result().model_dump_json(by_alias=True))
    for path, keys in EXPECTED_KEYS.items():
        node = dumped
        for part in filter(None, path.split(".")):
            node = node[part]
        assert set(keys) <= set(node), f"{path}: missing {set(keys) - set(node)}"


def test_amount_json_number():
    text = minimum_result().model_dump_json(by_alias=True)
    assert '"grossTotal":119.0' in text
    assert '"grossTotal":"' not in text
