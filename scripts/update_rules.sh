#!/usr/bin/env bash
# Update the vendored KoSIT validator configuration (scenarios, CEN EN16931 and XRechnung XSLT).
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
target="$root/src/einvoice_reader/rules/kosit"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

repo=itplr-kosit/validator-configuration-xrechnung
tag="$(gh release view -R "$repo" --json tagName --jq .tagName)"
gh release download "$tag" -R "$repo" -p '*.zip' -D "$tmp"
unzip -q -o "$tmp"/*.zip -d "$tmp/config"

find "$target/resources" -name '*.xsl' -delete
cp "$tmp/config/scenarios.xml" "$target/"
(cd "$tmp/config" && find resources -path '*/xsl/*.xsl') | while read -r file; do
  mkdir -p "$target/$(dirname "$file")"
  cp "$tmp/config/$file" "$target/$file"
done
gh api "repos/$repo/contents/LICENSE" --jq .content | base64 --decode > "$target/LICENSE"
gh api "repos/$repo/contents/NOTICE" --jq .content | base64 --decode > "$target/NOTICE"
basename "$tmp"/*.zip .zip > "$target/VERSION"
(cd "$target" && find . -type f ! -name SHA256SUMS | sort | xargs shasum -a 256 > SHA256SUMS)

echo "KoSIT validator configuration: $(cat "$target/VERSION")"
