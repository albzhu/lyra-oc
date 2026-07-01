# Moved. The canonical gateway image now lives at docker/Dockerfile, built via
# docker-compose.yml (service: gateway) with the host-path-identical bind-mount
# design required because openclaw.json hardcodes absolute paths.
#
# See docker/README-docker.md.  Build with:
#   make -f docker/docker.mk d-build
#
# This file is intentionally not buildable (no FROM) so a stray `docker build .`
# fails loudly instead of producing a stale, mis-pathed image.
