#!/bin/bash
# Build and start the weather channel. Idempotent.
# Run as root from /opt/house-tv/weather, after house.env exists.
set -euo pipefail
cd /opt/house-tv/weather
if [ -f /opt/house-tv/house.env ]; then
  set -a
  # shellcheck disable=SC1091
  . /opt/house-tv/house.env
  set +a
fi
if [ ! -d ws4kp ]; then
  git clone -q --depth 1 --branch v7.1.6 https://github.com/netbymatt/ws4kp ws4kp
fi
# Stock ws4kp travel cities are national. This file replaces them. Edit it.
if [ -f travelcities.json ]; then
  cp travelcities.json ws4kp/datagenerators/output/travelcities.json
fi
mkdir -p music/regular music/holiday
# Drop mp3s in those two folders. December plays holiday/, the rest of the year plays regular/.
[ -e music/active ] || ln -sfn regular music/active
docker compose build ws4kp
docker compose pull ws4channels
docker compose up -d
