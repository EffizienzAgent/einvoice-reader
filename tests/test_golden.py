import json
import os
from pathlib import Path

import pytest
from conftest import FIXTURES

from einvoice_reader.detect import detect
from einvoice_reader.loading import load
from einvoice_reader.mapping import map_invoice

GOLDEN = Path(__file__).parent / "golden"
CASES = sorted((FIXTURES / "kosit").rglob("*.xml"))


@pytest.mark.parametrize("path", CASES, ids=lambda p: str(p.relative_to(FIXTURES / "kosit")))
def test_golden(path):
    source = load(path.read_bytes())
    invoice, _ = map_invoice(source, detect(source))
    actual = json.loads(invoice.model_dump_json(by_alias=True))
    golden = GOLDEN / path.relative_to(FIXTURES / "kosit").with_suffix(".json")
    if os.environ.get("UPDATE_GOLDEN"):
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n")
    assert actual == json.loads(golden.read_text())
