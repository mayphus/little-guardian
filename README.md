# little-guardian

This repository is now primarily a Frigate deployment project for `pb62`.

## Current Focus

The active deployment lives in [deploy/frigate](/Users/mayphus/making/little-guardian/deploy/frigate).

That directory contains:

- a minimal Frigate config template
- a simple Podman launcher for `pb62`
- setup notes for the homelab deployment

## Repo Layout

- [deploy/frigate](/Users/mayphus/making/little-guardian/deploy/frigate): active Frigate deployment files
- [legacy/node-app](/Users/mayphus/making/little-guardian/legacy/node-app): archived custom Node frontend prototype

## Why

Frigate already covers the primary product needs better than the original custom app:

- live camera access
- recording
- detection
- auth
- review UI

The old Node app is kept only as an archive in case we want custom family-specific features later.

## Next Steps

- make the `pb62` deployment reproducible from the repo
- add zones and basic event tuning for `baby_room`
- revisit hardware acceleration after the base setup is stable
