import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import fixture
from fastapi.testclient import TestClient

from einvoice_reader.api import create_app
from einvoice_reader.scenarios import KOSIT
from einvoice_reader.settings import Settings

PARSE = "/v1/invoices/parse"


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app(Settings())) as client:
        yield client


def post_file(client, data: bytes, name="invoice", params=None):
    return client.post(PARSE, files={"file": (name, data)}, params=params)


def test_parse_multipart_pdf(client):
    response = post_file(client, fixture("pdf/en16931.pdf"), "invoice.pdf")
    assert response.status_code == 200
    body = response.json()
    assert body["format"]["container"] == "PDF"
    assert body["invoice"]["number"]
    assert body["validation"]["performed"] is True
    assert body["validation"]["valid"] is True
    assert body["xml"].lstrip().startswith("<")
    assert body["meta"]["serviceVersion"]


def test_parse_raw_xml(client, xrechnung_ubl):
    response = client.post(
        PARSE, content=xrechnung_ubl, headers={"content-type": "application/xml"}
    )
    assert response.status_code == 200
    assert response.json()["format"]["syntax"] == "UBL"


def test_latin1_xml_returned_as_text(client):
    response = client.post(
        PARSE, content=fixture("own/latin1.xml"), headers={"content-type": "text/xml"}
    )
    assert response.status_code == 200
    assert "Müller" in response.json()["xml"]


def test_include_xml_false(client, xrechnung_cii):
    response = post_file(client, xrechnung_cii, params={"include_xml": "false"})
    assert response.json()["xml"] is None


def test_validate_false(client, xrechnung_cii):
    response = post_file(client, xrechnung_cii, params={"validate": "false"})
    assert response.json()["validation"]["performed"] is False


def test_invalid_still_200(client):
    response = post_file(client, fixture("own/broken_ubl_no_issue_date.xml"))
    assert response.status_code == 200
    assert response.json()["validation"]["valid"] is False


@pytest.mark.parametrize(
    ("data", "code"),
    [
        ("pdf/plain.pdf", "NO_EMBEDDED_XML"),
        (b"", "EMPTY_FILE"),
        (b"<a>", "NO_EMBEDDED_XML"),
        (b'<?xml version="1.0"?><a>', "MALFORMED_XML"),
        ("own/zugferd1.xml", "UNSUPPORTED_FORMAT"),
    ],
)
def test_422_codes(client, data, code):
    if isinstance(data, str):
        data = fixture(data)
    response = post_file(client, data)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


def test_missing_file_field(client):
    response = client.post(PARSE, files={"other": ("other.xml", b"<x/>")})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMPTY_FILE"


def test_413():
    with TestClient(create_app(Settings(max_upload_mb=1))) as client:
        raw = client.post(
            PARSE, content=b"x" * (2**20 + 1), headers={"content-type": "application/xml"}
        )
        multipart = post_file(client, b"x" * (2**20 + 1))
    assert raw.status_code == 413
    assert multipart.status_code == 413


def test_api_key(xrechnung_cii):
    with TestClient(create_app(Settings(api_key="k"))) as client:
        assert post_file(client, xrechnung_cii).status_code == 401
        ok = client.post(PARSE, files={"file": ("x", xrechnung_cii)}, headers={"X-API-Key": "k"})
        assert ok.status_code == 200
        assert client.get("/v1/health").status_code == 200
        assert client.get("/v1/info").status_code == 401


def test_parallel_requests(client):
    pdf = fixture("pdf/en16931.pdf")
    with ThreadPoolExecutor(10) as pool:
        responses = list(pool.map(lambda _: post_file(client, pdf), range(10)))
    assert [r.status_code for r in responses] == [200] * 10
    assert all(r.json()["validation"]["performed"] for r in responses)


def test_info(client):
    body = client.get("/v1/info").json()
    assert body["ruleSets"]["kosit"] == (KOSIT / "VERSION").read_text().strip()
    assert body["ruleSets"]["facturx"] == "1.09"
    assert body["serviceVersion"]


def test_doctype_rejected_before_validation(client, xrechnung_ubl, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    doctype = f'<!DOCTYPE x [<!ENTITY % s SYSTEM "file://{secret}">]>\n'.encode()
    body = xrechnung_ubl.replace(b"?>", b"?>\n" + doctype, 1)
    assert doctype in body
    response = client.post(PARSE, content=body, headers={"content-type": "application/xml"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MALFORMED_XML"


def test_utf16_xml_validates(client, xrechnung_cii):
    text = xrechnung_cii.decode().replace('encoding="UTF-8"', 'encoding="UTF-16"')
    response = client.post(
        PARSE, content=text.encode("utf-16"), headers={"content-type": "application/xml"}
    )
    assert response.status_code == 200
    assert response.json()["validation"]["valid"] is True


def test_latin1_reaches_validation_intact(client):
    response = client.post(
        PARSE, content=fixture("own/latin1.xml"), headers={"content-type": "text/xml"}
    )
    body = response.json()
    assert body["validation"]["valid"] is True
    assert body["invoice"]["seller"]["name"] == "Müller"


def test_422_format_is_camel_case(monkeypatch):
    from einvoice_reader.errors import ReadError
    from einvoice_reader.service import read_invoice

    def broken(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr("einvoice_reader.service.map_invoice", broken)
    with pytest.raises(ReadError) as excinfo:
        read_invoice(fixture("own/minimum.xml"), validate=False, include_xml=False, runner=None)
    assert excinfo.value.code == "READ_FAILED"
    assert "documentType" in excinfo.value.format


def test_malformed_multipart_is_400(client):
    response = client.post(
        PARSE, content=b"garbage", headers={"content-type": "multipart/form-data"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"


@pytest.mark.parametrize(
    "xml_fixture", ["own/minimum.xml", "kosit/business-cases/standard/01.01a-INVOICE_ubl.xml"]
)
def test_unknown_bt24_still_parses(client, xml_fixture):
    data = fixture(xml_fixture)
    for urn in (
        b"urn:factur-x.eu:1p0:minimum",
        b"urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0",
    ):
        data = data.replace(urn, b"urn:example:custom")
    response = post_file(client, data)
    assert response.status_code == 200
    body = response.json()
    assert body["format"]["standard"] == "unknown"
    assert body["invoice"]["number"]
    assert body["validation"]["performed"] is True


INJECTION = "X' or count(//*[count(//*[count(//*)=-1])=-1])=-1 or 'a'='b"


def test_currency_xpath_injection_is_fast(client, xrechnung_cii):
    filler = b"<ram:IncludedNote><ram:Content>a</ram:Content></ram:IncludedNote>" * 400
    xml = xrechnung_cii.replace(
        b"<ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>",
        f"<ram:InvoiceCurrencyCode>{INJECTION}</ram:InvoiceCurrencyCode>".encode(),
        1,
    ).replace(b"</rsm:ExchangedDocument>", filler + b"</rsm:ExchangedDocument>", 1)
    start = time.monotonic()
    response = post_file(client, xml, params={"validate": "false"})
    assert time.monotonic() - start < 3
    assert response.status_code == 200
    body = response.json()
    assert body["invoice"]["currency"] is None
    assert any("BT-5" in w for w in body["warnings"])


def test_parse_timeout_returns_422(xrechnung_cii):
    settings = Settings(parse_timeout_s=0.001)
    with TestClient(create_app(settings)) as client:
        response = post_file(client, xrechnung_cii)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROCESSING_TIMEOUT"


def test_docs_hidden_when_api_key_set():
    with TestClient(create_app(Settings(api_key="k"))) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_security_headers(client):
    response = client.get("/v1/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_exotic_declared_encoding_is_not_500(client, xrechnung_cii):
    xml = xrechnung_cii.replace(b'encoding="UTF-8"', b'encoding="ARMSCII-8"', 1)
    response = post_file(client, xml, params={"validate": "false"})
    assert response.status_code in (200, 422)


def test_two_currency_codes_still_read(client, xrechnung_cii):
    xml = xrechnung_cii.replace(
        b"<ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>",
        b"<ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode><ram:InvoiceCurrencyCode>USD</ram:InvoiceCurrencyCode>",
        1,
    )
    response = post_file(client, xml, params={"validate": "false"})
    assert response.status_code == 200
    assert response.json()["invoice"]["currency"] == "EUR"
    assert any("BT-5" in w for w in response.json()["warnings"])


def test_slow_upload_times_out():
    import socket
    import threading

    import uvicorn

    app = create_app(Settings(read_timeout_s=0.5))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
            conn.sendall(
                b"POST /v1/invoices/parse HTTP/1.1\r\nHost: x\r\n"
                b"Content-Type: application/xml\r\nContent-Length: 1000\r\n\r\n<"
            )
            response = ""
            while "REQUEST_TIMEOUT" not in response:
                chunk = conn.recv(4096).decode()
                if not chunk:
                    break
                response += chunk
        assert response.startswith("HTTP/1.1 408")
        assert "REQUEST_TIMEOUT" in response
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_memory_error_is_processing_failed(monkeypatch):
    from einvoice_reader.errors import ReadError
    from einvoice_reader.service import parse_document

    def exhausted(*args, **kwargs):
        raise MemoryError

    monkeypatch.setattr("einvoice_reader.service.map_invoice", exhausted)
    with pytest.raises(ReadError) as excinfo:
        parse_document(fixture("own/minimum.xml"), validate=False)
    assert excinfo.value.code == "PROCESSING_FAILED"
