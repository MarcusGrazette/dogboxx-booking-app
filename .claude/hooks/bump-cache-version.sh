#!/bin/bash
# Automatically bump sw.js CACHE_VERSION when watched static assets are edited.
# Triggered by PostToolUse hook on Edit/Write tools.
#
# Watched files (per CLAUDE.md):
#   brand.css, reusable-calendar.css, reusable-calendar.js

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""')

# Only act on the specific watched assets
case "$FILE" in
  */brand.css|*/reusable-calendar.css|*/reusable-calendar.js)
    ;;
  *)
    exit 0
    ;;
esac

SW_JS="/home/marcus/claude/dogboxx-booking-app/app/static/js/sw.js"

# Extract current numeric version from: const CACHE_VERSION = 'vN';
CURRENT=$(grep -oP "const CACHE_VERSION = 'v\K[0-9]+" "$SW_JS")
if [ -z "$CURRENT" ]; then
  echo "bump-cache-version: could not find CACHE_VERSION in sw.js" >&2
  exit 0
fi

NEXT=$((CURRENT + 1))

sed -i "s/const CACHE_VERSION = 'v${CURRENT}'/const CACHE_VERSION = 'v${NEXT}'/" "$SW_JS"

echo "bump-cache-version: sw.js CACHE_VERSION bumped v${CURRENT} → v${NEXT} (triggered by edit to $(basename "$FILE"))"
