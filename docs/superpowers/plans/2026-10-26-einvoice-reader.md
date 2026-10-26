# einvoice-reader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stateless HTTP service in a Docker image that reads ZUGFeRD/Factur-X PDFs and XRechnung/EN 16931 XML (CII and UBL), returns one uniform JSON document and validates the invoice in the same call.

**Architecture:** FastAPI app. The input is loaded (PDF → embedded XML plus XMP via `factur-x`/`pypdf`), the format is detected, fields are read with `facturx.parse_ubl_cii_xml()` and mapped to Pydantic models. Validation runs XSD in-process and Schematron in one dedicated `spawn` worker process that owns `saxonche`.

**Tech Stack:** Python 3.12, uv, FastAPI, uvicorn, python-multipart, factur-x ≥ 7.7, lxml, pypdf (via factur-x), saxonche 13.1, pydantic 2, pytest, httpx, ruff. GitHub Actions, release-please, GHCR.

**Spec:** `docs/superpowers/specs/2026-10-26-einvoice-reader-design.md`

## Global Constraints

- Repo `~/Projekte/einvoice-reader` → `github.com/EffizienzAgent/einvoice-reader`, licence Apache-2.0, Conventional Commits.
- API base path `/v1`. Within v1 the result document may only gain keys. Never rename keys, remove them or change their types.
- Result JSON: camelCase keys. Every key is always present; missing values are `null`, missing lists are `[]`.
- Dates are `YYYY-MM-DD` strings with no time-zone conversion.
- Amounts are JSON numbers with the value exactly as written in the XML. They are never computed.
- `422` error codes, exactly: `NO_EMBEDDED_XML`, `UNSUPPORTED_FORMAT`, `MALFORMED_XML`, `MALFORMED_PDF`, `EMPTY_FILE`. Also `413` for files over `MAX_UPLOAD_MB` (default 20) and `401` when `API_KEY` is set and `X-API-Key` is wrong. `/v1/health` stays open without a key.
- Invalid but readable invoices return `200` with `validation.valid: false`.
- `isEInvoice` = `valid` and profile not in {`MINIMUM`, `BASIC WL`}.
- `saxonche` is only ever called inside the Schematron worker process, never from the API's threads.
- XML parsing: `resolve_entities=False`, `no_network=True`, `load_dtd=False`.
- Container: non-root, port 8080, logs to stdout without invoice content.
- Image `ghcr.io/effizienzagent/einvoice-reader`, amd64 + arm64.

## Review Focus

1. **PDF with an XML attachment that has a non-standard name** (`xrechnung.xml`, `ZUGFeRD-invoice.xml`) → it is still found. Test in Task 3.
2. **PDF with several XML attachments** (invoice plus some other XML) → the one whose root is CII or UBL is used. Test in Task 3.
3. **XML with a BOM, ISO-8859-1 encoding or leading whitespace** → parsed normally. Test in Task 3.
4. **n8n sending 10 files in parallel** → all return 200 with `validation.performed: true`, and nothing crashes. Test in Task 8.
5. **File just over the size limit** → `413` before any parsing. Test in Task 8.

---

### Task 1: Scaffold, health endpoint, CI

**Files:**
- Create: `pyproject.toml`, `src/einvoice_reader/__init__.py`, `src/einvoice_reader/settings.py`, `src/einvoice_reader/api.py`, `tests/test_health.py`, `.github/workflows/ci.yml`, `LICENSE` (Apache-2.0 text), `.gitignore`, `README.md` (stub: one paragraph + "docs follow")

**Interfaces:**
- Produces: `__version__: str` in `einvoice_reader/__init__.py` (start `"0.1.0"`). `Settings` (frozen dataclass), built from the environment by `Settings.from_env()`, with fields `max_upload_mb: int = 20`, `api_key: str | None = None`, `log_level: str = "INFO"` and `validation_timeout_s: float = 30`. `create_app(settings: Settings | None = None) -> FastAPI` in `api.py`, plus a module-level `app = create_app()` for uvicorn.

- [ ] **Step 1:** Create `pyproject.toml` with the hatchling build backend and package `einvoice_reader` under `src/`. Runtime dependencies: `fastapi`, `uvicorn[standard]`, `python-multipart`, `factur-x>=7.7`, `lxml`, `saxonche>=13.1`, `pydantic>=2`. Dev group: `pytest`, `httpx`, `ruff`. Ruff settings: line length 100, `select = ["E","F","I","UP","B"]`. Run `uv sync` and expect it to succeed.
- [ ] **Step 2: Write the failing test** `tests/test_health.py::test_health_ok`. It uses a `TestClient(create_app())` and checks that `GET /v1/health` returns `200` with body `{"status": "ok"}`.
- [ ] **Step 3:** `uv run pytest tests/test_health.py -v` → FAIL (import error).
- [ ] **Step 4:** Implement `Settings.from_env()`, which reads `MAX_UPLOAD_MB`, `API_KEY`, `LOG_LEVEL` and `VALIDATION_TIMEOUT_S`. Then implement `create_app` with the `/v1/health` route. Set the OpenAPI title to `einvoice-reader` and the version to `__version__`.
- [ ] **Step 5:** `uv run pytest -v && uv run ruff check && uv run ruff format --check` → all pass.
- [ ] **Step 6:** Write `.github/workflows/ci.yml`. Triggers: push and pull_request. `concurrency: {group: ci-${{ github.ref }}, cancel-in-progress: true}`. Two parallel jobs on `ubuntu-24.04`, `lint` (`ruff check`, `ruff format --check`) and `test` (`pytest -q`), both via `astral-sh/setup-uv` with `enable-cache: true`. Verify with `uvx actionlint` → no errors.
- [ ] **Step 7: Commit** `chore: scaffold service with health endpoint and CI`

### Task 2: Test fixtures

**Files:**
- Create: `tests/fixtures/kosit/` (business-cases/standard, business-cases/extension, technical-cases from the KoSIT `xrechnung-testsuite` release `v2026-08-31`, plus its `LICENSE`), `tests/fixtures/own/` (handwritten files, see below), `scripts/make_fixture_pdfs.py`, `tests/fixtures/pdf/` (generated output, committed), `tests/fixtures/README.md` (where every file comes from and under which licence)

**Interfaces:**
- Produces: the fixture paths that later tasks use by name:
  - `kosit/business-cases/standard/01.01a-INVOICE_uncefact.xml` / `_ubl.xml` (simple valid XRechnung, IBAN `DE79000000001234567890`, issue date `2016-06-16`)
  - `kosit/technical-cases/cius/01.03_comprehensive_test_{uncefact,ubl}.xml` (nearly every BT)
  - `kosit/business-cases/standard/03.01a-INVOICE_uncefact.xml`, `03.04a-…`, `03.05a-…` (the cases where Mustang's sum check failed)
  - `own/minimum.xml` (Factur-X MINIMUM CII, GuidelineID `urn:factur-x.eu:1p0:minimum`, invoice number `MIN-1`, issue date `20260105`, BT-109 `100.00`, BT-110 `19.00`, BT-112 `119.00`, BT-115 `119.00`, buyer reference `REF-1`, seller `Min GmbH` with VAT ID `DE111111111`, buyer `Kunde AG`)
  - `own/basicwl.xml` (same data, GuidelineID `urn:factur-x.eu:1p0:basicwl`, with one VAT breakdown `S` 19 %)
  - `own/broken_ubl_no_issue_date.xml` (copy of `01.01a-INVOICE_ubl.xml` without `cbc:IssueDate`)
  - `own/credit_note_ubl.xml` (UBL `CreditNote`, type code `381`, one `CreditNoteLine` with `CreditedQuantity` `2`)
  - `own/zugferd1.xml` (root `rsm:CrossIndustryDocument`, namespace `urn:ferd:CrossIndustryDocument:invoice:1p0`)
  - `own/not_an_invoice.xml` (`<order/>`), `own/latin1.xml` (`01.01a-INVOICE_uncefact.xml` re-encoded as ISO-8859-1 with seller name `Müller` and a matching XML declaration), `own/bom.xml` (same with UTF-8 BOM and a leading newline)
  - `pdf/en16931.pdf` (blank page + `01.01a-INVOICE_uncefact.xml`, flavor `factur-x`, attachment `factur-x.xml`), `pdf/zugferd_xrechnung_name.pdf` (same XML attached as `xrechnung.xml` via `pypdf`), `pdf/two_attachments.pdf` (`notes.xml` = `<notes/>` first, then `factur-x.xml`), `pdf/minimum.pdf` (`own/minimum.xml`, flavor `factur-x`, level `minimum`), `pdf/plain.pdf` (blank page, no attachment), `pdf/broken.pdf` (`%PDF-1.7` followed by 100 bytes of garbage)

- [ ] **Step 1:** Download the KoSIT test suite: `gh release download v2026-08-31 -R itplr-kosit/xrechnung-testsuite`. Copy the `src/test` tree and `LICENSE` into `tests/fixtures/kosit/`.
- [ ] **Step 2:** Write the `own/` files listed above.
- [ ] **Step 3:** Write `scripts/make_fixture_pdfs.py`. It creates the blank A4 page with `pypdf.PdfWriter().add_blank_page(595, 842)` and uses `facturx.generate_from_binary(..., check_xsd=False)` for the factur-x PDFs. For the custom attachment names it uses `PdfWriter.add_attachment`. Run `uv run python scripts/make_fixture_pdfs.py`; the six PDFs exist afterwards.
- [ ] **Step 4:** Write `tests/fixtures/README.md` (source, version and licence per folder).
- [ ] **Step 5: Commit** `test: add KoSIT test suite and generated fixtures`

### Task 3: Input loading and safe XML parsing

**Files:**
- Create: `src/einvoice_reader/errors.py`, `src/einvoice_reader/loading.py`
- Test: `tests/test_loading.py`

**Interfaces:**
- Produces:
  - `class ReadError(Exception)` with `code: str`, `message: str` and `format: dict | None`.
  - `parse_xml(data: bytes) -> etree._Element`, hardened as described in the constraints. Raises `ReadError("MALFORMED_XML", …)`.
  - `@dataclass Source`: `container: Literal["PDF","XML"]`, `xml: bytes`, `root: etree._Element`, `embedded_file_name: str | None`, `xmp: etree._Element | None`.
  - `load(data: bytes) -> Source`.
  - `INVOICE_ROOTS: dict[str, str]`, mapping the Clark-notation root tag to `"CII" | "UBL-Invoice" | "UBL-CreditNote"`. ZUGFeRD 1.0 is not in it.

- [ ] **Step 1: Write the failing tests:**
  - `test_xml_cii`: `load(01.01a uncefact)` → `container == "XML"`, root tag ends with `CrossIndustryInvoice`.
  - `test_pdf_factur_x`: `load(pdf/en16931.pdf)` → `container == "PDF"`, `embedded_file_name == "factur-x.xml"`, `xmp is not None`.
  - `test_pdf_custom_name`: `pdf/zugferd_xrechnung_name.pdf` → `embedded_file_name == "xrechnung.xml"`.
  - `test_pdf_two_attachments`: picks `factur-x.xml`, not `notes.xml`.
  - `test_encodings` (parametrized over `own/latin1.xml` and `own/bom.xml`) → loads, and the seller name in the XML text decodes to `Müller` for latin1.
  - `test_errors` (parametrized over `(b"", "EMPTY_FILE")`, `(pdf/plain.pdf, "NO_EMBEDDED_XML")`, `(pdf/broken.pdf, "MALFORMED_PDF")`, `(b"<a>", "MALFORMED_XML")`) → `ReadError.code` matches.
  - `test_xxe_not_resolved`: XML with `<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>` → no file content appears in the parsed tree text.
- [ ] **Step 2:** `uv run pytest tests/test_loading.py -v` → FAIL.
- [ ] **Step 3:** Implement `load`.
  - Strip leading whitespace and a BOM before checking `%PDF`.
  - For PDFs, use `facturx.get_xml_from_pdf(data, check_xsd=False)`. If it returns no XML, or the root is not in `INVOICE_ROOTS`, iterate over `pypdf.PdfReader(...).attachments` and take the first XML whose root is in `INVOICE_ROOTS`.
  - Read the XMP from `reader.xmp_metadata` with `parse_xml`; on any error, use `None`.
  - Map pypdf/`PdfReadError` exceptions to `MALFORMED_PDF`.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5: Commit** `feat: load PDF and XML input safely`

### Task 4: Format detection

**Files:**
- Create: `src/einvoice_reader/detect.py`
- Test: `tests/test_detect.py`

**Interfaces:**
- Consumes: `Source` and `ReadError` (Task 3).
- Produces: `@dataclass FormatInfo`, with fields `syntax: Literal["CII","UBL"]`, `container`, `document_type: Literal["Invoice","CreditNote"]`, `standard: Literal["ZUGFeRD","Factur-X","XRechnung","EN16931","Peppol","unknown"]`, `profile: str | None`, `version: str | None`, `customization_id: str | None` and `embedded_file_name: str | None`. Also `detect(source: Source) -> FormatInfo`.

Lookup by BT-24, first match wins. Use `startswith`/`contains` on the lower-cased ID:

| BT-24 contains | standard | profile |
|---|---|---|
| `xrechnung` | XRechnung | XRECHNUNG |
| `peppol` (`urn:fdc:peppol.eu`) without `xrechnung` | Peppol | null |
| `urn:factur-x.eu:1p0:minimum` | Factur-X | MINIMUM |
| `urn:factur-x.eu:1p0:basicwl` | Factur-X | BASIC WL |
| `urn:cen.eu:en16931:2017#compliant#urn:factur-x.eu:1p0:basic` | Factur-X | BASIC |
| `#conformant#urn:factur-x.eu:1p0:extended` | Factur-X | EXTENDED |
| exactly `urn:cen.eu:en16931:2017` | EN16931 (CII → Factur-X when container is PDF) | EN16931 |
| anything else | unknown | null |

For PDFs, the XMP decides `standard` and `version`. Namespace `urn:zugferd:pdfa:CrossIndustryDocument:invoice:2p0#` → `ZUGFeRD`; `urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#` → `Factur-X`. `version` is the text of `*:Version`. XRechnung stays `XRechnung` even inside a PDF.

- [ ] **Step 1: Write the failing tests:**
  - `test_xrechnung_cii`: 01.01a uncefact → `("CII","XML","Invoice","XRechnung","XRECHNUNG")`.
  - `test_xrechnung_ubl`: 01.01a ubl → `syntax "UBL"`, `standard "XRechnung"`.
  - `test_credit_note`: `own/credit_note_ubl.xml` → `document_type "CreditNote"`.
  - `test_minimum`: `own/minimum.xml` → profile `"MINIMUM"`, standard `"Factur-X"`, version `None`.
  - `test_basicwl`: `own/basicwl.xml` → profile `"BASIC WL"`.
  - `test_pdf_xmp`: `pdf/minimum.pdf` → standard `"Factur-X"`, version is not `None`.
  - `test_zugferd1_unsupported`: `own/zugferd1.xml` → `ReadError("UNSUPPORTED_FORMAT")`.
  - `test_not_an_invoice`: `own/not_an_invoice.xml` → `ReadError("UNSUPPORTED_FORMAT")`.
- [ ] **Step 2:** Run the tests → FAIL. **Step 3:** Implement. **Step 4:** Run the tests → PASS.
- [ ] **Step 5: Commit** `feat: detect e-invoice syntax, standard and profile`

### Task 5: Result model and mapping

**Files:**
- Create: `src/einvoice_reader/model.py`, `src/einvoice_reader/mapping.py`
- Test: `tests/test_mapping.py`

**Interfaces:**
- Consumes: `Source`, `FormatInfo`.
- Produces:
  - Pydantic models: `Result`, `FormatOut`, `Invoice`, `Party`, `Address`, `Contact`, `Payment`, `Totals`, `VatBreakdown`, `AllowanceCharge`, `Line`, `Attachment`, `Validation` and `Message`. Field names and nesting follow the spec's result document exactly. A shared base config sets `alias_generator=to_camel` and `populate_by_name=True`; output uses `model_dump(by_alias=True)`. Decimal fields serialize to JSON numbers through a `PlainSerializer(float, when_used="json")` type alias `Amount`.
  - `map_invoice(source: Source, fmt: FormatInfo) -> tuple[Invoice, list[str]]`. It returns the model plus reader warnings.
  - `TYPE_CODES: dict[str, str]` holds `380→invoice`, `381→credit_note`, `384→corrected`, `386→prepayment`, `389→self_billed`; everything else maps to `other`.

Approach: call `facturx.parse_ubl_cii_xml(source.xml, check_xsd=False, float_as="string", date_as="string")`, then map the BT/BG-keyed dict. Key facts verified in planning:
- Parties are `BG-4`/`BG-7` with `name`, `biz_name`, `vat_identifier`, `tax_identifier`, `identifiers` (dict scheme→value, scheme may be `None`), `legal_identifier(_schemeid)`, `einvoicing_addr(_schemeid)`, `addr_l1..3`, `city`, `postcode`, `country_subdivision`, `country_code` and `contacts[]`.
- Payment uses flat keys `BT-81..BT-88`; there is only one account.
- Lines are `BG-25[]` with `BT-126..BT-161`.
- Allowances/charges are `BG-20`/`BG-21` and, on lines, `BG-27`/`BG-28`.

If `date_as="string"` does not return ISO dates, convert `datetime.date` with `isoformat()`. If `Party.name` is empty and `tradingName` is set (or the reverse), copy the value over and add the warning `"BT-27 missing, used trading name"`. If BT-110 is missing, set `vatTotal` to `null` and add the warning `"BT-110 missing"`.

- [ ] **Step 1: Write the failing tests:**
  - `test_cii_core`: 01.01a uncefact → `number`, `issueDate == "2016-06-16"`, `currency == "EUR"` and `payment.accounts[0].iban == "DE79000000001234567890"`. Also `totals` equal the decimal values of BT-109/110/112/115 read straight from the file with XPath. The test computes the expected values itself with lxml, so the mapper cannot recalculate them.
  - `test_ubl_matches_cii`: the 01.01a UBL and CII variants produce the same `number`, `issueDate`, `totals` and `seller.name`.
  - `test_ubl_seller_name_is_registration_name`: 01.03 comprehensive ubl → `seller.name` equals the text of `cac:PartyLegalEntity/cbc:RegistrationName` and `seller.tradingName` equals `cac:PartyName/cbc:Name`.
  - `test_mustang_regressions` (parametrized over 03.01a, 03.04a and 03.05a uncefact) → maps without raising, and `totals.grossTotal` equals the XML's `GrandTotalAmount`.
  - `test_credit_note`: `type == "credit_note"`, `typeCode == "381"`, `lines[0].quantity == 2`, and amounts stay positive.
  - `test_minimum_has_no_lines`: `lines == []`, `vatBreakdown == []`, `totals.grossTotal == 119.0`.
  - `test_all_keys_present`: dump `Result` for `own/minimum.xml` and assert that every key from the spec's example JSON exists. Keep the key list as a fixture constant in the test.
  - `test_amount_json_number`: `Result.model_dump_json()` contains `"grossTotal":119.0` and no quoted amount.
- [ ] **Step 2:** Run the tests → FAIL. **Step 3:** Implement. **Step 4:** Run the tests → PASS.
- [ ] **Step 5:** Golden files. Write `tests/test_golden.py`. It runs `map_invoice` for every XML under `tests/fixtures/kosit/**` and compares `invoice` with `tests/golden/<relative path>.json`. With `UPDATE_GOLDEN=1`, it writes the golden file instead. Generate the files, spot-check three by hand against their XML (one CII, one UBL, one with allowances), then commit.
- [ ] **Step 6: Commit** `feat: map e-invoices to the uniform result model`

### Task 6: Schematron worker

**Files:**
- Create: `src/einvoice_reader/schematron.py`, `rules/xrechnung/XRechnung-CII-validation.xsl`, `rules/xrechnung/XRechnung-UBL-validation.xsl`, `rules/xrechnung/LICENSE`, `rules/xrechnung/VERSION` (contains `xrechnung-3.0.2-schematron-2.6.0`), `scripts/update_xrechnung_rules.sh`
- Test: `tests/test_schematron.py`

**Interfaces:**
- Produces:
  - `RULES: dict[str, Path]`. Keys: `facturx-minimum`, `facturx-basicwl`, `facturx-basic`, `facturx-en16931` and `facturx-extended` (from `facturx/xsd_and_schematron/<dir>/*.xsl*`), `en16931-ubl` (`ubl-2.1/EN16931-UBL-validation.xslt`), `xrechnung-cii` and `xrechnung-ubl`.
  - `@dataclass Message`: `severity: Literal["error","warning","info"]`, `rule_id: str | None`, `message: str`, `location: str | None`. Task 5's Pydantic `Message` is built from it.
  - `parse_svrl(svrl: str) -> list[Message]`. It reads `svrl:failed-assert` and `svrl:successful-report`; the `@flag` value maps `fatal|error` to `error`, `warning` to `warning`, anything else to `info`, with a default of `error`. `rule_id` comes from `@id`, `location` from `@location` and `message` from the normalised `svrl:text`.
  - `class SchematronRunner` with `__init__(timeout_s: float)`, `run(xml: bytes, rule_keys: list[str]) -> list[Message] | None` (`None` means the worker died or timed out and has been restarted) and `close()`.

Approach: `ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn"))`. The worker module keeps one `PySaxonProcessor(license=False)` and compiles each XSLT lazily on first use, caching it by key. `run` is thread-safe: submit to the pool and wait with `timeout_s`. On `BrokenProcessPool` or `TimeoutError`, take a lock, terminate the worker processes, create a new pool and return `None`. The update script downloads the latest `itplr-kosit/xrechnung-schematron` release zip, copies the two XSL files and `LICENSE`, and writes `VERSION`.

- [ ] **Step 1: Write the failing tests:**
  - `test_valid_xrechnung_cii_no_errors`: `run(01.01a uncefact, ["facturx-en16931","xrechnung-cii"])` → no message with severity `error`.
  - `test_valid_xrechnung_ubl_no_errors`: the same for UBL with `["en16931-ubl","xrechnung-ubl"]`.
  - `test_detects_rule_violation`: 01.01a uncefact with `ram:GrandTotalAmount` changed to `1.00` → at least one error whose `rule_id` starts with `BR-CO-`.
  - `test_parse_svrl_flags`: a handwritten SVRL string with three asserts (`flag="fatal"`, `flag="warning"`, no flag) → severities `["error","warning","error"]`.
  - `test_worker_restart`: kill the worker PID (`os.kill(next(iter(runner._pool._processes)), SIGKILL)`) → the next `run` returns `None`, the run after that returns a list again.
  - `test_parallel_threads`: 10 threads, each calling `run` on 01.01a uncefact → all return lists and no process crashes.
  - `test_soak` (marked `@pytest.mark.slow`, excluded from the default run with `-m "not slow"` in `pyproject.toml`): 5,000 runs over the 01.01a CII and UBL files, cycling through 8 threads. Assert that no run returns `None`. Sample the worker's RSS with `psutil` (dev dependency) after runs 500 and 5,000, and assert that it grows by less than 20 %. Record both RSS values and the total time in the commit message.
- [ ] **Step 2:** Run the tests → FAIL. **Step 3:** Implement. **Step 4:** Run the tests → PASS, then `uv run pytest -m slow tests/test_schematron.py -v` → PASS.
- [ ] **Step 4b:** If the soak test fails (crash or memory growth over 20 %), stop and report to Tobias before you continue. The fallback is the Saxon server sidecar behind the same `SchematronRunner.run` interface.
- [ ] **Step 5: Commit** `feat: run Schematron rules in an isolated worker process`

### Task 7: Validation

**Files:**
- Create: `src/einvoice_reader/validation.py`
- Test: `tests/test_validation.py`

**Interfaces:**
- Consumes: `FormatInfo`, `Source`, `SchematronRunner`, `RULES` and `Message`.
- Produces: `rule_keys_for(fmt: FormatInfo) -> list[str]`, and `validate(source: Source, fmt: FormatInfo, runner: SchematronRunner) -> tuple[ValidationOut, list[str]]`. `ValidationOut` is the Task 5 `Validation` model; the second value is the list of warnings.

Rules:
- XSD runs first, via `facturx.xml_check_xsd(source.xml)`, which returns `True` or raises `Exception`. A failure becomes one `Message("error", "XSD", str(e)[:1000], None)`. When the XSD check fails, Schematron is skipped, because the rules assume schema-valid input.
- Rule selection:
  - XRechnung CII → `facturx-en16931`, `xrechnung-cii`
  - XRechnung UBL → `en16931-ubl`, `xrechnung-ubl`
  - Factur-X/ZUGFeRD profile P → `facturx-<p>`
  - EN16931 CII → `facturx-en16931`
  - EN16931/Peppol UBL → `en16931-ubl`
  - unknown CII → `facturx-en16931`
  - unknown UBL → `en16931-ubl`
- `ruleSets` lists human-readable names with versions, e.g. `"Factur-X 1.09 EN16931"`, `"EN16931 UBL"` and `"XRechnung CII 3.0.2 (schematron 2.6.0)"`. The XRechnung version comes from `rules/xrechnung/VERSION`, the Factur-X version from the bundled file name.
- An XRechnung BT-24 that names an older version than `3.0` gets a warning in `messages` (severity `warning`, `rule_id` `"VERSION"`).
- If `runner.run` returns `None`, set `performed: false`, set `valid` and `isEInvoice` to `null`, and add the warning `"validation unavailable, retry later"`.

- [ ] **Step 1: Write the failing tests:**
  - `test_valid_kosit_files` (parametrized over every file in `kosit/business-cases/standard`) → `valid is True`. If a KoSIT file is listed as invalid in the test suite's own metadata, `xfail` it with the reason.
  - `test_broken_ubl_invalid`: `own/broken_ubl_no_issue_date.xml` → `valid is False`, with a message where `rule_id == "XSD"`.
  - `test_minimum_valid_but_not_einvoice`: `own/minimum.xml` → `valid is True`, `isEInvoice is False`.
  - `test_xrechnung_is_einvoice`: 01.01a → `isEInvoice is True`.
  - `test_rule_keys_for` (table-driven) → expected keys for each row above.
  - `test_runner_down`: a runner stub whose `run` returns `None` → `performed is False`, `valid is None`.
- [ ] **Step 2:** Run the tests → FAIL. **Step 3:** Implement. **Step 4:** Run the tests → PASS.
- [ ] **Step 5: Commit** `feat: validate with XSD and Schematron, derive isEInvoice`

### Task 8: Parse endpoint and info

**Files:**
- Create: `src/einvoice_reader/service.py`
- Modify: `src/einvoice_reader/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `read_invoice(data: bytes, *, validate: bool, include_xml: bool, runner: SchematronRunner | None) -> Result`, which raises `ReadError`.
  - `POST /v1/invoices/parse`
  - `GET /v1/info` → `{"serviceVersion", "ruleSets": {"facturx": "1.09", "xrechnung": "<VERSION file>", "en16931Ubl": "<from file name or 'bundled'>"}}`.
  - `create_app` starts a `SchematronRunner` in the lifespan and closes it on shutdown.

Endpoint details:
- Accept `multipart/form-data` (field `file`), or a raw body when `content-type` is `application/pdf`, `application/xml` or `text/xml`.
- Read the body in chunks and stop with `413` as soon as it exceeds `max_upload_mb`.
- The handler is a sync `def`, so it runs in the threadpool.
- `ReadError` → `422 {"error": {"code","message"}, "format": e.format}`.
- API key: a dependency on every `/v1/*` route except `/v1/health`, active only when `settings.api_key` is set, checking `X-API-Key`.
- Logging: method, path, status and duration only.

- [ ] **Step 1: Write the failing tests:**
  - `test_parse_multipart_pdf`: `pdf/en16931.pdf` → `200`, `format.container == "PDF"`, `invoice.number` set, `validation.performed is True`, `xml` starts with `<?xml` or `<`.
  - `test_parse_raw_xml`: `content-type: application/xml` body 01.01a ubl → `200`, `format.syntax == "UBL"`.
  - `test_include_xml_false` → `xml is None`.
  - `test_validate_false` → `validation.performed is False`.
  - `test_invalid_still_200`: `own/broken_ubl_no_issue_date.xml` → `200`, `validation.valid is False`.
  - `test_422_codes` (parametrized: plain.pdf, empty body, `<a>`, zugferd1.xml) → `422` with the matching `error.code`.
  - `test_413`: settings `max_upload_mb=1` and a body of 1 MiB + 1 byte → `413`.
  - `test_api_key`: settings `api_key="k"` → parse without the header returns `401`, with `X-API-Key: k` returns `200`, and `/v1/health` without the header returns `200`.
  - `test_parallel_requests`: 10 threads post `pdf/en16931.pdf` → all `200`, all `validation.performed is True`.
  - `test_info`: `ruleSets.xrechnung` equals the content of `rules/xrechnung/VERSION`.
- [ ] **Step 2:** Run the tests → FAIL. **Step 3:** Implement. **Step 4:** Run the tests → PASS.
- [ ] **Step 5:** `uv run uvicorn einvoice_reader.api:app --port 8080`, then `curl -F file=@tests/fixtures/pdf/en16931.pdf localhost:8080/v1/invoices/parse | jq .invoice.totals` shows the totals. Open `/docs`; the OpenAPI page loads.
- [ ] **Step 6: Commit** `feat: add parse and info endpoints`

### Task 9: Docker image and smoke test

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `scripts/smoke_test.sh`
- Modify: `.github/workflows/ci.yml` (add job `image`)

**Interfaces:**
- Consumes: `einvoice_reader.api:app`, fixtures `pdf/en16931.pdf` and `kosit/.../01.01a-INVOICE_ubl.xml`.
- Produces: an image listening on 8080 with a `HEALTHCHECK` on `/v1/health`. `scripts/smoke_test.sh <image>` starts the container, waits up to 30 s for health, posts both fixtures, asserts `200` and a non-empty `.invoice.number` with `jq`, and removes the container. It exits non-zero on any failure.

Approach: a multi-stage build. The builder is `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`, running `uv sync --frozen --no-dev` into `/app/.venv`. The runtime is `python:3.12-slim-bookworm`, copying the venv, `src` and `rules`, with user `app` (uid 10001). The command is `uvicorn einvoice_reader.api:app --host 0.0.0.0 --port 8080 --workers ${WORKERS:-1}`. Do not install a compiler in the runtime image.

- [ ] **Step 1:** `docker build -t einvoice-reader:dev .` succeeds. `docker image ls einvoice-reader:dev` shows a size under 300 MB; record the actual size in the PR description.
- [ ] **Step 2:** `scripts/smoke_test.sh einvoice-reader:dev` → exit 0.
- [ ] **Step 3:** CI job `image`, only on `pull_request`: `docker/setup-buildx-action`, `docker/build-push-action` with `load: true`, `cache-from/to: type=gha`, `platforms: linux/amd64`, then the smoke test. Check with `uvx actionlint` → clean.
- [ ] **Step 4: Commit** `build: add container image and smoke test`

### Task 10: Releases, image publishing, maintenance, README

**Files:**
- Create: `.github/workflows/release.yml`, `.github/workflows/rules-check.yml`, `.github/workflows/pr-title.yml`, `release-please-config.json`, `.release-please-manifest.json` (`{".": "0.1.0"}`), `renovate.json`
- Modify: `README.md`

**Interfaces:**
- Produces:
  - `release.yml`, on push to `main`, has three parts:
    - Job `release-please` (`googleapis/release-please-action@v4`, `release-type: python`) with outputs `release_created` and `tag_name`.
    - Job `image`, a matrix over `{platform: linux/amd64, runner: ubuntu-24.04}` and `{platform: linux/arm64, runner: ubuntu-24.04-arm}`. It builds and pushes by digest to `ghcr.io/effizienzagent/einvoice-reader` with GHA cache scoped per platform.
    - Job `manifest`, which merges the digests with `docker buildx imagetools create`. On a release it tags `X.Y.Z`, `X.Y`, `X` and `latest` via `docker/metadata-action`; otherwise it tags `edge`.
    - The build steps set `sbom: true` and `provenance: mode=max`. Permissions: `contents: write`, `pull-requests: write`, `packages: write`, `id-token: write`, `attestations: write`.
  - `rules-check.yml` runs weekly on cron `0 6 * * 1` plus `workflow_dispatch`. It compares the latest `itplr-kosit/xrechnung-schematron` release tag with `rules/xrechnung/VERSION` and opens an issue with `gh issue create` when they differ and no open issue with the same title exists.
  - `pr-title.yml`: `amannn/action-semantic-pull-request@v5`.
  - `renovate.json`: `config:recommended`, plus grouping for the GitHub Actions and pip packages.
  - README in English. Sections:
    - what it does
    - quick start (`docker run -p 8080:8080 ghcr.io/effizienzagent/einvoice-reader`)
    - API with the curl example
    - result document (link to the spec)
    - n8n usage: HTTP Request node, Body Content Type `Form-Data`, parameter type `n8n Binary File`, name `file`
    - configuration env vars
    - licences of bundled rules
- [ ] **Step 1:** Write the files. `uvx actionlint` → clean. `npx --yes renovate-config-validator` → valid.
- [ ] **Step 2: Commit** `ci: add releases, multi-arch image publishing and maintenance workflows`

### Task 11: Publish to GitHub (needs Tobias's go-ahead in chat)

- [ ] **Step 1:** Ask Tobias to confirm creating the public repository `EffizienzAgent/einvoice-reader`.
- [ ] **Step 2:** `gh repo create EffizienzAgent/einvoice-reader --public --source ~/Projekte/einvoice-reader --push`.
- [ ] **Step 3:** Enable "Allow GitHub Actions to create and approve pull requests" (needed by release-please). Then set the GHCR package visibility to public after the first image push.
- [ ] **Step 4:** Watch the first CI and release runs with `gh run watch`. The release-please PR for `0.1.0` appears. After the merge, `docker pull ghcr.io/effizienzagent/einvoice-reader:0.1.0` works on amd64 and arm64.
