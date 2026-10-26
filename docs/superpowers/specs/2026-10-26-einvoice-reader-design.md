# einvoice-reader — Design

Status: draft, 2026-10-26
Owner: Tobias Mende (EffizienzAgent)

## Intent

Incoming e-invoices in Germany arrive as ZUGFeRD/Factur-X PDFs (CII XML embedded in a PDF/A-3) or as plain XRechnung XML (CII or UBL). Libraries exist to extract or validate them, but there is no small, self-hostable service that turns any of these formats into **one stable, uniform JSON** and validates them in the same call.

`einvoice-reader` is that service: an open-source Docker image that clients run in their own infrastructure. Automation tools (first of all n8n) send it a file over HTTP and get back structured invoice data, the detected format, the original XML and a validation report.

It is a reusable building block for EffizienzAgent's consulting offer (self-hosted, set up and operated for the client), not a hosted SaaS. A typical consumer is an n8n workflow that receives incoming invoices, stores the extracted data in a database and uses OCR only for documents that are not e-invoices.

## Goals

- Accept a PDF (ZUGFeRD 2.x / Factur-X) or an XML file (CII or UBL, Invoice or CreditNote) and detect the format automatically.
- Return one uniform JSON for all formats, with at least: invoice number, issue date, type, currency, net / VAT / gross / amount due, VAT breakdown, seller and buyer, and everything else EN 16931 defines that the invoice contains (see field list).
- Return the detected format (syntax, container, standard, profile) and the original XML.
- Validate in the same call (XSD + official Schematron business rules) and return `valid` plus messages.
- Stable, versioned HTTP API (`/v1`); the engine underneath can change without breaking consumers.
- Small image, low memory, starts fast, runs on amd64 and arm64.

## Non-goals (v1)

- Creating e-invoices (other tools do that, e.g. gflohr/e-invoice-eu).
- OCR or reading PDFs without embedded XML.
- Rendering invoices as PDF/HTML.
- Storing anything: the service is stateless.
- Accounting logic (accounts, cost centres, matching to suppliers or payments) — that is the consumer's job.
- PDF/A-3 conformance checks of the PDF container.
- ZUGFeRD 1.0 (old namespace) and Peppol BIS-specific rules.
- Hosting it ourselves as a multi-tenant SaaS.

## Anti-goals

- Recalculating amounts and returning numbers that differ from the XML. Totals come straight from the XML, always.
- Rejecting a readable invoice because it fails validation. Invalid but readable → data plus `valid: false`.
- Shifting dates by a day through time-zone conversion. Dates are plain calendar dates.
- An API whose shape depends on the input format.

## Constraints and decisions made with Tobias

- **Stack:** Python. `factur-x` (Akretion, BSD) to extract the XML from PDFs and for CII XSD checks; `factur-x`'s `parse_ubl_cii_xml()` plus `lxml` for reading CII and UBL; `saxonche` (SaxonC-HE, MPL-2.0, wheels for linux amd64/arm64) to run the official Schematron rules compiled to XSLT. No Java, no Mustang.
- **Field reading** *(updated during planning, 2026-10-26)*: `factur-x` ≥ 7.7 ships `parse_ubl_cii_xml()`. It reads CII and UBL into a dict keyed by BT/BG numbers, with amounts as strings exactly as written. Tested on all 86 KoSIT test-suite files, including the three CII files where Mustang failed, with no error. Our code maps that dict to the result document in a single mapper. Fields `factur-x` does not return are read with our own XPath (format detection, XMP metadata).
- **Saxon runs in its own process** *(updated during planning)*: in testing, `saxonche` crashed the Python process when it was called from a thread other than the one that created it. So all Schematron runs go through one dedicated worker process (`spawn`). If that process dies, it is restarted, and the request returns `validation.performed: false` with a warning instead of an error. Measured: about 17 ms per check, 0.2 s start-up.
- **Amounts from XML only:** BT-106, BT-107, BT-108, BT-109, BT-110, BT-112, BT-113, BT-114, BT-115 are read, never computed.
- **Invalid invoices** still return data with `valid: false` and the rule messages.
- **Repository:** `github.com/EffizienzAgent/einvoice-reader`, local `~/Projekte/einvoice-reader`, open source under Apache-2.0 *(Claude's call, Tobias did not object)*. Vendored rule sets keep their own licences (EUPL-1.2, Apache-2.0).

## API

Base path `/v1`. OpenAPI is generated and served at `/docs` and `/openapi.json`.

### `POST /v1/invoices/parse`

Input, either:
- `multipart/form-data` with field `file` (what n8n's HTTP Request node sends), or
- raw body with `Content-Type: application/pdf`, `application/xml` or `text/xml`.

Format detection uses the file content (`%PDF` magic, XML root element), not the file name or content type.

Query parameters:
- `validate` (bool, default `true`) — skip validation for speed when false.
- `include_xml` (bool, default `true`) — include the original XML string.

Responses:
- `200` — an e-invoice was read. Body: the result document below, whether valid or not.
- `422` — no e-invoice could be read. Body: `{ "error": { "code": "...", "message": "..." }, "format": {...partial or null} }`. Codes:
  - `NO_EMBEDDED_XML` — PDF without (recognised) invoice XML. This is the normal "send it to OCR" case.
  - `UNSUPPORTED_FORMAT` — XML that is neither CII nor UBL Invoice/CreditNote, or ZUGFeRD 1.0.
  - `MALFORMED_XML` — not well-formed XML.
  - `MALFORMED_PDF` — not a readable PDF.
  - `EMPTY_FILE`
- `413` — file larger than `MAX_UPLOAD_MB`.
- `401` — API key required and missing/wrong.

### `GET /v1/health`

`{ "status": "ok" }` for container health checks.

### `GET /v1/info`

Service version and the versions of the bundled rule sets (EN 16931 Schematron, XRechnung Schematron, ZUGFeRD/Factur-X Schematron).

## Result document

JSON, camelCase keys, dates as `YYYY-MM-DD` strings, amounts as JSON numbers exactly as written in the XML (no rounding), absent values as `null`, absent lists as `[]`. All keys are always present so consumers can rely on the shape.

```json
{
  "format": {
    "syntax": "CII",                 // CII | UBL
    "container": "PDF",              // PDF | XML
    "documentType": "Invoice",       // Invoice | CreditNote (UBL root); CII always Invoice
    "standard": "ZUGFeRD",           // ZUGFeRD | Factur-X | XRechnung | EN16931 | Peppol | unknown
    "profile": "EN16931",            // MINIMUM | BASIC WL | BASIC | EN16931 | EXTENDED | XRECHNUNG | null
    "version": "2.3",                // standard version if derivable, else null
    "customizationId": "urn:cen.eu:en16931:2017",   // BT-24 verbatim
    "embeddedFileName": "factur-x.xml"              // PDF only
  },
  "invoice": {
    "number": "471102",              // BT-1
    "typeCode": "380",               // BT-3 verbatim
    "type": "invoice",               // invoice | credit_note | corrected | prepayment | self_billed | other
    "issueDate": "2026-03-05",       // BT-2
    "dueDate": null,                 // BT-9
    "taxPointDate": null,            // BT-7
    "currency": "EUR",               // BT-5
    "taxCurrency": null,             // BT-6
    "buyerReference": null,          // BT-10 (Leitweg-ID)
    "orderReference": null,          // BT-13
    "salesOrderReference": null,     // BT-14
    "contractReference": null,       // BT-12
    "projectReference": null,        // BT-11
    "precedingInvoices": [],         // BG-3: [{ "number", "issueDate" }]
    "deliveryDate": null,            // BT-72
    "invoicingPeriod": null,         // BG-14: { "start", "end" }
    "notes": [],                     // BG-1: [{ "subjectCode", "text" }]
    "paymentTerms": null,            // BT-20
    "payment": {
      "meansCode": "58",             // BT-81
      "remittanceInformation": null, // BT-83
      "accounts": [],                // BG-17: [{ "iban", "bic", "name" }], one entry per payment means
      "card": null,                  // BG-18: { "lastDigits", "holder" }
      "directDebit": null            // BG-19: { "mandateReference", "creditorId", "debitedAccount" }
    },
    "seller": "<Party>",             // BG-4
    "buyer": "<Party>",              // BG-7
    "payee": null,                   // BG-10: { "name", "identifier", "legalRegistrationId" }
    "taxRepresentative": null,       // BG-11: { "name", "vatId", "address" }
    "delivery": null,                // BG-13: { "partyName", "locationId", "address" }
    "totals": {
      "lineNetTotal": 314.86,        // BT-106
      "allowanceTotal": 0,           // BT-107
      "chargeTotal": 0,              // BT-108
      "netTotal": 314.86,            // BT-109
      "vatTotal": 22.04,             // BT-110 (invoice currency)
      "vatTotalTaxCurrency": null,   // BT-111
      "grossTotal": 336.90,          // BT-112
      "prepaidAmount": null,         // BT-113
      "roundingAmount": null,        // BT-114
      "amountDue": 336.90            // BT-115
    },
    "vatBreakdown": [                // BG-23
      { "categoryCode": "S", "rate": 7, "taxableAmount": 314.86, "taxAmount": 22.04,
        "exemptionReason": null, "exemptionReasonCode": null }
    ],
    "allowancesCharges": [],         // BG-20/BG-21: [{ "isCharge", "amount", "baseAmount", "percent",
                                     //   "reason", "reasonCode", "vatCategoryCode", "vatRate" }]
    "lines": [                       // BG-25
      { "id": "1", "name": "...", "description": null, "note": null,
        "quantity": 20, "unitCode": "H87",
        "netPrice": 9.9, "grossPrice": null, "priceBaseQuantity": null,
        "netAmount": 198, "vatCategoryCode": "S", "vatRate": 7,
        "sellerItemId": null, "buyerItemId": null, "standardItemId": null,
        "orderLineReference": null, "period": null, "allowancesCharges": [] }
    ],
    "attachments": []                // BG-24 metadata only: [{ "id", "description", "fileName", "mimeType", "uri" }]
  },
  "validation": {
    "performed": true,
    "valid": true,                   // no message with severity "error"
    "isEInvoice": true,              // valid AND profile is not MINIMUM / BASIC WL
    "ruleSets": ["XSD CII D22B", "EN16931 CII 1.3.x", "XRechnung CII 3.0.x"],
    "messages": [                    // [{ "severity": "error|warning|info", "ruleId", "message", "location" }]
    ]
  },
  "warnings": [],                    // non-validation notes from reading, e.g. "BT-110 missing, vatTotal is null"
  "xml": "<?xml ...>",
  "meta": { "serviceVersion": "1.0.0" }
}
```

`<Party>`:

```json
{
  "name": "Lieferant GmbH",                // BT-27 / BT-44
  "tradingName": null,                     // BT-28 / BT-45
  "identifiers": [],                       // BT-29 / BT-46: [{ "value", "scheme" }]
  "legalRegistrationId": null,             // BT-30 / BT-47: { "value", "scheme" }
  "vatId": "DE123456789",                  // BT-31 / BT-48
  "taxNumber": null,                       // BT-32 (seller only)
  "electronicAddress": null,               // BT-34 / BT-49: { "value", "scheme" }
  "address": { "line1", "line2", "line3", "city", "postalCode", "countrySubdivision", "countryCode" },
  "contact": null                          // BG-6 / BG-9: { "name", "phone", "email" }
}
```

Mapping rules that matter:
- **UBL supplier name:** take `PartyLegalEntity/RegistrationName` (BT-27) for `name` and `PartyName/Name` (BT-28) for `tradingName`; never leave `name` empty when one of them exists (fall back to the other and add a warning).
- **UBL CreditNote:** root `CreditNote`, lines are `CreditNoteLine` with `CreditedQuantity`; map to the same model, `documentType: "CreditNote"`.
- **Credit notes:** amounts are passed through as written (usually positive). `type` tells the consumer it is a credit note; the service never flips signs.
- **CII dates:** `udt:DateTimeString` format `102` → `YYYY-MM-DD`. Formats `610`/`616` (month/week) only appear in periods; return the first day and add a warning.
- **VAT IDs:** CII `SpecifiedTaxRegistration/ID[@schemeID='VA']` → `vatId`, `[@schemeID='FC']` → `taxNumber`.
- **Several BT-110 elements in CII** (one per currency): the one whose `currencyID` equals BT-5 is `vatTotal`, the one equal to BT-6 is `vatTotalTaxCurrency`.
- Unknown or unmapped elements are ignored; the original XML is always available in `xml`.

## Format detection

1. Content starts with `%PDF` → use `factur-x` to get the embedded XML (it knows `factur-x.xml`, `zugferd-invoice.xml`, `xrechnung.xml`, `order-x.xml`). Fallback: any embedded file whose XML root is CII or UBL. None found → `422 NO_EMBEDDED_XML`.
2. XML root:
   - `{urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100}CrossIndustryInvoice` → CII
   - `{urn:oasis:names:specification:ubl:schema:xsd:Invoice-2}Invoice` → UBL Invoice
   - `{urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2}CreditNote` → UBL CreditNote
   - `CrossIndustryDocument` (ZUGFeRD 1.0) → `422 UNSUPPORTED_FORMAT`
3. `standard` and `version` *(Claude's call)*: for PDFs they come from the XMP metadata (`fx:DocumentType`, `fx:Version`, and whether the namespace is ZUGFeRD or Factur-X). Plain CII XML carries no such hint, so it gets `standard: "Factur-X"` and `version: null` unless BT-24 identifies XRechnung or plain EN 16931.
4. Standard and profile from BT-24 (CII `GuidelineSpecifiedDocumentContextParameter/ID`, UBL `CustomizationID`), via a lookup table (e.g. `urn:factur-x.eu:1p0:minimum` → MINIMUM, `…#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0` → XRECHNUNG, `urn:cen.eu:en16931:2017#conformant#urn:factur-x.eu:1p0:extended` → EXTENDED, Peppol BIS → Peppol). Unknown ID → `standard: "unknown"`, still parse.

## Validation

Layers, all collected into `validation.messages`:
1. **XSD** for the detected syntax (CII D22B / D16B as shipped with the Factur-X/ZUGFeRD package, UBL 2.1).
2. **Schematron**, chosen by format:
   - ZUGFeRD/Factur-X profiles MINIMUM, BASIC WL, BASIC, EN16931, EXTENDED → the profile's Schematron from the official Factur-X/ZUGFeRD package.
   - Plain EN 16931 CII or UBL → CEN rules (`ConnectingEurope/eInvoicing-EN16931`).
   - XRechnung → CEN rules + KoSIT XRechnung rules (`itplr-kosit/xrechnung-schematron`).
3. `valid` is true when there is no message with severity `error` for the rules of the declared profile. Schematron `warning`/`information` flags map to `warning`/`info`.
4. `isEInvoice` (decided with Tobias): `false` for ZUGFeRD/Factur-X MINIMUM and BASIC WL, because German law does not accept them as e-invoices. For every other profile it equals `valid`. Consumers such as n8n filter on this field, not on `valid`.

Rule sets are pinned to specific versions and a script updates them. Each XSLT is compiled once at start-up and reused. `GET /v1/info` reports the versions.

Which versions ship *(Claude's call)*: one rule set per family, the current one. XRechnung 2.x has not been accepted since 2024, so only 3.0.x ships. ZUGFeRD 2.1–2.3 and Factur-X 1.0.x use the same identifiers, and their rules are backwards compatible, so the newest package validates all of them. If an invoice declares an XRechnung version older than the bundled rules, it gets a `warning` that names the version. A second version is added only when a real invoice needs it, and then through a lookup table from BT-24 to rule set.

Where the files come from *(updated during planning)*: the `factur-x` package already contains the Factur-X 1.09 XSD and Schematron XSLT for every profile, plus the UBL 2.1 XSD and the CEN `EN16931-UBL-validation.xslt`. They are used straight from the installed package, nothing is committed. Only the KoSIT XRechnung Schematron (release v2.6.0, Apache-2.0, two XSL files) is committed under `rules/xrechnung/` together with its licence. Sample PDFs are generated from the public CII examples with the `factur-x` library.

## Security and operation

- Stateless; nothing is written to disk except temp files that are deleted per request.
- XML parsed with entity resolution and network access disabled (no XXE).
- `MAX_UPLOAD_MB` (default 20).
- Optional `API_KEY` env var; when set, requests need header `X-API-Key`. Health endpoint stays open.
- Container runs as non-root, listens on port 8080, logs to stdout without invoice content.
- Image published to `ghcr.io/effizienzagent/einvoice-reader`, multi-arch (amd64, arm64); see CI/CD.

## Testing

- Fixtures: KoSIT XRechnung test suite, CEN EN 16931 examples, ZUGFeRD/Factur-X sample package (all profiles, PDFs). Each fixture has a golden JSON; the test compares the full result. Fixture licences are checked and kept in the repo.
- Specific regression cases from the earlier Mustang spike, all from the public KoSIT test suite and examples (no client invoices): CII invoices whose totals do not match recomputed sums, UBL supplier name in RegistrationName only, dates near midnight/time zones.
- A non-e-invoice PDF and malformed XML/PDF for the 422 paths.
## CI/CD (GitHub Actions)

Requested by Tobias: fast workflows, automatic releases, container images built by CI.

- **On every push and PR (`ci.yml`)**, target under 2 minutes: `uv` with a cached environment, `ruff` lint and format check, `pytest`. Jobs run in parallel, and a newer push cancels older runs.
- **Image check on PRs:** build the image for amd64 only (with the GHA layer cache), start it, and run a smoke test: `GET /v1/health`, then parse one PDF and one UBL fixture.
- **Releases (`release.yml`):** release-please on `main` reads Conventional Commits, keeps a release PR with version bump and `CHANGELOG.md`, and creates the tag and GitHub release when the PR is merged. *(Claude's call: release-please over semantic-release, because the release only happens when someone merges the PR.)*
- **Image publish on release tags:** native runners per architecture (`ubuntu-24.04` and `ubuntu-24.04-arm`, no QEMU), then a manifest merge, pushed to `ghcr.io/effizienzagent/einvoice-reader` with tags `X.Y.Z`, `X.Y`, `X`, `latest`. The image includes an SBOM and provenance attestations. `main` also publishes an `edge` tag.
- **Maintenance:** Renovate (or Dependabot) for Python packages, the base image and the Actions. A weekly scheduled workflow checks for new CEN/KoSIT rule releases and opens an issue when one appears.
- Commits follow Conventional Commits (`feat:`, `fix:`, `chore:`), and a PR-title check enforces it.

## Versioning

- API path `/v1`. Within v1 only additive changes (new keys); never rename or remove keys or change types.
- Semantic versioning for the image. Rule-set updates are minor releases.

## Later

- **v1.x, in this service:** human-readable view (`POST /v1/invoices/render` → HTML/PDF) for XML-only invoices, built from the same parsed model.
- **Separate services, not in this repo** (decided with Tobias): OCR/LLM extraction for non-e-invoices returning the same result document, outgoing e-invoice generation, bank statement parsing (CAMT.053/MT940), master-data checks (VIES, IBAN, Leitweg-ID). Bundles for clients live in `customer-stacks`. Keep the result document free of e-invoice-only assumptions where that costs nothing, so the OCR service can reuse it.

## Integration example (not part of this repo)

An n8n workflow sends each incoming document to `POST /v1/invoices/parse`. A 200 fills the invoice record (number, date, amounts, supplier, format, validity); a 422 `NO_EMBEDDED_XML` routes the document to the existing OCR or manual path.

## Left to the builder

- Internal module layout, Pydantic models, error class design.
- Exact XSD/Schematron versions and the update script.
- Base image choice and how to keep the image small.
- Whether uvicorn runs with one or several workers (saxonche thread safety decides).

## Update 2026-10-26: KoSIT scenarios

XRechnung and plain EN 16931 invoices are validated exactly as the official KoSIT validator does it. The KoSIT validator configuration is vendored under `src/einvoice_reader/rules/kosit` (Apache-2.0, with the CEN Schematron under EUPL-1.2). Its `scenarios.xml` decides which rule sets run for a given CustomizationID. Its `customLevel` severity overrides are applied to the messages. Example: in the standard scenario BR-CL-21/23 drop to `warning` and CII-SR-452..466 rise to `error`. Extension and CVD scenarios relax further rules. Documents without a matching scenario fall back to the earlier selection (Factur-X profile rules, or CEN EN 16931). Previously the vendored CEN 1.3.17 rules without overrides failed 4 KoSIT test-suite files that the official validator accepts.

## Update 2026-10-26: audit results

Two audits (security, edge cases) led to these changes in the contract and behaviour:

- **Additive fields:** `invoice.isCreditNote`, `lines[].parentLineId`, `lines[].lineStatusReasonCode`. `payment.accounts` lists every account, not only the first.
- **New response codes:**
  - 422 `PROCESSING_TIMEOUT` when reading takes longer than `PARSE_TIMEOUT_S`
  - 400 `BAD_REQUEST` for a malformed multipart body
  - 503 `SERVICE_BUSY` when the queue is full
  - `NO_EMBEDDED_XML` now also covers images, Office files and other non-XML input, so it is the single "send to OCR" code
- **`format.version`:** comes from BT-24 only (XRechnung `3.0`, ZUGFeRD `2.0`). The XMP schema version is no longer reported. Factur-X/ZUGFeRD 2.1+ PDFs therefore have `version: null`.
- **Dates:** ISO dates in 102 fields, the 610/616 formats and UBL dates with a time zone are normalised. An unreadable optional date is dropped with a warning instead of failing the request.
- **Validation:** non-Factur-X CII is schema-checked against the full CII D22B XSD that `factur-x` ships, so the KoSIT rules decide. `ruleSets` names the XSD only by syntax (`XSD CII`, `XSD UBL`).
- **Isolation:** document parsing runs in a spawned worker pool with a hard timeout, like Schematron. Currency codes are restricted to `[A-Z]{3}` before `factur-x` uses them in XPath. PDF decompression is limited to 25 MB per stream and 60 MB per document.
- **ZUGFeRD 1.0 inside a PDF** returns `NO_EMBEDDED_XML` (OCR route), not `UNSUPPORTED_FORMAT`.

## Update 2026-10-26: second audit

- **New 422 codes:**
  - `READ_FAILED`: a recognised CII/UBL invoice that could not be mapped. Previously `UNSUPPORTED_FORMAT`.
  - `PROCESSING_FAILED`: the document crashed the reader twice. Do not retry. Only a full queue returns 503 `SERVICE_BUSY`.
- **Routing:**
  - `NO_EMBEDDED_XML` also covers HTML, SVG and any XML outside the e-invoice namespaces.
  - `UNSUPPORTED_FORMAT` is limited to e-invoice-family documents we do not read (ZUGFeRD 1.0, Order-X, other UBL/CII documents).
  - `MALFORMED_XML` is used only for input that declares XML or carries CII/UBL markers.
- **Robustness:**
  - Attachments (BG-24) are not base64-decoded.
  - `udt:DateString` dates (BT-7) are normalised like other dates.
  - A missing BT-5 gets a placeholder; `currency` is `null` and the warning is `BT-5 missing`.
- **KoSIT fallback:** XRechnung CustomizationIDs without an exact scenario (2.x, different spelling, CreditNote with extension URN) use the severity levels of the matching 3.0 or extension scenario. A matching KoSIT scenario always means the full CII D22B XSD, also inside Factur-X PDFs.
- **Validation warnings** distinguish three cases:
  - "validation timed out for this document"
  - "validation queue full, retry later"
  - "validation failed for this document"
- **UBL sub-lines** (`SubInvoiceLine`) are not listed. A warning says so, and top-level lines never get a `parentLineId` from them.
- **Additive fields:**
  - `invoice`: `buyerAccountingReference` (BT-19), `invoicedObjectIdentifier` (BT-18), `receivingAdviceReference` (BT-15), `despatchAdviceReference` (BT-16), `tenderReference` (BT-17), `businessProcess` (BT-23)
  - `payment`: `meansText` (BT-82)
  - `lines[]`: `accountingReference` (BT-133), `objectIdentifier` (BT-128), `attributes` (BG-32), `classifications` (BT-158)
- **Other:**
  - `customizationId` is verbatim.
  - Type code 325 (proforma) maps to `other`.
  - `PARSE_TIMEOUT_S` defaults to 30.
  - With several invoice XMLs in one PDF, a full profile is preferred over MINIMUM/BASIC WL.
