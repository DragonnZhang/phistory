#!/usr/bin/env bash
set -eu

scan_dir="${1:-captures/qoder}"
if [ -z "${QODER_PERSONAL_ACCESS_TOKEN:-}" ] || [ ! -d "$scan_dir" ]; then
  exit 0
fi

status=0
grep -ralF -- "$QODER_PERSONAL_ACCESS_TOKEN" "$scan_dir" || status=$?
case "$status" in
  0)
    echo "::error title=Qoder credential leak detected::Refusing to render or commit capture artifacts."
    exit 1
    ;;
  1) ;;
  *)
    echo "::error title=Qoder credential scan failed::Refusing to render or commit unchecked capture artifacts."
    exit "$status"
    ;;
esac
