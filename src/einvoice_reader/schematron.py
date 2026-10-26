"""Schematron validation in a dedicated worker process.

saxonche crashes the whole process when it is used from a thread other than the one
that created it, so it is only ever loaded inside one spawned worker process.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import facturx
from lxml import etree

from einvoice_reader.isolation import PoolCrashed, PoolTimeout, PoolUnavailable, WorkerPool
from einvoice_reader.scenarios import kosit_rule_path

logger = logging.getLogger(__name__)

_FACTURX = Path(facturx.__file__).parent / "xsd_and_schematron"
RULES: dict[str, Path] = {
    "facturx-minimum": _FACTURX / "facturx-minimum" / "Factur-X_1.09_MINIMUM.xsl",
    "facturx-basicwl": _FACTURX / "facturx-basicwl" / "FACTUR-X_BASIC-WL.xslt",
    "facturx-basic": _FACTURX / "facturx-basic" / "Factur-X_1.09_BASIC.xsl",
    "facturx-en16931": _FACTURX / "facturx-en16931" / "FACTUR-X_EN16931.xslt",
    "facturx-extended": _FACTURX / "facturx-extended" / "FACTUR-X_EXTENDED.xslt",
    **{
        key: kosit_rule_path(key)
        for key in ("en16931-cii", "en16931-ubl", "xrechnung-cii", "xrechnung-ubl")
    },
}

_SVRL = "http://purl.oclc.org/dsdl/svrl"


_SEVERITY = {"fatal": "error", "error": "error", "warning": "warning"}


@dataclass(frozen=True)
class RunFailure:
    reason: Literal["timeout", "busy", "failed"]


@dataclass
class Message:
    severity: Literal["error", "warning", "info"]
    rule_id: str | None
    message: str
    location: str | None


def parse_svrl(svrl: str) -> list[Message]:
    root = etree.fromstring(svrl.encode())
    messages = []
    for node in root.iter(f"{{{_SVRL}}}failed-assert", f"{{{_SVRL}}}successful-report"):
        flag = (node.get("flag") or "error").lower()
        text = node.findtext(f"{{{_SVRL}}}text") or ""
        messages.append(
            Message(
                severity=_SEVERITY.get(flag, "info"),
                rule_id=node.get("id"),
                message=" ".join(text.split()),
                location=node.get("location"),
            )
        )
    return messages


_worker: dict = {}


def _worker_init() -> None:
    from saxonche import PySaxonProcessor

    processor = PySaxonProcessor(license=False)
    _worker["processor"] = processor
    _worker["compiled"] = {
        key: processor.new_xslt30_processor().compile_stylesheet(stylesheet_file=str(path))
        for key, path in RULES.items()
    }


def _worker_run(xml: bytes, rule_keys: list[str]) -> list[Message] | str:
    try:
        processor = _worker["processor"]
        compiled = _worker["compiled"]
        document = processor.parse_xml(xml_text=xml.decode("utf-8"))
        messages = []
        for key in rule_keys:
            if key not in compiled:
                compiled[key] = processor.new_xslt30_processor().compile_stylesheet(
                    stylesheet_file=str(RULES[key])
                )
            messages += parse_svrl(compiled[key].transform_to_string(xdm_node=document))
        return messages
    except Exception as e:
        return type(e).__name__


class SchematronRunner:
    def __init__(self, timeout_s: float = 30):
        self.timeout_s = timeout_s
        self._pool = WorkerPool("Schematron", 1, timeout_s, initializer=_worker_init)

    def worker_pid(self) -> int:
        return self._pool.worker_pids()[0]

    def run(self, xml: bytes, rule_keys: list[str]) -> list[Message] | RunFailure:
        """Return the Schematron messages, or why validation could not run."""
        try:
            result = self._pool.call(_worker_run, xml, rule_keys)
        except PoolTimeout:
            return RunFailure("timeout")
        except PoolUnavailable:
            return RunFailure("busy")
        except PoolCrashed:
            return RunFailure("failed")
        if isinstance(result, str):
            logger.warning("Schematron could not process the document: %s", result[:200])
            return RunFailure("failed")
        return result

    def close(self) -> None:
        self._pool.close()
