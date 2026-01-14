#!/usr/bin/with-contenv bashio
set -euo pipefail

LOGIN="$(bashio::config 'login' || true)"
PASSWORD="$(bashio::config 'password' || true)"

if [ "${LOGIN}" = "null" ]; then
  LOGIN=""
fi

if [ "${PASSWORD}" = "null" ]; then
  PASSWORD=""
fi

export LOGIN
export PASSWORD

exec python /app/main.py
