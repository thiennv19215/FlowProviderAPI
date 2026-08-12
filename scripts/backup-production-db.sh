#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env.production}"
COMPOSE_FILE="${COMPOSE_FILE:-${ROOT_DIR}/compose.production.yaml}"
BACKUP_DIR="${BACKUP_DIR:-${ROOT_DIR}/backups}"

cd "${ROOT_DIR}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "missing ${ENV_FILE}" >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"
output="${BACKUP_DIR}/flowprovider-$(date -u +%Y%m%dT%H%M%SZ).dump"
compose=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}")

"${compose[@]}" exec -T postgres \
  pg_dump -U flowprovider -d flowprovider --format=custom --no-owner --no-privileges \
  > "${output}"

if [[ ! -s "${output}" ]]; then
  rm -f "${output}"
  echo "database backup is empty; backup failed" >&2
  exit 1
fi

chmod 600 "${output}"
echo "database backup written to ${output}"
