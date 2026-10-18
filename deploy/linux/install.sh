#!/usr/bin/env bash
# Install the LRB decision service on Ubuntu/Debian (separate-host option).
# Usage: sudo ./install.sh /path/to/TRading EA_VPS_IP
# The HMAC secret must be the SAME as on the Windows host (<Common>\Files\LRB\hmac_secret.txt).
# Copy it over a secure channel (e.g. scp) to /etc/lrb/hmac_secret - never via chat or git.
set -euo pipefail
REPO=${1:?repo path}
EA_IP=${2:?Windows VPS public IP}
id lrb >/dev/null 2>&1 || useradd --system --home /var/lib/lrb --shell /usr/sbin/nologin lrb
install -d -o lrb -g lrb -m 0700 /var/lib/lrb /var/lib/lrb/backups
install -d -m 0750 -g lrb /etc/lrb
rsync -a --delete --exclude .venv --exclude data --exclude reports "$REPO"/ /opt/lrb/
command -v uv >/dev/null || { echo "install uv first: https://docs.astral.sh/uv/"; exit 1; }
cd /opt/lrb/python && uv sync --frozen --no-dev
chown -R root:lrb /opt/lrb && chmod -R g+rX,o-rwx /opt/lrb
if [ ! -f /etc/lrb/hmac_secret ]; then
  echo "Place the shared secret in /etc/lrb/hmac_secret (chmod 0640 root:lrb) and re-run." >&2; exit 2
fi
chown root:lrb /etc/lrb/hmac_secret && chmod 0640 /etc/lrb/hmac_secret
cat > /etc/lrb/lrb.env <<ENV
LRB_HMAC_SECRET_FILE=/etc/lrb/hmac_secret
LRB_KEY_ID=ea1
LRB_DB_PATH=/var/lib/lrb/lrb_service.db
LRB_BIND_HOST=127.0.0.1
LRB_PORT=8787
LRB_ALLOWED_SYMBOLS=EURUSD
LRB_LOG_LEVEL=INFO
ENV
chmod 0640 /etc/lrb/lrb.env && chown root:lrb /etc/lrb/lrb.env
install -m 0644 "$REPO"/deploy/linux/lrb.service "$REPO"/deploy/linux/lrb-backup.service "$REPO"/deploy/linux/lrb-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lrb.service lrb-backup.timer
# time sync
timedatectl set-ntp true || true
# firewall: only SSH and HTTPS from the EA host
if command -v ufw >/dev/null; then
  ufw allow OpenSSH
  ufw allow from "$EA_IP" to any port 443 proto tcp
  ufw deny 8787/tcp
  ufw --force enable
fi
echo "Service on 127.0.0.1:8787. Put Caddy (deploy/linux/Caddyfile.example) in front for TLS on 443."
echo "Bridge input InpServiceUrl=https://<your host>; add that URL to MT5 WebRequest allow-list."
curl -fsS http://127.0.0.1:8787/health && echo
