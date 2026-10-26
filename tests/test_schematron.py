import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor

import psutil
import pytest

from einvoice_reader.schematron import RULES, RunFailure, SchematronRunner, parse_svrl


def errors(messages):
    return [m for m in messages if m.severity == "error"]


def test_rules_exist():
    assert all(path.is_file() for path in RULES.values()), RULES


def test_valid_xrechnung_cii_no_errors(runner, xrechnung_cii):
    messages = runner.run(xrechnung_cii, ["en16931-cii", "xrechnung-cii"])
    assert errors(messages) == []


def test_valid_xrechnung_ubl_no_errors(runner, xrechnung_ubl):
    messages = runner.run(xrechnung_ubl, ["en16931-ubl", "xrechnung-ubl"])
    assert errors(messages) == []


def test_detects_rule_violation(runner, xrechnung_cii):
    broken = xrechnung_cii.replace(b"<ram:GrandTotalAmount>", b"<ram:GrandTotalAmount>9", 1)
    messages = runner.run(broken, ["en16931-cii", "xrechnung-cii"])
    assert any((m.rule_id or "").startswith("BR-CO-") for m in errors(messages))


def test_parse_svrl_flags():
    svrl = """<svrl:schematron-output xmlns:svrl="http://purl.oclc.org/dsdl/svrl">
      <svrl:failed-assert id="BR-01" flag="fatal" location="/a"><svrl:text> one </svrl:text></svrl:failed-assert>
      <svrl:failed-assert id="BR-02" flag="warning" location="/b"><svrl:text>two</svrl:text></svrl:failed-assert>
      <svrl:failed-assert id="BR-03" location="/c"><svrl:text>three</svrl:text></svrl:failed-assert>
    </svrl:schematron-output>"""
    messages = parse_svrl(svrl)
    assert [m.severity for m in messages] == ["error", "warning", "error"]
    assert (messages[0].rule_id, messages[0].message, messages[0].location) == (
        "BR-01",
        "one",
        "/a",
    )


def test_worker_restart(xrechnung_cii):
    runner = SchematronRunner(timeout_s=60)
    try:
        assert isinstance(runner.run(xrechnung_cii, ["facturx-en16931"]), list)
        old_pid = runner.worker_pid()
        os.kill(old_pid, signal.SIGKILL)
        time.sleep(0.2)
        assert isinstance(runner.run(xrechnung_cii, ["facturx-en16931"]), list)
        assert runner.worker_pid() != old_pid
    finally:
        runner.close()


def test_parallel_threads(runner, xrechnung_cii):
    with ThreadPoolExecutor(10) as pool:
        results = list(
            pool.map(lambda _: runner.run(xrechnung_cii, ["facturx-en16931"]), range(10))
        )
    assert all(isinstance(r, list) for r in results)


@pytest.mark.slow
def test_soak(xrechnung_cii, xrechnung_ubl):
    runner = SchematronRunner(timeout_s=60)
    jobs = [
        (xrechnung_cii, ["en16931-cii", "xrechnung-cii"]),
        (xrechnung_ubl, ["en16931-ubl", "xrechnung-ubl"]),
    ]

    def rss() -> int:
        return psutil.Process(runner.worker_pid()).memory_info().rss

    try:
        start = time.monotonic()
        with ThreadPoolExecutor(8) as pool:
            first = list(pool.map(lambda i: runner.run(*jobs[i % 2]), range(500)))
            rss_500 = rss()
            rest = list(pool.map(lambda i: runner.run(*jobs[i % 2]), range(500, 5000)))
            rss_5000 = rss()
        elapsed = time.monotonic() - start
        print(
            f"soak: rss@500={rss_500 / 2**20:.1f} MiB rss@5000={rss_5000 / 2**20:.1f} MiB "
            f"time={elapsed:.1f}s"
        )
        assert all(isinstance(r, list) for r in first + rest)
        assert rss_5000 < rss_500 * 1.2
    finally:
        runner.close()


def big_invoice(xml: bytes, copies: int) -> bytes:
    start = xml.index(b"<ram:IncludedSupplyChainTradeLineItem>")
    end = xml.rindex(b"</ram:IncludedSupplyChainTradeLineItem>") + len(
        b"</ram:IncludedSupplyChainTradeLineItem>"
    )
    return xml[:start] + xml[start:end] * copies + xml[end:]


def test_timeout_under_load_never_raises(xrechnung_cii):
    runner = SchematronRunner(timeout_s=2)
    slow = big_invoice(xrechnung_cii, 5000)
    jobs = [slow] + [xrechnung_cii] * 60
    try:
        with ThreadPoolExecutor(16) as pool:
            results = list(
                pool.map(lambda x: runner.run(x, ["en16931-cii", "xrechnung-cii"]), jobs)
            )
        assert results[0] == RunFailure("timeout")
        assert all(isinstance(r, list) or r == RunFailure("busy") for r in results[1:])
        assert sum(isinstance(r, list) for r in results[1:]) >= 30
    finally:
        runner.close()


def test_saxon_error_returns_none(runner):
    assert runner.run(b"\xff\xfe<not-xml", ["en16931-cii"]) == RunFailure("failed")


def test_queue_wait_is_bounded(xrechnung_cii):
    runner = SchematronRunner(timeout_s=4)
    medium = big_invoice(xrechnung_cii, 400)
    pid = runner.worker_pid()

    def timed(_):
        start = time.monotonic()
        result = runner.run(medium, ["en16931-cii", "xrechnung-cii"])
        return time.monotonic() - start, result

    try:
        with ThreadPoolExecutor(40) as pool:
            results = list(pool.map(timed, range(40)))
        assert max(elapsed for elapsed, _ in results) < 14
        assert any(result == RunFailure("busy") for _, result in results)
        assert any(isinstance(result, list) for _, result in results)
        assert runner.worker_pid() == pid
    finally:
        runner.close()


def test_rules_compiled_at_start(xrechnung_cii):
    runner = SchematronRunner(timeout_s=30)
    try:
        start = time.monotonic()
        runner.run(fixture_minimum_extended(), ["facturx-extended"])
        assert time.monotonic() - start < 0.4
    finally:
        runner.close()


def fixture_minimum_extended() -> bytes:
    from conftest import fixture

    return fixture("own/minimum.xml").replace(b"1p0:minimum", b"1p0:extended")
