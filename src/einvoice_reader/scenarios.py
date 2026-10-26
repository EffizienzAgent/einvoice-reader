"""KoSIT validator scenarios: which rules apply to which document and how severe each rule is."""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from lxml import etree

KOSIT = Path(__file__).resolve().parent / "rules" / "kosit"
_NS = {"s": "http://www.xoev.de/de/validator/framework/1/scenarios"}
_ROOTS = {
    "rsm:CrossIndustryInvoice": "CII",
    "invoice:Invoice": "UBL-Invoice",
    "creditnote:CreditNote": "UBL-CreditNote",
}
_RULE_FILES = {
    "EN16931-CII-validation.xsl": "en16931-cii",
    "EN16931-UBL-validation.xsl": "en16931-ubl",
    "XRechnung-CII-validation.xsl": "xrechnung-cii",
    "XRechnung-UBL-validation.xsl": "xrechnung-ubl",
}
_LEVELS = {"information": "info", "warning": "warning", "error": "error"}


@dataclass(frozen=True)
class Scenario:
    name: str
    kind: str
    customization_id: str
    rule_keys: list[str]
    levels: dict[str, str]


@cache
def load_scenarios() -> list[Scenario]:
    root = etree.parse(str(KOSIT / "scenarios.xml")).getroot()
    scenarios = []
    for node in root.iterfind("s:scenario", _NS):
        match = node.findtext("s:match", namespaces=_NS)
        kind = next(k for prefix, k in _ROOTS.items() if f"/{prefix}" in match)
        customization_id = re.search(r"'([^']+)'", match).group(1)
        locations = node.xpath(
            "s:validateWithSchematron/s:resource/s:location/text()", namespaces=_NS
        )
        scenarios.append(
            Scenario(
                name=node.findtext("s:name", namespaces=_NS),
                kind=kind,
                customization_id=customization_id,
                rule_keys=[_RULE_FILES[Path(location).name] for location in locations],
                levels={
                    level.text.strip(): _LEVELS[level.get("level")]
                    for level in node.iterfind("s:createReport/s:customLevel", _NS)
                },
            )
        )
    return scenarios


def find_scenario(kind: str, customization_id: str | None) -> Scenario | None:
    return next(
        (
            s
            for s in load_scenarios()
            if s.kind == kind and s.customization_id == (customization_id or "").strip()
        ),
        None,
    )


XRECHNUNG_30 = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
EXTENSION_30 = XRECHNUNG_30 + "#conformant#urn:xeinkauf.de:kosit:extension:xrechnung_3.0"


def fallback_scenario(kind: str, customization_id: str | None) -> Scenario | None:
    """KoSIT levels for XRechnung IDs without an exact scenario (older versions, odd spelling)."""
    value = (customization_id or "").strip().lower()
    if "xrechnung" not in value:
        return None
    target = EXTENSION_30 if "extension" in value else XRECHNUNG_30
    scenario = find_scenario(kind, target)
    if scenario is None and kind == "UBL-CreditNote":
        scenario = find_scenario("UBL-Invoice", target)
    return scenario


def kosit_rule_path(key: str) -> Path:
    name = next(file for file, k in _RULE_FILES.items() if k == key)
    return next(KOSIT.joinpath("resources").rglob(name))
