#!/usr/bin/env bash
# Create an unprivileged user that runs model-written code (ADR-005, ADR-009).
# It cannot read the runner user's /proc/<pid>/environ, so it cannot see GEMINI_API_KEY.
set -euo pipefail
sudo useradd --system --no-create-home autose-sandbox 2>/dev/null || true
# let the sandbox user traverse to and write in the workspace
d="$GITHUB_WORKSPACE"
while [ "$d" != "/" ]; do sudo chmod o+x "$d" || true; d=$(dirname "$d"); done
chmod -R a+rwX "$GITHUB_WORKSPACE"
PY=$(python -c 'import sys; print(sys.executable)')
if sudo -n -u autose-sandbox env -i PATH=/usr/bin:/bin "$PY" -c "import tinydb, pytest" 2>/dev/null \
   && ! sudo -n -u autose-sandbox env -i cat "/proc/$$/environ" >/dev/null 2>&1; then
  echo "AUTOSE_SANDBOX_USER=autose-sandbox" >> "$GITHUB_ENV"
  echo "sandbox user ready (cannot read runner environ)"
else
  echo "::warning::sandbox user unusable; falling back to environment scrubbing only"
fi
