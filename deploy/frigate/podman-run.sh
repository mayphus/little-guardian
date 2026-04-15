#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${HOME}/frigate"
CONFIG_DIR="${BASE_DIR}/config"
STORAGE_DIR="${BASE_DIR}/storage"

mkdir -p "${CONFIG_DIR}" "${STORAGE_DIR}"

if [ ! -f "${CONFIG_DIR}/config.yml" ]; then
  echo "Missing ${CONFIG_DIR}/config.yml"
  echo "Copy deploy/frigate/config.yml there first."
  exit 1
fi

ENV_ARGS=()
if [ -f "${CONFIG_DIR}/.env" ]; then
  ENV_ARGS+=(--env-file "${CONFIG_DIR}/.env")
fi

if podman container exists frigate; then
  podman rm -f frigate >/dev/null
fi

exec podman run -d \
  --name frigate \
  --restart unless-stopped \
  --shm-size=512m \
  --device /dev/dri/renderD128:/dev/dri/renderD128 \
  --device /dev/dri/card0:/dev/dri/card0 \
  -p 8971:8971 \
  -p 8554:8554 \
  -v "${CONFIG_DIR}:/config" \
  -v "${STORAGE_DIR}:/media/frigate" \
  "${ENV_ARGS[@]}" \
  --tmpfs /tmp/cache:rw,size=1g \
  ghcr.io/blakeblackshear/frigate:stable
