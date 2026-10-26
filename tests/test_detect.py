import pytest
from conftest import fixture

from einvoice_reader.detect import detect
from einvoice_reader.errors import ReadError
from einvoice_reader.loading import load


def detect_bytes(data: bytes):
    return detect(load(data))


def test_xrechnung_cii(xrechnung_cii):
    fmt = detect_bytes(xrechnung_cii)
    assert (fmt.syntax, fmt.container, fmt.document_type, fmt.standard, fmt.profile) == (
        "CII",
        "XML",
        "Invoice",
        "XRechnung",
        "XRECHNUNG",
    )
    assert (
        fmt.customization_id
        == "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
    )


def test_xrechnung_ubl(xrechnung_ubl):
    fmt = detect_bytes(xrechnung_ubl)
    assert fmt.syntax == "UBL"
    assert fmt.standard == "XRechnung"
    assert fmt.document_type == "Invoice"


def test_credit_note():
    fmt = detect_bytes(fixture("own/credit_note_ubl.xml"))
    assert fmt.document_type == "CreditNote"
    assert fmt.syntax == "UBL"


def test_minimum():
    fmt = detect_bytes(fixture("own/minimum.xml"))
    assert (fmt.standard, fmt.profile, fmt.version) == ("Factur-X", "MINIMUM", None)


def test_basicwl():
    assert detect_bytes(fixture("own/basicwl.xml")).profile == "BASIC WL"


def test_pdf_xmp():
    fmt = detect_bytes(fixture("pdf/minimum.pdf"))
    assert fmt.container == "PDF"
    assert fmt.standard == "Factur-X"
    assert fmt.profile == "MINIMUM"
    assert fmt.version is None
    assert fmt.embedded_file_name == "factur-x.xml"


def test_xrechnung_inside_pdf_stays_xrechnung():
    fmt = detect_bytes(fixture("pdf/en16931.pdf"))
    assert fmt.standard == "XRechnung"
    assert fmt.container == "PDF"


@pytest.mark.parametrize("name", ["own/zugferd1.xml"])
def test_unsupported(name):
    with pytest.raises(ReadError) as excinfo:
        detect_bytes(fixture(name))
    assert excinfo.value.code == "UNSUPPORTED_FORMAT"


@pytest.mark.parametrize(
    ("bt24", "standard", "profile"),
    [
        ("urn:fdc:peppol.eu:2017:poacc:billing:3.0", "Peppol", None),
        ("urn:cen.eu:en16931:2017#compliant#urn:factur-x.eu:1p0:basic", "Factur-X", "BASIC"),
        ("urn:cen.eu:en16931:2017#conformant#urn:factur-x.eu:1p0:extended", "Factur-X", "EXTENDED"),
        ("urn:cen.eu:en16931:2017", "EN16931", "EN16931"),
        ("urn:example:something", "unknown", None),
    ],
)
def test_bt24_table(bt24, standard, profile):
    xml = fixture("own/minimum.xml").replace(b"urn:factur-x.eu:1p0:minimum", bt24.encode())
    fmt = detect_bytes(xml)
    assert (fmt.standard, fmt.profile) == (standard, profile)


@pytest.mark.parametrize(
    ("urn", "profile"),
    [
        ("urn:zugferd.de:2p0:minimum", "MINIMUM"),
        ("urn:zugferd.de:2p0:basicwl", "BASIC WL"),
        ("urn:cen.eu:en16931:2017#compliant#urn:zugferd.de:2p0:basic", "BASIC"),
        ("urn:cen.eu:en16931:2017#conformant#urn:zugferd.de:2p0:extended", "EXTENDED"),
    ],
)
def test_zugferd_20_urns(urn, profile):
    xml = fixture("own/minimum.xml").replace(b"urn:factur-x.eu:1p0:minimum", urn.encode())
    fmt = detect_bytes(xml)
    assert (fmt.standard, fmt.profile, fmt.version) == ("ZUGFeRD", profile, "2.0")


def test_foreign_xml_goes_to_ocr():
    with pytest.raises(ReadError) as excinfo:
        detect_bytes(fixture("own/not_an_invoice.xml"))
    assert excinfo.value.code == "NO_EMBEDDED_XML"
