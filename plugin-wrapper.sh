#!/bin/bash
set -euo pipefail

PLUGIN_DIR="/home/tom/claude/legal-research-plugin"
EMAIL_QUERIES_DIR="${PLUGIN_DIR}/email-queries"
OUTPUT_FILE="/tmp/gmail-monitor/result-${REQUEST_ID}.html"
SEND_RESULT="/home/tom/claude/gmail-monitor/send_result.py"
VENV="/home/tom/claude/gmail-monitor/.venv/bin/python"
LOG_FILE="${EMAIL_QUERIES_DIR}/research-${REQUEST_ID}.log"
STDERR_LOG="${EMAIL_QUERIES_DIR}/research-${REQUEST_ID}-stderr.log"

mkdir -p "${EMAIL_QUERIES_DIR}"
mkdir -p /tmp/gmail-monitor
cd "${EMAIL_QUERIES_DIR}"

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Starting legal research for request ${REQUEST_ID}" >> "${LOG_FILE}"

set +e
claude --dangerously-skip-permissions --plugin-dir "${PLUGIN_DIR}" --print "/legal-research:research-email" \
  2>"${STDERR_LOG}" | tee -a "${LOG_FILE}"
CLAUDE_EXIT=${PIPESTATUS[0]}
set -e

# Append stderr with separator so it's visible in main log
if [ -s "${STDERR_LOG}" ]; then
    echo "=== STDERR ===" >> "${LOG_FILE}"
    cat "${STDERR_LOG}" >> "${LOG_FILE}"
    echo "=== END STDERR ===" >> "${LOG_FILE}"
fi

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Claude process exited with code: ${CLAUDE_EXIT}" >> "${LOG_FILE}"

if [ "${CLAUDE_EXIT}" -ne 0 ]; then
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] ERROR: claude exited non-zero, writing error HTML" >> "${LOG_FILE}"
    cat > "${OUTPUT_FILE}" <<'EOF'
<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Research Error</title></head>
<body><h1>Legal Research: Processing Error</h1>
<p>The research system encountered an unexpected error. Please try again.</p>
</body></html>
EOF
fi

if [ ! -f "${OUTPUT_FILE}" ]; then
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] ERROR: no output file produced, writing fallback HTML" >> "${LOG_FILE}"
    cat > "${OUTPUT_FILE}" <<'EOF'
<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Research Error</title></head>
<body><h1>Legal Research: No Output Produced</h1>
<p>No result was produced. Please try again with a clear legal research question.</p>
</body></html>
EOF
fi

"${VENV}" "${SEND_RESULT}" \
    --thread-id  "${GMAIL_THREAD_ID}" \
    --to         "${EMAIL_FROM}" \
    --subject    "${EMAIL_SUBJECT}" \
    --file       "${OUTPUT_FILE}" \
    --request-id "${REQUEST_ID}" \
    --body       "Your legal research report is attached. Open in a browser for best results."

rm -f "${OUTPUT_FILE}"
echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Legal research result sent for request ${REQUEST_ID}." >> "${LOG_FILE}"
exit 0
