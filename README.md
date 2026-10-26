# einvoice-reader

A small, self-hosted HTTP service that reads incoming German and European e-invoices and returns **one uniform JSON document**, whatever the input format, and validates them in the same call:

- ZUGFeRD 2.x / Factur-X PDFs (CII XML embedded in a PDF/A-3), all profiles from MINIMUM to EXTENDED
- XRechnung 3.x as CII or UBL XML (Invoice and CreditNote)
- Plain EN 16931 CII or UBL XML

Amounts come straight from the XML and are never recalculated. Validation uses the official rule sets: XSD, the KoSIT validator configuration for XRechnung and EN 16931 (same scenarios and severity overrides as the official KoSIT validator), and the Factur-X Schematron per profile.

## Quick start

```bash
docker run -p 127.0.0.1:8080:8080 -e API_KEY=change-me ghcr.io/effizienzagent/einvoice-reader:latest
curl -F file=@invoice.pdf http://localhost:8080/v1/invoices/parse
```

For production, pin a version from the [releases](https://github.com/EffizienzAgent/einvoice-reader/releases) instead of `latest` and upgrade on purpose: a new version can bring updated validation rules that change `valid` for the same invoice.

The OpenAPI documentation is at `http://localhost:8080/docs`.

## API

| Endpoint | Purpose |
|---|---|
| `POST /v1/invoices/parse` | Read and validate one invoice |
| `GET /v1/health` | Health check, always open |
| `GET /v1/info` | Service version and rule set versions |

`POST /v1/invoices/parse` accepts the file in either of two ways:

- as `multipart/form-data` in the field `file`
- as the raw request body (`application/pdf`, `application/xml`, `text/xml`)

The service detects the format from the content, not from the file name.

Query parameters:

- `validate` (default `true`): set to `false` to skip validation.
- `include_xml` (default `true`): set to `false` to leave out the original XML.

Responses:

| Status | Meaning |
|---|---|
| `200` | Invoice read. Check `validation.valid` and `validation.isEInvoice`. An invalid but readable invoice still returns `200`. |
| `422` | No e-invoice could be read. `error.code` is one of the values below. |
| `400` | Malformed multipart body (`BAD_REQUEST`) |
| `401` | `API_KEY` is set and the `X-API-Key` header is missing or wrong |
| `408` | Upload took longer than `READ_TIMEOUT_S` (`REQUEST_TIMEOUT`) |
| `413` | File larger than `MAX_UPLOAD_MB` |
| `503` | Too many documents queued (`SERVICE_BUSY`); retry later |

`error.code` values for `422`:

| Code | Meaning | Typical next step |
|---|---|---|
| `NO_EMBEDDED_XML` | Not an e-invoice: a PDF without invoice XML, an image, an Office file, HTML, a scan, XML that is not an invoice | Send to OCR |
| `MALFORMED_PDF` | The PDF cannot be opened | Send to OCR |
| `UNSUPPORTED_FORMAT` | An e-invoice family document this service does not read: ZUGFeRD 1.0, Order-X, other UBL documents | Manual check |
| `MALFORMED_XML` | An XML invoice that is not well-formed or contains a DOCTYPE | Ask the supplier for a corrected file |
| `READ_FAILED` | A recognised CII/UBL invoice that could not be mapped | Manual check |
| `EMPTY_FILE` | The file is empty, or the multipart body has no `file` field | Check the upload |
| `PROCESSING_TIMEOUT` | Reading the document took longer than `PARSE_TIMEOUT_S` | Manual check; do not retry automatically |
| `PROCESSING_FAILED` | Reading this document crashed the reader | Manual check; do not retry automatically |

## Result document

```json
{
  "format": { "syntax": "CII", "container": "PDF", "documentType": "Invoice",
              "standard": "XRechnung", "profile": "XRECHNUNG", "version": "3.0",
              "customizationId": "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0",
              "embeddedFileName": "factur-x.xml" },
  "invoice": {
    "number": "123456XX", "type": "invoice", "isCreditNote": false, "issueDate": "2016-04-04", "currency": "EUR",
    "seller": { "name": "[Seller name]", "vatId": "DE 123456789", "...": "..." },
    "totals": { "netTotal": 314.86, "vatTotal": 22.04, "grossTotal": 336.9, "amountDue": 336.9, "...": "..." },
    "vatBreakdown": [ { "categoryCode": "S", "rate": 7.0, "taxableAmount": 314.86, "taxAmount": 22.04 } ],
    "lines": [ "..." ]
  },
  "validation": { "performed": true, "valid": true, "isEInvoice": true,
                  "ruleSets": ["XSD CII", "EN16931 CII (KoSIT 2026-08-31)", "XRechnung CII 3.0.2 (KoSIT 2026-08-31)"],
                  "messages": [] },
  "warnings": [],
  "xml": "<?xml ...",
  "meta": { "serviceVersion": "0.1.0" }
}
```

Every key is always present. Missing values are `null` and missing lists are `[]`. Dates are `YYYY-MM-DD`. The full field list is in the [design document](docs/superpowers/specs/2026-10-26-einvoice-reader-design.md).

Things to know when you book from this data:

- **`isEInvoice`** is `false` for ZUGFeRD/Factur-X MINIMUM and BASIC WL, because German law does not accept those profiles as e-invoices. Filter on `isEInvoice`, not on `valid`.
- **`valid` and `isEInvoice` can be `null`.** That happens when `validate=false`, or when validation could not run in time (`validation.performed` is `false`, and `warnings` says why). Treat `null` as "unknown, check again", never as `false`.
- **Credit notes** keep the amounts as written, usually positive. Use `isCreditNote` (or `type`) to flip the sign yourself. A credit note that already carries negative amounts must not be flipped again.
- **Several bank accounts** are all listed in `payment.accounts`. If `payee` is set, the accounts belong to the payee (factoring), not the seller. On credit notes the account can be your own (the buyer's) account to receive the refund.
- **IBAN and BIC** are returned without spaces and in upper case. A value that does not look like an IBAN (for example a proprietary account number) stays as written. An IBAN that fails its checksum still comes through, but with a warning.
- **`payment.meansCode` 59** means SEPA direct debit: the supplier collects, do not transfer.
- **Sub-lines:** lines with a `parentLineId` are sub-lines (ZUGFeRD EXTENDED, XRechnung extension CII). Do not add them on top of their parent, and never sum lines with `lineStatusReasonCode` `INFORMATION`; use `totals` for amounts. UBL `SubInvoiceLine`s are not listed (a warning says so).
- **Booking references:** `buyerAccountingReference` (BT-19), `invoicedObjectIdentifier` (BT-18, e.g. a VIN or contract), and per line `accountingReference`, `objectIdentifier`, `attributes` and `classifications`.
- **`warnings`** lists everything the reader had to repair or ignore: unreadable dates, repeated fields, invalid currency codes, a second invoice XML in the PDF, `BT-115` not matching the other totals.

## Using it from n8n

Add an **HTTP Request** node and configure it like this:

- Method: `POST`
- URL: `http://einvoice-reader:8080/v1/invoices/parse`
- Body Content Type: `Form-Data`
- Body parameter: type `n8n Binary File`, name `file`, input data field `data` (or whichever field holds your file)
- If you check the `422` codes yourself: Options → Response → enable *Include Response Headers and Status* and *Never Error*
- Route `422` with `NO_EMBEDDED_XML` or `MALFORMED_PDF` to your OCR branch, every other `422` to manual review. Retry only `503`, with a pause.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MAX_UPLOAD_MB` | `20` | Upload limit |
| `API_KEY` | unset | When set, requests need the header `X-API-Key` |
| `LOG_LEVEL` | `INFO` | Python log level; the service does not log invoice content |
| `PARSE_TIMEOUT_S` | `30` | Hard time limit for reading one document (PDF, XML, mapping, XSD) |
| `PARSE_WORKERS` | `2` | Processes that read documents in parallel |
| `PARSE_MEMORY_MB` | `1024` | Memory limit per parser process (Linux); a document that needs more ends with `PROCESSING_FAILED` |
| `READ_TIMEOUT_S` | `60` | Time limit for receiving an upload |
| `VALIDATION_TIMEOUT_S` | `30` | Time limit per Schematron run; queued requests give up after twice this |
| `MAX_CONCURRENT_REQUESTS` | `16` | Uploads processed at the same time; more wait |
| `EXPOSE_DOCS` | `true` without `API_KEY`, else `false` | Serve `/docs`, `/redoc` and `/openapi.json` |
| `WORKERS` | `1` | uvicorn worker processes; each starts its own parser and Schematron workers |

Memory: about 400 MB per uvicorn worker at rest (Schematron worker with all rules compiled, plus the parser workers). Each parser worker is capped at `PARSE_MEMORY_MB`, and memory a worker once used is not always returned to the system. Size the container with `PARSE_WORKERS × PARSE_MEMORY_MB + 1 GB` per uvicorn worker, so 3 GB with the defaults (`mem_limit: 3g` in Compose). Very large invoices (tens of thousands of lines) need about 550 MB in a parser worker and 600 MB in the Schematron worker. On slow CPUs raise `PARSE_TIMEOUT_S` if they hit `PROCESSING_TIMEOUT`.

Network: the service is meant for internal networks. Bind it to localhost or a private Docker network, set `API_KEY`, and put a reverse proxy with request timeouts in front if untrusted clients can reach it.

## Development

```bash
uv sync
uv run pytest            # fast suite
uv run pytest -m slow    # 5,000-run Schematron soak test
scripts/update_rules.sh  # pull the latest KoSIT validator configuration
```

Releases are created automatically from Conventional Commits via release-please. Images for amd64 and arm64 are published to GHCR.

Repository setup, needed once for releases to work:

- Settings → Actions → General: enable **Allow GitHub Actions to create and approve pull requests**. release-please opens its release PR with the default `GITHUB_TOKEN`. CI does not run on that PR (GitHub never triggers workflows from `GITHUB_TOKEN` events); it only changes the version and `CHANGELOG.md`, and the image is built after the merge.
- After the first image push, set the GHCR package `einvoice-reader` to **public** so that anonymous `docker pull` works.

## Licences

The code is licensed under Apache-2.0. Bundled and installed third-party components keep their own licences; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). In short:

- KoSIT validator configuration (`src/einvoice_reader/rules/kosit`): Apache-2.0. The CEN EN 16931 Schematron it contains (`resources/cii`, `resources/ubl`) is EUPL-1.2, see `LICENSE-EUPL-1.2-CEN.txt`
- KoSIT XRechnung test suite (`tests/fixtures/kosit`): Apache-2.0
- The Factur-X rules ship with the [`factur-x`](https://github.com/akretion/factur-x) package (BSD)
