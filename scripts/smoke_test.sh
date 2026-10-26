#!/usr/bin/env bash
# Start the image, wait for health, parse two fixtures, clean up.
set -euo pipefail

image="${1:?usage: smoke_test.sh <image>}"
root="$(cd "$(dirname "$0")/.." && pwd)"
port="${SMOKE_PORT:-18080}"
name="einvoice-smoke-$$"

docker run -d --rm --name "$name" -p "$port:8080" "$image" >/dev/null
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT

for _ in $(seq 1 30); do
  curl -sf "http://localhost:$port/v1/health" >/dev/null && break
  sleep 1
done
curl -sf "http://localhost:$port/v1/health" >/dev/null || { docker logs "$name"; exit 1; }

for file in tests/fixtures/pdf/en16931.pdf \
            tests/fixtures/kosit/business-cases/standard/01.01a-INVOICE_ubl.xml; do
  number="$(curl -sf -F "file=@$root/$file" "http://localhost:$port/v1/invoices/parse" \
    | jq -er '.invoice.number')"
  echo "ok: $file -> $number"
done
