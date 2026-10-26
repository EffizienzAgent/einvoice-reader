import sys

import pytest
from conftest import fixture

from einvoice_reader.errors import ReadError
from einvoice_reader.loading import load, parse_xml


def test_xml_cii(xrechnung_cii):
    source = load(xrechnung_cii)
    assert source.container == "XML"
    assert source.root.tag.endswith("CrossIndustryInvoice")
    assert source.embedded_file_name is None


def test_pdf_factur_x():
    source = load(fixture("pdf/en16931.pdf"))
    assert source.container == "PDF"
    assert source.embedded_file_name == "factur-x.xml"
    assert source.xmp is not None


def test_pdf_custom_name():
    source = load(fixture("pdf/zugferd_xrechnung_name.pdf"))
    assert source.embedded_file_name == "xrechnung.xml"


def test_pdf_two_attachments():
    source = load(fixture("pdf/two_attachments.pdf"))
    assert source.embedded_file_name == "factur-x.xml"
    assert source.root.tag.endswith("CrossIndustryInvoice")


@pytest.mark.parametrize("name", ["own/latin1.xml", "own/bom.xml"])
def test_encodings(name):
    source = load(fixture(name))
    names = source.root.xpath("//*[local-name()='SellerTradeParty']/*[local-name()='Name']/text()")
    assert names == ["Müller"]


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "EMPTY_FILE"),
        (b"  \n", "EMPTY_FILE"),
        ("pdf/plain.pdf", "NO_EMBEDDED_XML"),
        ("pdf/broken.pdf", "MALFORMED_PDF"),
        (b"<a>", "NO_EMBEDDED_XML"),
        (b"<hello", "NO_EMBEDDED_XML"),
        (b'<?xml version="1.0"?><a>', "MALFORMED_XML"),
    ],
)
def test_errors(data, code):
    if isinstance(data, str):
        data = fixture(data)
    with pytest.raises(ReadError) as excinfo:
        load(data)
    assert excinfo.value.code == code


def test_xxe_not_resolved(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    xml = f'<!DOCTYPE x [<!ENTITY e SYSTEM "file://{secret}">]><x>&e;</x>'.encode()
    try:
        root = parse_xml(xml)
    except ReadError:
        return
    assert "TOPSECRET" not in "".join(root.itertext())


def pdf_with(attachments: dict[str, bytes], encrypt: str | None = None) -> bytes:
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    for name, content in attachments.items():
        writer.add_attachment(name, content)
    if encrypt:
        writer.encrypt(user_password="", owner_password="owner", algorithm=encrypt)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "data",
    [b"\xff\xd8\xff\xe0\x00\x10JFIF", b"\x89PNG\r\n\x1a\n", b"PK\x03\x04docx", b"hello"],
    ids=["jpeg", "png", "docx", "text"],
)
def test_non_xml_files_go_to_ocr(data):
    with pytest.raises(ReadError) as excinfo:
        load(data)
    assert excinfo.value.code == "NO_EMBEDDED_XML"


def test_pdf_with_leading_garbage():
    with pytest.raises(ReadError) as excinfo:
        load(b"garbage\n" * 10 + fixture("pdf/plain.pdf"))
    assert excinfo.value.code == "NO_EMBEDDED_XML"


@pytest.mark.parametrize("algorithm", ["AES-128", "AES-256"])
def test_encrypted_pdf_with_invoice(algorithm, xrechnung_cii):
    source = load(pdf_with({"factur-x.xml": xrechnung_cii}, encrypt=algorithm))
    assert source.root.tag.endswith("CrossIndustryInvoice")


def test_encrypted_plain_pdf_goes_to_ocr():
    with pytest.raises(ReadError) as excinfo:
        load(pdf_with({}, encrypt="AES-256"))
    assert excinfo.value.code == "NO_EMBEDDED_XML"


def bomb_pdf(count: int, size: int, extra: dict[str, bytes]) -> bytes:
    import io
    import zlib

    from pypdf import PdfWriter
    from pypdf.generic import NameObject, StreamObject

    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    for i in range(count):
        writer.add_attachment(f"bomb{i}.xml", b"x")
    names = writer._root_object["/Names"]["/EmbeddedFiles"]["/Names"]
    packed = zlib.compress(b"<" + b"a" * size, 9)
    for j in range(1, len(names), 2):
        spec = names[j].get_object()
        stream = StreamObject()
        stream._data = packed
        stream[NameObject("/Filter")] = NameObject("/FlateDecode")
        stream[NameObject("/Type")] = NameObject("/EmbeddedFile")
        spec["/EF"][NameObject("/F")] = writer._add_object(stream)
    for name, content in extra.items():
        writer.add_attachment(name, content)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize("size_mb", [20, 70])
def test_decompression_bomb_is_bounded(xrechnung_cii, size_mb):
    import resource

    data = bomb_pdf(40, size_mb * 2**20, {"factur-x.xml": xrechnung_cii})
    assert len(data) < 5 * 2**20
    unit = 1 if sys.platform == "darwin" else 1024
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    source = load(data)
    grown_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) * unit / 2**20
    assert source.embedded_file_name == "factur-x.xml"
    assert grown_mb < 300


def test_bomb_without_invoice_is_422():
    with pytest.raises(ReadError) as excinfo:
        load(bomb_pdf(40, 70 * 2**20, {}))
    assert excinfo.value.code == "NO_EMBEDDED_XML"


def test_second_invoice_xml_warns(xrechnung_cii, xrechnung_ubl):
    source = load(pdf_with({"factur-x.xml": xrechnung_cii, "rechnung.xml": xrechnung_ubl}))
    assert source.embedded_file_name == "factur-x.xml"
    assert any("rechnung.xml" in w for w in source.warnings)


@pytest.mark.parametrize("algorithm", ["AES-128", "AES-256"])
def test_crypt_filter_length_in_bits_is_tolerated(algorithm, xrechnung_cii):
    data = pdf_with({"factur-x.xml": xrechnung_cii}, encrypt=algorithm)
    bytes_length, bits_length = (b"16", b"128") if algorithm == "AES-128" else (b"32", b"256")
    broken = data.replace(
        b"/Length " + bytes_length + b"\n>>", b"/Length " + bits_length + b">>", 1
    )
    assert broken != data and len(broken) == len(data)
    source = load(broken)
    assert source.embedded_file_name == "factur-x.xml"


def test_crypt_filter_length_in_bits_without_invoice_goes_to_ocr():
    data = pdf_with({}, encrypt="AES-128")
    broken = data.replace(b"/Length 16\n>>", b"/Length 128>>", 1)
    with pytest.raises(ReadError) as excinfo:
        load(broken)
    assert excinfo.value.code == "NO_EMBEDDED_XML"
