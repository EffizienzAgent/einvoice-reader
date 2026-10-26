from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
KOSIT = FIXTURES / "kosit" / "business-cases" / "standard"


def fixture(path: str) -> bytes:
    return (FIXTURES / path).read_bytes()


@pytest.fixture
def xrechnung_cii() -> bytes:
    return (KOSIT / "01.01a-INVOICE_uncefact.xml").read_bytes()


@pytest.fixture
def xrechnung_ubl() -> bytes:
    return (KOSIT / "01.01a-INVOICE_ubl.xml").read_bytes()


@pytest.fixture(scope="session")
def runner():
    from einvoice_reader.schematron import SchematronRunner

    runner = SchematronRunner(timeout_s=60)
    yield runner
    runner.close()
