#!/usr/bin/env bash
# Concatenate src/*.js in dependency order (lexical) into dist/pfolio-oec.js.
# No Node toolchain. Mirrors pfolio-ips-tool/build.sh.

set -euo pipefail
cd "$(dirname "$0")"

OUT="dist/pfolio-oec.js"
mkdir -p dist

{
  echo "/* pfolio-oec.js — built bundle. Do not edit; regenerate via build.sh.       */"
  echo "/* See pfolio-io/pfolio-execution-quality (tool/src/) for source.            */"
  echo "(function () {"
  echo "  'use strict';"
  for f in src/*.js; do
    echo ""
    echo "  /* ───── $(basename "$f") ───── */"
    cat "$f"
  done
  echo ""
  echo "})();"
} > "$OUT"

BYTES=$(wc -c < "$OUT" | tr -d ' ')
LINES=$(wc -l < "$OUT" | tr -d ' ')
echo "wrote $OUT  (${BYTES} bytes, ${LINES} lines)"
