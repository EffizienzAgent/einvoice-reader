import json

import pytest
from conftest import fixture

from einvoice_reader.detect import detect
from einvoice_reader.loading import load
from einvoice_reader.mapping import map_invoice

CII_MEANS = b"""</ram:SpecifiedTradeSettlementPaymentMeans>
            <ram:SpecifiedTradeSettlementPaymentMeans>
                <ram:TypeCode>58</ram:TypeCode>
                <ram:PayeePartyCreditorFinancialAccount>
                    <ram:IBANID>DE02120300000000202051</ram:IBANID>
                    <ram:AccountName>Second</ram:AccountName>
                </ram:PayeePartyCreditorFinancialAccount>
                <ram:PayeeSpecifiedCreditorFinancialInstitution>
                    <ram:BICID>BYLADEM1001</ram:BICID>
                </ram:PayeeSpecifiedCreditorFinancialInstitution>
            </ram:SpecifiedTradeSettlementPaymentMeans>"""
UBL_MEANS = b"""<cac:PaymentMeans>
        <cbc:PaymentMeansCode>58</cbc:PaymentMeansCode>
        <cac:PayeeFinancialAccount>
            <cbc:ID>DE02120300000000202051</cbc:ID>
            <cbc:Name>Second</cbc:Name>
            <cac:FinancialInstitutionBranch><cbc:ID>BYLADEM1001</cbc:ID></cac:FinancialInstitutionBranch>
        </cac:PayeeFinancialAccount>
    </cac:PaymentMeans>
    <cac:PaymentTerms>"""


def read(data: bytes):
    source = load(data)
    return map_invoice(source, detect(source))


def no_stringified_lists(model) -> bool:
    return "\"['" not in model.model_dump_json()


def test_two_bank_accounts_cii(xrechnung_cii):
    xml = xrechnung_cii.replace(b"</ram:SpecifiedTradeSettlementPaymentMeans>", CII_MEANS, 1)
    invoice, warnings = read(xml)
    assert [a.iban for a in invoice.payment.accounts] == [
        "DE79000000001234567890",
        "DE02120300000000202051",
    ]
    assert invoice.payment.accounts[1].bic == "BYLADEM1001"
    assert invoice.payment.accounts[1].name == "Second"
    assert invoice.payment.means_code == "58"
    assert no_stringified_lists(invoice)


def test_two_bank_accounts_ubl(xrechnung_ubl):
    xml = xrechnung_ubl.replace(b"<cac:PaymentTerms>", UBL_MEANS, 1)
    invoice, _ = read(xml)
    assert [a.iban for a in invoice.payment.accounts] == [
        "DE79000000001234567890",
        "DE02120300000000202051",
    ]
    assert invoice.payment.accounts[1].bic == "BYLADEM1001"
    assert no_stringified_lists(invoice)


def test_two_payment_terms_cii(xrechnung_cii):
    second = b"""<ram:SpecifiedTradePaymentTerms>
                <ram:Description>Skonto 2%</ram:Description>
                <ram:DueDateDateTime><udt:DateTimeString format="102">20260115</udt:DateTimeString></ram:DueDateDateTime>
            </ram:SpecifiedTradePaymentTerms>
            <ram:SpecifiedTradePaymentTerms>
                <ram:Description>Netto</ram:Description>
                <ram:DueDateDateTime><udt:DateTimeString format="102">20260130</udt:DateTimeString></ram:DueDateDateTime>
            </ram:SpecifiedTradePaymentTerms>"""
    start = xrechnung_cii.index(b"<ram:SpecifiedTradePaymentTerms>")
    end = xrechnung_cii.index(b"</ram:SpecifiedTradePaymentTerms>") + len(
        b"</ram:SpecifiedTradePaymentTerms>"
    )
    invoice, warnings = read(xrechnung_cii[:start] + second + xrechnung_cii[end:])
    assert invoice.due_date == "2026-01-15"
    assert invoice.payment_terms == "Skonto 2%\nNetto"
    assert any("BT-9" in w for w in warnings)
    assert no_stringified_lists(invoice)


@pytest.mark.parametrize(
    ("original", "replacement", "expected"),
    [
        (b'format="102">20160404<', b'format="102">2016-04-04<', "2016-04-04"),
    ],
)
def test_iso_date_in_102_field(xrechnung_cii, original, replacement, expected):
    invoice, warnings = read(xrechnung_cii.replace(original, replacement, 1))
    assert invoice.issue_date == expected


def test_period_formats(xrechnung_cii):
    period = b"""<ram:BillingSpecifiedPeriod>
                <ram:StartDateTime><udt:DateTimeString format="610">201601</udt:DateTimeString></ram:StartDateTime>
                <ram:EndDateTime><udt:DateTimeString format="616">201605</udt:DateTimeString></ram:EndDateTime>
            </ram:BillingSpecifiedPeriod>
            <ram:SpecifiedTradePaymentTerms>"""
    invoice, warnings = read(xrechnung_cii.replace(b"<ram:SpecifiedTradePaymentTerms>", period, 1))
    assert invoice.invoicing_period.start == "2016-01-01"
    assert invoice.invoicing_period.end == "2016-02-01"
    assert sum("format" in w for w in warnings) == 2


def test_broken_optional_date_is_dropped(xrechnung_cii):
    due = b"""<ram:SpecifiedTradePaymentTerms>
                <ram:DueDateDateTime><udt:DateTimeString format="102">2016-13-45</udt:DateTimeString></ram:DueDateDateTime>"""
    invoice, warnings = read(xrechnung_cii.replace(b"<ram:SpecifiedTradePaymentTerms>", due, 1))
    assert invoice.due_date is None
    assert invoice.number
    assert any("2016-13-45" in w for w in warnings)


@pytest.mark.parametrize("suffix", [b"Z", b"+01:00"])
def test_ubl_date_with_timezone(xrechnung_ubl, suffix):
    invoice, _ = read(
        xrechnung_ubl.replace(
            b"<cbc:IssueDate>2016-04-04<", b"<cbc:IssueDate>2016-04-04" + suffix + b"<", 1
        )
    )
    assert invoice.issue_date == "2016-04-04"


def test_ubl_tax_number(xrechnung_ubl):
    fc = b"""<cac:PartyTaxScheme>
                <cbc:CompanyID>123/456/7890</cbc:CompanyID>
                <cac:TaxScheme><cbc:ID>FC</cbc:ID></cac:TaxScheme>
            </cac:PartyTaxScheme>
            <cac:PartyLegalEntity>"""
    invoice, _ = read(xrechnung_ubl.replace(b"<cac:PartyLegalEntity>", fc, 1))
    assert invoice.seller.tax_number == "123/456/7890"
    assert invoice.seller.vat_id == "DE 123456789"


def test_sub_lines_cii(xrechnung_cii):
    xml = xrechnung_cii.replace(
        b"<ram:LineID>Porto + Versandkosten</ram:LineID>",
        b"<ram:LineID>Porto + Versandkosten</ram:LineID><ram:ParentLineID>Zeitschrift [...]</ram:ParentLineID>"
        b"<ram:LineStatusReasonCode>DETAIL</ram:LineStatusReasonCode>",
        1,
    )
    invoice, warnings = read(xml)
    child = next(line for line in invoice.lines if line.id == "Porto + Versandkosten")
    parent = next(line for line in invoice.lines if line.id == "Zeitschrift [...]")
    assert (child.parent_line_id, child.line_status_reason_code) == ("Zeitschrift [...]", "DETAIL")
    assert parent.parent_line_id is None
    assert any("sub-lines" in w for w in warnings)


@pytest.mark.parametrize(
    ("code", "kind", "credit"),
    [
        ("380", "invoice", False),
        ("381", "credit_note", True),
        ("261", "credit_note", True),
        ("396", "credit_note", True),
        ("875", "invoice", False),
        ("326", "invoice", False),
        ("384", "corrected", False),
        ("386", "prepayment", False),
        ("389", "self_billed", False),
        ("999", "other", False),
    ],
)
def test_type_codes(xrechnung_cii, code, kind, credit):
    invoice, _ = read(
        xrechnung_cii.replace(
            b"<ram:TypeCode>380</ram:TypeCode>", f"<ram:TypeCode>{code}</ram:TypeCode>".encode(), 1
        )
    )
    assert (invoice.type, invoice.is_credit_note) == (kind, credit)


def test_unparseable_amount_warns(xrechnung_cii):
    invoice, warnings = read(
        xrechnung_cii.replace(b"<ram:GrandTotalAmount>336.9<", b"<ram:GrandTotalAmount>336,90<", 1)
    )
    assert invoice.totals.gross_total is None
    assert any("BT-112" in w for w in warnings)


def test_amount_due_mismatch_warns(xrechnung_cii):
    _, warnings = read(
        xrechnung_cii.replace(b"<ram:DuePayableAmount>336.9<", b"<ram:DuePayableAmount>300<", 1)
    )
    assert any("BT-115" in w for w in warnings)


def test_versions():
    source = load(fixture("kosit/business-cases/standard/01.01a-INVOICE_uncefact.xml"))
    assert detect(source).version == "3.0"
    pdf = detect(load(fixture("pdf/minimum.pdf")))
    assert pdf.standard == "Factur-X"
    assert pdf.version is None


def test_golden_files_have_no_stringified_lists():
    from conftest import FIXTURES

    golden = FIXTURES.parent / "golden"
    offenders = [p.name for p in golden.rglob("*.json") if "\"['" in p.read_text()]
    assert offenders == []


def test_result_json_serializable(xrechnung_cii):
    invoice, _ = read(xrechnung_cii)
    json.loads(invoice.model_dump_json(by_alias=True))
