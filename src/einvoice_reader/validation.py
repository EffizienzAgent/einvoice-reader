import re

import facturx

from einvoice_reader.detect import FormatInfo, facturx_options
from einvoice_reader.loading import Source, canonical_xml
from einvoice_reader.model import Message, Validation
from einvoice_reader.scenarios import KOSIT, Scenario, fallback_scenario, find_scenario
from einvoice_reader.schematron import RunFailure, SchematronRunner

FACTURX_PROFILES = {
    "MINIMUM": "facturx-minimum",
    "BASIC WL": "facturx-basicwl",
    "BASIC": "facturx-basic",
    "EN16931": "facturx-en16931",
    "EXTENDED": "facturx-extended",
}
NOT_E_INVOICE_PROFILES = {"MINIMUM", "BASIC WL"}
CURRENT_XRECHNUNG = (3, 0)
FAILURE_WARNINGS = {
    "timeout": "validation timed out for this document",
    "busy": "validation queue full, retry later",
    "failed": "validation failed for this document",
}


def kosit_version() -> str:
    return (KOSIT / "VERSION").read_text().strip()


def rule_set_name(key: str) -> str:
    if key.startswith("facturx-"):
        profile = next(p for p, k in FACTURX_PROFILES.items() if k == key)
        return f"Factur-X 1.09 {profile}"
    match = re.fullmatch(r"xrechnung-(.+)-validator-configuration-(.+)", kosit_version())
    xrechnung, config = match.groups() if match else ("", kosit_version())
    syntax = key[-3:].upper()
    if key.startswith("en16931-"):
        return f"EN16931 {syntax} (KoSIT {config})"
    return f"XRechnung {syntax} {xrechnung} (KoSIT {config})"


def scenario_for(fmt: FormatInfo) -> Scenario | None:
    kind = "CII" if fmt.syntax == "CII" else f"UBL-{fmt.document_type}"
    return find_scenario(kind, fmt.customization_id) or fallback_scenario(
        kind, fmt.customization_id
    )


def rule_keys_for(fmt: FormatInfo) -> list[str]:
    scenario = scenario_for(fmt)
    if scenario is not None:
        return scenario.rule_keys
    syntax = fmt.syntax.lower()
    if fmt.standard == "XRechnung":
        return [f"en16931-{syntax}", f"xrechnung-{syntax}"]
    if fmt.syntax == "CII" and fmt.standard in ("Factur-X", "ZUGFeRD"):
        if fmt.profile in FACTURX_PROFILES:
            return [FACTURX_PROFILES[fmt.profile]]
    return [f"en16931-{syntax}"]


def _xsd_options(source: Source, fmt: FormatInfo) -> dict:
    """KoSIT scenarios and non-Factur-X CII use the full D22B schema, Factur-X profiles theirs."""
    if fmt.syntax == "CII" and (
        scenario_for(fmt) is not None
        or not (fmt.standard in ("Factur-X", "ZUGFeRD") and fmt.profile in FACTURX_PROFILES)
    ):
        return {"flavor": "factur-x", "level": "extended-ctc-fr"}
    return facturx_options(source, fmt)


def _xsd_messages(source: Source, fmt: FormatInfo) -> list[Message]:
    try:
        facturx.xml_check_xsd(source.xml, **_xsd_options(source, fmt))
    except Exception as e:
        return [Message(severity="error", rule_id="XSD", message=str(e)[:1000])]
    return []


def _version_messages(fmt: FormatInfo) -> list[Message]:
    match = re.search(r"xrechnung_(\d+)\.(\d+)", fmt.customization_id or "")
    if fmt.standard != "XRechnung" or not match:
        return []
    declared = (int(match[1]), int(match[2]))
    if declared >= CURRENT_XRECHNUNG:
        return []
    return [
        Message(
            severity="warning",
            rule_id="VERSION",
            message=f"Declares XRechnung {declared[0]}.{declared[1]}, "
            f"validated against XRechnung {CURRENT_XRECHNUNG[0]}.{CURRENT_XRECHNUNG[1]} rules",
        )
    ]


def xsd_messages(source: Source, fmt: FormatInfo) -> list[Message]:
    return _xsd_messages(source, fmt)


def finish_validation(
    fmt: FormatInfo,
    xsd: list[Message],
    canonical: bytes,
    runner: SchematronRunner,
) -> tuple[Validation, list[str]]:
    keys = rule_keys_for(fmt)
    messages = list(xsd)
    rule_sets = [f"XSD {fmt.syntax}"]
    if not messages:
        schematron = runner.run(canonical, keys)
        if isinstance(schematron, RunFailure):
            return Validation(performed=False), [FAILURE_WARNINGS[schematron.reason]]
        scenario = scenario_for(fmt)
        levels = scenario.levels if scenario else {}
        messages += [
            Message(**{**vars(m), "severity": levels.get(m.rule_id, m.severity)})
            for m in schematron
        ]
        rule_sets += [rule_set_name(key) for key in keys]
    messages += _version_messages(fmt)
    valid = not any(m.severity == "error" for m in messages)
    return (
        Validation(
            performed=True,
            valid=valid,
            is_e_invoice=valid and fmt.profile not in NOT_E_INVOICE_PROFILES,
            rule_sets=rule_sets,
            messages=messages,
        ),
        [],
    )


def validate(
    source: Source, fmt: FormatInfo, runner: SchematronRunner
) -> tuple[Validation, list[str]]:
    return finish_validation(fmt, xsd_messages(source, fmt), canonical_xml(source.root), runner)
