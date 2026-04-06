#!/bin/bash
set -e

if [[ -f "/workspaces/frappe_codespace/frappe-bench/apps/frappe" ]]
then
    echo "Bench already exists, skipping init"
    exit 0
fi

rm -rf /workspaces/frappe_codespace/.git

# 2. Setup Node 24
source /home/frappe/.nvm/nvm.sh
nvm install 24
nvm alias default 24
nvm use 24

# Node 24 → does NOT include yarn
npm install -g yarn@1.22

# 3. Setup Python 3.14
curl -LsSf https://astral.sh/uv/install.sh | sh
# Ensure uv is in PATH
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.14
uv python pin 3.14

# 4. Initialize Bench
# We use 'uv python which' to ensure we grab the version we just installed
PYTHON_BIN=$(uv python find 3.14)

cd /workspace
chown frappe:frappe /workspace/frappe-bench

bench init \
  --ignore-exist \
  --skip-redis-config-generation \
  --frappe-branch version-16 \
  --python "$PYTHON_BIN" \
  frappe-bench

cd frappe-bench

# Use containers instead of localhost
bench set-mariadb-host mariadb
bench set-redis-cache-host redis://redis-cache:6379
bench set-redis-queue-host redis://redis-queue:6379
bench set-redis-socketio-host redis://redis-socketio:6379

# Remove redis from Procfile
sed -i '/redis/d' ./Procfile

bench new-site dev.localhost \
  --db-host mariadb \
  --mariadb-root-username root \
  --mariadb-root-password 123 \
  --admin-password admin \
  --mariadb-user-host-login-scope='%'

bench --site dev.localhost set-config developer_mode 1
bench --site dev.localhost clear-cache
bench use dev.localhost
bench get-app --branch version-16 --resolve-deps erpnext
bench --site dev.localhost install-app erpnext
bench get-app --branch version-3 --resolve-deps insights
bench --site dev.localhost install-app insights
bench get-app --branch v2.8.6 --resolve-deps https://github.com/The-Commit-Company/raven.git
bench --site dev.localhost install-app raven