# Third-party notices

einvoice-reader is licensed under the Apache License 2.0 (see `LICENSE`). The source tree and the Docker image contain or install the following third-party components under their own licences.

## Bundled in this repository

| Component | Path | Licence |
|---|---|---|
| KoSIT validator configuration for XRechnung (scenarios, XRechnung Schematron XSLT) | `src/einvoice_reader/rules/kosit` | Apache-2.0, `rules/kosit/LICENSE` and `rules/kosit/NOTICE` |
| CEN EN 16931 validation artefacts (Schematron XSLT, shipped inside the KoSIT configuration) | `src/einvoice_reader/rules/kosit/resources/cii`, `.../ubl` | EUPL-1.2, `rules/kosit/LICENSE-EUPL-1.2-CEN.txt` |
| KoSIT XRechnung test suite | `tests/fixtures/kosit` | Apache-2.0, `tests/fixtures/kosit/LICENSE` |

The EUPL-1.2 files are redistributed unmodified as separate files. Changes to them would fall under the EUPL-1.2.

## Installed into the Docker image

| Package | Licence | Notes |
|---|---|---|
| saxonche (SaxonC-HE by Saxonica) | MPL-2.0, text in `licenses/MPL-2.0.txt` | "The Saxon XSLT and XQuery Processor from Saxonica Limited", https://www.saxonica.com/. Source: https://www.saxonica.com/download/c.xml. The wheel contains a GraalVM Native Image, which includes parts of the GraalVM/OpenJDK runtime under GPL-2.0 with Classpath Exception; source: https://github.com/oracle/graal and https://github.com/openjdk/jdk |
| factur-x (Akretion) | BSD | Ships the Factur-X/ZUGFeRD XSD and Schematron published by FNFE-MPE and FeRD, based on UN/CEFACT CII schemas |
| python-stdnum | LGPL-2.1-or-later | Used unmodified as a Python library (dependency of factur-x). Source: https://github.com/arthurdejong/python-stdnum |
| certifi | MPL-2.0, text in `licenses/MPL-2.0.txt` | |
| cryptography | Apache-2.0 OR BSD-3-Clause | The wheel bundles OpenSSL (Apache-2.0) and Rust crates under their own licences, listed in its `dist-info` |
| cffi | MIT-0 | |
| pycparser | BSD-3-Clause | |
| lxml, pypdf, starlette, uvicorn, click, idna | BSD-3-Clause | |
| fastapi, pydantic, pydantic-core, anyio, h11, urllib3, charset-normalizer, annotated-types, annotated-doc, typing-inspection | MIT | |
| python-multipart, requests, opentelemetry-api | Apache-2.0 | |
| typing-extensions | PSF-2.0 | |
| iso4217 | Public Domain | |

Every Python package keeps its own licence files in its installed `*.dist-info` directory inside the image.

## Base images

The Docker image is built on `python:3.12-slim-bookworm` (Debian bookworm). The Debian packages it contains keep their own licences, listed under `/usr/share/doc/*/copyright` in the image. The CPython licence (PSF-2.0) is under `/usr/local/lib/python3.12/LICENSE.txt`.
