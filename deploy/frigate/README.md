# Simple Frigate on pb62

This is the smallest Frigate setup for `pb62`.

- Starts Frigate with a disabled dummy camera so the UI can boot cleanly
- Keeps config and media in `~/frigate`
- Loads optional env vars from `~/frigate/config/.env`
- Exposes the web UI on port `8971`
- Prepares Intel iGPU access for later camera decoding and OpenVINO detection

This directory is the repo-safe starting point. The live `pb62` camera config may
be rendered from local secrets and should not be committed back into the repo.

## First start

Run on `pb62`:

```bash
mkdir -p ~/frigate/config ~/frigate/storage
cp deploy/frigate/config.yml ~/frigate/config/config.yml
cp deploy/frigate/.env.example ~/frigate/config/.env
podman run -d \
  --name frigate \
  --restart unless-stopped \
  --shm-size=512m \
  --device /dev/dri/renderD128:/dev/dri/renderD128 \
  --device /dev/dri/card0:/dev/dri/card0 \
  -p 8971:8971 \
  -p 8554:8554 \
  -v ~/frigate/config:/config \
  -v ~/frigate/storage:/media/frigate \
  --tmpfs /tmp/cache:rw,size=1g \
  ghcr.io/blakeblackshear/frigate:stable
```

Then check logs:

```bash
podman logs --tail 100 frigate
```

On first startup, finish the Frigate login flow in the web UI.

## Next step

Replace `dummy_camera` with a real camera entry and add your RTSP URL using
Frigate's UI editor or by updating `~/frigate/config/config.yml`.

If you want to keep credentials out of the YAML, put them in
`~/frigate/config/.env` and reference them from your camera config.
