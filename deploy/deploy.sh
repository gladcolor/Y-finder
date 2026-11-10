#!/usr/bin/env bash
# Deploy Y-finder to the Research Architect server (Contabo VPS, see SERVER_MAINTENANCE.md of Research Architect).
# Usage (git-bash on the workstation, from the repo root):  bash deploy/deploy.sh
# First run also needs:  bash deploy/deploy.sh --setup   (user, venv, systemd, nginx, key)
set -euo pipefail
# The server address is not in the repository: set YFINDER_HOST=user@host, or put that one line in deploy/host.txt
# (git-ignored).
HOST=${YFINDER_HOST:-$(cat "$(dirname "$0")/host.txt" 2>/dev/null || true)}
[[ -n "$HOST" ]] || { echo "Set YFINDER_HOST=user@host, or write it to deploy/host.txt"; exit 1; }
APP=/opt/yfinder
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "== sync code"
tar -C "$ROOT" --exclude='.git' --exclude='runtime' --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
    --exclude='OpenAI_key.txt' --exclude='*key*.txt' --exclude='eval_runs' --exclude='static/brand' --exclude='host.txt' -czf - . | \
  ssh "$HOST" "mkdir -p $APP.new && tar -xzf - -C $APP.new && \
    if [ -d $APP ]; then rm -rf $APP.prev; cp -a $APP $APP.prev; fi; \
    mkdir -p $APP && cp -a $APP.new/. $APP/ && rm -rf $APP.new"

if [[ "${1:-}" == "--setup" ]]; then
  echo "== one-time setup"
  ssh "$HOST" "id yfinder >/dev/null 2>&1 || useradd --system --home /var/lib/yfinder --shell /usr/sbin/nologin yfinder; \
    mkdir -p /var/lib/yfinder && chown -R yfinder:yfinder /var/lib/yfinder; \
    python3 -m venv $APP/venv && $APP/venv/bin/pip install -q --upgrade pip && $APP/venv/bin/pip install -q -r $APP/requirements.txt gunicorn"
  # The key goes to a root-only env file, read by systemd. It is piped, never echoed.
  python - "$ROOT/OpenAI_key.txt" <<'PY' | ssh "$HOST" "umask 077; cat > /etc/yfinder.env; chmod 600 /etc/yfinder.env"
import re, sys
key = re.search(r"sk-[A-Za-z0-9_\-]{20,}", open(sys.argv[1], encoding="utf-8").read()).group(0)
print("OPENAI_API_KEY=" + key)
print("YFINDER_HANDBOOK_ONDEMAND=1")
import os
ds = os.path.join(os.path.dirname(sys.argv[1]), "Deepseek_key.txt")
if os.path.exists(ds):  # web search through DeepSeek Search/Fetch (yfinder/websearch.py)
    m = re.search(r"sk-[A-Za-z0-9_\-]{16,}", open(ds, encoding="utf-8").read())
    if m:
        print("DEEPSEEK_API_KEY=" + m.group(0))
PY
  scp "$ROOT/deploy/yfinder.service" "$ROOT/deploy/yfinder-watch.service" "$ROOT/deploy/yfinder-watch.timer" "$HOST:/etc/systemd/system/"
  scp "$ROOT/deploy/nginx-yfinder.conf" "$HOST:/etc/nginx/snippets/yfinder.conf"
  # Add one include line to each server block of the Research Architect vhost. Restore the backup if nginx -t fails.
  ssh "$HOST" 'set -e; V=/etc/nginx/sites-available/causalai; B=/root/causalai.nginx.bak-$(date +%F-%H%M%S); cp -a $V $B;
    grep -q "snippets/yfinder.conf" $V || sed -i "s#^\(\s*\)location / {#\1include /etc/nginx/snippets/yfinder.conf;\n\n\1location / {#" $V;
    if ! nginx -t; then cp -a $B $V; echo "nginx test failed; restored $B"; exit 1; fi;
    systemctl reload nginx; systemctl daemon-reload; systemctl enable --now yfinder yfinder-watch.timer'
else
  ssh "$HOST" "$APP/venv/bin/pip install -q -r $APP/requirements.txt"
fi

# Y-finder's own browser (yfinder/browser.py): headless Chromium outside $APP, so deploys do not copy it.
# Installing is a no-op when this Playwright version's build is already there. The unit file carries the path.
ssh "$HOST" "PLAYWRIGHT_BROWSERS_PATH=/opt/yfinder-browsers $APP/venv/bin/python -m playwright install --with-deps chromium-headless-shell >/dev/null &&   chmod -R a+rX /opt/yfinder-browsers &&   if ! cmp -s $APP/deploy/yfinder.service /etc/systemd/system/yfinder.service; then cp $APP/deploy/yfinder.service /etc/systemd/system/ && systemctl daemon-reload; fi"

ssh "$HOST" "chown -R root:yfinder $APP && chmod -R g+rX $APP && chown -R yfinder:yfinder $APP/handbooks && systemctl restart yfinder && sleep 3 && \
  curl -s -o /dev/null -w 'local %{http_code}\n' http://127.0.0.1:8060/healthz"
curl -s -o /dev/null -w "public %{http_code}\n" https://app.researcharchitect.ai/y-finder/healthz
