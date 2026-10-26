import pytest

from einvoice_reader.scenarios import find_scenario

XR = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
EXT = XR + "#conformant#urn:xeinkauf.de:kosit:extension:xrechnung_3.0"
CVD = XR + "#compliant#urn:xeinkauf.de:kosit:xrechnung:cvd_0.9"


def test_xrechnung_cii_levels():
    scenario = find_scenario("CII", XR)
    assert scenario.rule_keys == ["en16931-cii", "xrechnung-cii"]
    assert scenario.levels["CII-SR-452"] == "error"
    assert scenario.levels["BR-CL-21"] == "warning"


def test_extension_ubl_levels():
    scenario = find_scenario("UBL-Invoice", EXT)
    assert scenario.rule_keys == ["en16931-ubl", "xrechnung-ubl"]
    assert scenario.levels["BR-CO-16"] == "info"


def test_cvd_credit_note():
    scenario = find_scenario("UBL-CreditNote", CVD)
    assert scenario.levels["BR-CL-13"] == "info"


def test_plain_en16931():
    scenario = find_scenario("CII", "urn:cen.eu:en16931:2017")
    assert scenario.rule_keys == ["en16931-cii"]
    assert scenario.levels == {}


@pytest.mark.parametrize(
    ("kind", "bt24"), [("CII", "urn:example"), ("UBL-Invoice", None), ("CII", XR + "x")]
)
def test_no_scenario(kind, bt24):
    assert find_scenario(kind, bt24) is None


def test_vendored_rules_match_checksums():
    import hashlib

    from einvoice_reader.scenarios import KOSIT

    for line in (KOSIT / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        assert hashlib.sha256((KOSIT / name).read_bytes()).hexdigest() == digest, name
