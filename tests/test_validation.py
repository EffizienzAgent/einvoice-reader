from pathlib import Path

import pytest
from conftest import KOSIT, fixture

from einvoice_reader.detect import FormatInfo, detect
from einvoice_reader.loading import load
from einvoice_reader.validation import rule_keys_for, validate


def check(data: bytes, runner):
    source = load(data)
    return validate(source, detect(source), runner)


@pytest.mark.parametrize(
    "path",
    sorted((KOSIT.parent.parent).rglob("*.xml")),
    ids=lambda p: str(p.relative_to(KOSIT.parent.parent)),
)
def test_valid_kosit_files(path: Path, runner):
    validation, _ = check(path.read_bytes(), runner)
    errors = [m for m in validation.messages if m.severity == "error"]
    assert validation.valid is True, errors[:3]


def test_broken_ubl_invalid(runner):
    validation, _ = check(fixture("own/broken_ubl_no_issue_date.xml"), runner)
    assert validation.valid is False
    assert any(m.rule_id == "XSD" for m in validation.messages)


def test_minimum_valid_but_not_einvoice(runner):
    validation, _ = check(fixture("own/minimum.xml"), runner)
    assert validation.performed is True
    assert validation.valid is True
    assert validation.is_e_invoice is False


def test_xrechnung_is_einvoice(runner, xrechnung_cii):
    validation, _ = check(xrechnung_cii, runner)
    assert validation.is_e_invoice is True
    assert any(name.startswith("XRechnung CII 3.0.2") for name in validation.rule_sets)


def test_old_xrechnung_version_warns(runner, xrechnung_cii):
    old = xrechnung_cii.replace(b"xrechnung_3.0", b"xrechnung_2.3")
    validation, _ = check(old, runner)
    assert any(m.rule_id == "VERSION" and m.severity == "warning" for m in validation.messages)


def fmt(syntax, standard, profile):
    return FormatInfo(syntax, "XML", "Invoice", standard, profile, None, None, None)


@pytest.mark.parametrize(
    ("info", "keys"),
    [
        (fmt("CII", "XRechnung", "XRECHNUNG"), ["en16931-cii", "xrechnung-cii"]),
        (fmt("UBL", "XRechnung", "XRECHNUNG"), ["en16931-ubl", "xrechnung-ubl"]),
        (fmt("CII", "Factur-X", "MINIMUM"), ["facturx-minimum"]),
        (fmt("CII", "ZUGFeRD", "BASIC WL"), ["facturx-basicwl"]),
        (fmt("CII", "Factur-X", "BASIC"), ["facturx-basic"]),
        (fmt("CII", "ZUGFeRD", "EN16931"), ["facturx-en16931"]),
        (fmt("CII", "Factur-X", "EXTENDED"), ["facturx-extended"]),
        (fmt("CII", "EN16931", "EN16931"), ["en16931-cii"]),
        (fmt("UBL", "EN16931", "EN16931"), ["en16931-ubl"]),
        (fmt("UBL", "Peppol", None), ["en16931-ubl"]),
        (fmt("CII", "unknown", None), ["en16931-cii"]),
        (fmt("UBL", "unknown", None), ["en16931-ubl"]),
    ],
)
def test_rule_keys_for(info, keys):
    assert rule_keys_for(info) == keys


class DeadRunner:
    def run(self, xml, rule_keys):
        from einvoice_reader.schematron import RunFailure

        return RunFailure("busy")


def test_runner_down(xrechnung_cii):
    validation, warnings = check(xrechnung_cii, DeadRunner())
    assert validation.performed is False
    assert validation.valid is None
    assert validation.is_e_invoice is None
    assert "validation queue full, retry later" in warnings


def test_zugferd_20_minimum_not_einvoice(runner):
    xml = fixture("own/minimum.xml").replace(
        b"urn:factur-x.eu:1p0:minimum", b"urn:zugferd.de:2p0:minimum"
    )
    validation, _ = check(xml, runner)
    assert validation.is_e_invoice is False
    assert validation.rule_sets[-1] == "Factur-X 1.09 MINIMUM"


def test_xrechnung_cii_uses_full_schema_then_kosit_rules(runner, xrechnung_cii):
    second = b"""<ram:SpecifiedTradePaymentTerms><ram:Description>Netto</ram:Description></ram:SpecifiedTradePaymentTerms>
            <ram:SpecifiedTradeSettlementHeaderMonetarySummation>"""
    xml = xrechnung_cii.replace(b"<ram:SpecifiedTradeSettlementHeaderMonetarySummation>", second, 1)
    validation, _ = check(xml, runner)
    assert not [m for m in validation.messages if m.rule_id == "XSD"]
    assert [m.severity for m in validation.messages if m.rule_id == "CII-SR-452"] == ["error"]


def test_invoice_values_not_logged(runner, xrechnung_cii, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    xml = xrechnung_cii.replace(
        b"<ram:LineTotalAmount>314.86<", b"<ram:LineTotalAmount>CONFIDENTIAL-DE89<", 1
    )
    validation, _ = check(xml, runner)
    assert validation.valid is False
    assert "CONFIDENTIAL" not in caplog.text
