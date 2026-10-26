import io
import os

import facturx
import pytest
from conftest import FIXTURES, fixture
from pypdf import PdfWriter
from test_loading import pdf_with

from einvoice_reader.detect import detect
from einvoice_reader.errors import ReadError
from einvoice_reader.isolation import PoolCrashed, WorkerPool
from einvoice_reader.loading import load
from einvoice_reader.mapping import map_invoice
from einvoice_reader.settings import Settings
from einvoice_reader.validation import rule_keys_for, validate

XR30 = b"urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
EXTENSION = XR30 + b"#conformant#urn:xeinkauf.de:kosit:extension:xrechnung_3.0"
COMPREHENSIVE = "kosit/technical-cases/cius/01.03_comprehensive_test_{}.xml"


def read(data: bytes):
    source = load(data)
    return map_invoice(source, detect(source))


def check(data: bytes, runner):
    source = load(data)
    return validate(source, detect(source), runner)


def header_settlement(xml: bytes, old: bytes, new: bytes) -> bytes:
    head, tail = xml.split(b"<ram:ApplicableHeaderTradeSettlement>", 1)
    return head + b"<ram:ApplicableHeaderTradeSettlement>" + tail.replace(old, new, 1)


def blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# H1


def test_bad_base64_attachment_does_not_lose_invoice(xrechnung_cii):
    attachment = b"""<ram:AdditionalReferencedDocument>
                <ram:IssuerAssignedID>A1</ram:IssuerAssignedID>
                <ram:TypeCode>916</ram:TypeCode>
                <ram:AttachmentBinaryObject mimeCode="application/pdf" filename="a.pdf">JVBERi0xLjQ</ram:AttachmentBinaryObject>
            </ram:AdditionalReferencedDocument>
        </ram:ApplicableHeaderTradeAgreement>"""
    invoice, _ = read(
        xrechnung_cii.replace(b"</ram:ApplicableHeaderTradeAgreement>", attachment, 1)
    )
    assert invoice.number
    assert invoice.attachments[0].file_name == "a.pdf"


def test_tax_point_date_string_iso(xrechnung_cii):
    xml = header_settlement(
        xrechnung_cii,
        b"<ram:RateApplicablePercent>",
        b'<ram:TaxPointDate><udt:DateString format="102">2026-10-20</udt:DateString></ram:TaxPointDate>'
        b"<ram:RateApplicablePercent>",
    )
    invoice, _ = read(xml)
    assert invoice.tax_point_date == "2026-10-20"


def test_missing_currency_element(xrechnung_cii):
    invoice, warnings = read(
        xrechnung_cii.replace(b"<ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>", b"", 1)
    )
    assert invoice.currency is None
    assert invoice.totals.gross_total is not None
    assert "BT-5 missing" in warnings


# M1


def test_ubl_sub_line_never_marks_top_level_line(xrechnung_ubl):
    sub = b"""<cac:SubInvoiceLine>
            <cbc:ID>Porto + Versandkosten</cbc:ID>
            <cbc:InvoicedQuantity unitCode="XPP">1</cbc:InvoicedQuantity>
            <cbc:LineExtensionAmount currencyID="EUR">1.00</cbc:LineExtensionAmount>
        </cac:SubInvoiceLine>
    </cac:InvoiceLine>"""
    invoice, warnings = read(xrechnung_ubl.replace(b"</cac:InvoiceLine>", sub, 1))
    assert all(line.parent_line_id is None for line in invoice.lines)
    assert any("SubInvoiceLine" in w for w in warnings)
    assert not any("parentLineId" in w for w in warnings)


def test_ubl_extension_fixture_has_no_parent_warning():
    _, warnings = read(fixture("kosit/business-cases/extension/04.01a-INVOICE_ubl.xml"))
    assert not any("parentLineId" in w for w in warnings)


# M2


@pytest.mark.parametrize(
    "replacement",
    [b"xrechnung_2.3", b"XRECHNUNG_3.0"],
)
def test_xrechnung_fallback_uses_kosit_levels(runner, xrechnung_cii, replacement):
    xml = xrechnung_cii.replace(b'unitCode="XPP"', b'unitCode="Stk"')
    if replacement == b"XRECHNUNG_3.0":
        xml = xml.replace(b"xeinkauf.de:kosit:xrechnung_3.0", b"XEINKAUF.de:kosit:xrechnung_3.0")
    else:
        xml = xml.replace(b"xrechnung_3.0", replacement)
    validation, _ = check(xml, runner)
    assert [m.severity for m in validation.messages if m.rule_id == "BR-CL-23"] == [
        "warning",
        "warning",
    ]


def test_credit_note_extension_uses_extension_levels(runner):
    xml = (
        fixture("own/credit_note_ubl.xml")
        .replace(XR30, EXTENSION, 1)
        .replace(b'unitCode="XPP"', b'unitCode="Stk"')
    )
    validation, _ = check(xml, runner)
    severities = {m.severity for m in validation.messages if m.rule_id == "BR-CL-23"}
    assert severities == {"warning"}


# M3


def test_worker_crash_raises_pool_crashed():
    pool = WorkerPool("test", 1, timeout_s=5)
    try:
        with pytest.raises(PoolCrashed):
            pool.call(os._exit, 1)
        assert pool.call(abs, -2) == 2
    finally:
        pool.close()


def test_service_maps_crash_to_processing_failed(xrechnung_cii):
    from einvoice_reader.service import read_invoice

    class CrashingParser:
        timeout_s = 1

        def call(self, *args):
            raise PoolCrashed

    with pytest.raises(ReadError) as excinfo:
        read_invoice(
            xrechnung_cii, validate=False, include_xml=False, runner=None, parser=CrashingParser()
        )
    assert excinfo.value.code == "PROCESSING_FAILED"


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        ("timeout", "validation timed out for this document"),
        ("busy", "validation queue full, retry later"),
        ("failed", "validation failed for this document"),
    ],
)
def test_validation_failure_reasons(xrechnung_cii, reason, text):
    from einvoice_reader.schematron import RunFailure

    class Runner:
        def run(self, xml, keys):
            return RunFailure(reason)

    validation, warnings = check(xrechnung_cii, Runner())
    assert validation.performed is False
    assert warnings == [text]


# M4


def test_booking_reference_fields():
    invoice, _ = read(fixture(COMPREHENSIVE.format("uncefact")))
    assert invoice.invoiced_object_identifier.value == "OK987654321"
    assert invoice.buyer_accounting_reference
    assert invoice.receiving_advice_reference
    assert invoice.despatch_advice_reference
    assert invoice.tender_reference
    assert invoice.business_process
    assert invoice.payment.means_text == "[Payment means text]"
    line = invoice.lines[0]
    assert line.accounting_reference == "Konto 1"
    assert (line.object_identifier.value, line.object_identifier.scheme) == ("ANG987654321", "ABZ")
    assert [(a.name, a.value) for a in line.attributes] == [("[Description]", "[Value]")]
    assert [(c.value, c.scheme, c.version) for c in line.classifications] == [
        ("0721-880X", "IB", "88")
    ]


# M5


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"<html><body>Rechnung<br></body></html>", "NO_EMBEDDED_XML"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "NO_EMBEDDED_XML"),
        (b"<order/>", "NO_EMBEDDED_XML"),
        (b'<?xml version="1.0"?><a>', "MALFORMED_XML"),
        (
            b'<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100">',
            "MALFORMED_XML",
        ),
        (
            b'<x:Order xmlns:x="urn:oasis:names:specification:ubl:schema:xsd:Order-2"/>',
            "UNSUPPORTED_FORMAT",
        ),
    ],
)
def test_non_invoice_routing(data, code):
    with pytest.raises(ReadError) as excinfo:
        detect(load(data))
    assert excinfo.value.code == code


# M6


def test_factur_x_pdf_with_kosit_scenario_uses_full_schema(runner, xrechnung_cii):
    second = b"""<ram:SpecifiedTradePaymentTerms><ram:Description>Netto</ram:Description></ram:SpecifiedTradePaymentTerms>
            <ram:SpecifiedTradeSettlementHeaderMonetarySummation>"""
    xml = xrechnung_cii.replace(XR30, b"urn:cen.eu:en16931:2017", 1).replace(
        b"<ram:SpecifiedTradeSettlementHeaderMonetarySummation>", second, 1
    )
    pdf = facturx.generate_from_binary(
        blank_pdf(), xml, flavor="factur-x", level="en16931", check_xsd=False
    )
    validation, _ = check(pdf, runner)
    assert not [m for m in validation.messages if m.rule_id == "XSD"]
    assert validation.valid is True


# M7


def test_parse_timeout_default():
    assert Settings().parse_timeout_s == 30


# L1


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        ("325", "other"),
        ("331", "invoice"),
        ("382", "invoice"),
        ("817", "invoice"),
        ("870", "invoice"),
    ],
)
def test_more_type_codes(xrechnung_cii, code, kind):
    invoice, _ = read(
        xrechnung_cii.replace(
            b"<ram:TypeCode>380</ram:TypeCode>", f"<ram:TypeCode>{code}</ram:TypeCode>".encode(), 1
        )
    )
    assert invoice.type == kind


# L3


def test_repeat_warning_names_party(xrechnung_cii):
    second = b"""<ram:SpecifiedTaxRegistration>
                    <ram:ID schemeID="VA">DE 999999999</ram:ID>
                </ram:SpecifiedTaxRegistration>
            </ram:SellerTradeParty>"""
    _, warnings = read(xrechnung_cii.replace(b"</ram:SellerTradeParty>", second, 1))
    assert any(w.startswith("Seller ") and "appears 2 times" in w for w in warnings)


# L5


def test_customization_id_verbatim(xrechnung_cii):
    fmt = detect(load(xrechnung_cii.replace(XR30, b" " + XR30 + b"\n", 1)))
    assert fmt.customization_id == " " + XR30.decode() + "\n"
    assert fmt.standard == "XRechnung"
    assert rule_keys_for(fmt) == ["en16931-cii", "xrechnung-cii"]


# L6


def test_broken_known_attachment_is_named(xrechnung_cii):
    with pytest.raises(ReadError) as excinfo:
        load(pdf_with({"factur-x.xml": xrechnung_cii[:500]}))
    assert excinfo.value.code == "NO_EMBEDDED_XML"
    assert "factur-x.xml" in excinfo.value.message


# L7


def test_prefers_full_invoice_over_minimum(xrechnung_cii):
    source = load(
        pdf_with({"factur-x.xml": fixture("own/minimum.xml"), "xrechnung.xml": xrechnung_cii})
    )
    assert source.embedded_file_name == "xrechnung.xml"


def test_fixtures_dir_exists():
    assert FIXTURES.is_dir()
