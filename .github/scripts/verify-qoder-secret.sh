#!/usr/bin/env bash
set -eu

if [ -z "${QODER_PERSONAL_ACCESS_TOKEN:-}" ] || [ ! -d captures/qoder ]; then
  exit 0
fi

status=0
grep -ralF -- "$QODER_PERSONAL_ACCESS_TOKEN" captures/qoder || status=$?
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
