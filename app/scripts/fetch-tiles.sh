#!/usr/bin/env bash
# Downloads the pmtiles CLI, extracts Ukraine from the Protomaps daily OSM build,
# and fetches fonts + sprites so the map runs fully offline.
set -euo pipefail
cd "$(dirname "$0")/.."

PMTILES_VERSION="1.31.2"
BBOX="22.0,44.0,40.3,52.5"   # west,south,east,north (Ukraine + margin)
MAXZOOM="${MAXZOOM:-15}"
BUILD_DATE="${BUILD_DATE:-$(date -u -d yesterday +%Y%m%d)}"

mkdir -p bin public/data public/assets

if [ ! -x bin/pmtiles ]; then
  echo ">> Downloading pmtiles CLI v$PMTILES_VERSION"
  curl -fsSL "https://github.com/protomaps/go-pmtiles/releases/download/v${PMTILES_VERSION}/go-pmtiles_${PMTILES_VERSION}_Linux_x86_64.tar.gz" \
    | tar -xz -C bin pmtiles
fi

echo ">> Extracting Ukraine (build $BUILD_DATE, z0-$MAXZOOM)"
bin/pmtiles extract "https://build.protomaps.com/${BUILD_DATE}.pmtiles" public/data/ukraine.pmtiles \
  --bbox="$BBOX" --maxzoom="$MAXZOOM" --download-threads=8

if [ ! -d public/assets/fonts ]; then
  echo ">> Downloading fonts + sprites"
  tmp=$(mktemp -d)
  curl -fsSL https://github.com/protomaps/basemaps-assets/archive/refs/heads/main.tar.gz | tar -xz -C "$tmp"
  cp -r "$tmp"/basemaps-assets-main/fonts "$tmp"/basemaps-assets-main/sprites public/assets/
  rm -rf "$tmp"
fi

bin/pmtiles show public/data/ukraine.pmtiles | head -20
echo ">> Done. Run: npm run dev"
