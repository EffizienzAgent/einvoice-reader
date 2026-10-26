import pytest

from einvoice_reader.detect import detect
from einvoice_reader.loading import load
from einvoice_reader.mapping import map_invoice, normalize_bic, normalize_iban


def read(data: bytes):
    source = load(data)
    return map_invoice(source, detect(source))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DE34 1207 0024 0556 0420 00", "DE34120700240556042000"),
        (" de34120700240556042000 ", "DE34120700240556042000"),
        ("DE34 1207 0024 0556 0420 00", "DE34120700240556042000"),
        ("1234567890", "1234567890"),
        ("ACC 4711-X", "ACC 4711-X"),
    ],
)
def test_normalize_iban(raw, expected):
    assert normalize_iban(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"), [("deutdedb160", "DEUTDEDB160"), (" GENO DE51 DRE ", "GENODE51DRE")]
)
def test_normalize_bic(raw, expected):
    assert normalize_bic(raw) == expected


def test_accounts_are_normalised(xrechnung_cii):
    xml = xrechnung_cii.replace(
        b"<ram:IBANID>DE79000000001234567890</ram:IBANID>",
        b"<ram:IBANID>de79 0000 0000 1234 5678 90</ram:IBANID>",
        1,
    )
    invoice, warnings = read(xml)
    assert invoice.payment.accounts[0].iban == "DE79000000001234567890"
    assert not any("checksum" in w for w in warnings)


def test_invalid_iban_checksum_warns(xrechnung_cii):
    xml = xrechnung_cii.replace(b"DE79000000001234567890", b"DE78000000001234567890", 1)
    invoice, warnings = read(xml)
    assert invoice.payment.accounts[0].iban == "DE78000000001234567890"
    assert any("DE78000000001234567890" in w and "checksum" in w for w in warnings)
