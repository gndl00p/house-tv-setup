#!/bin/bash
# One-off: convert rendered jukebox .mkv files to .mp4 (stream copy). See render.py for why.
OUT="${JUKEBOX_OUT:-/mnt/media/Plex/Jukebox}"
find "$OUT" -name '*.mkv' -print0 | while IFS= read -r -d '' f; do
  out="${f%.mkv}.mp4"
  if ffmpeg -nostdin -v error -y -i "$f" -c copy -movflags +faststart -f mp4 "$out.part" && mv "$out.part" "$out"; then
    rm -f "$f"
  else
    rm -f "$out.part"; echo "FAIL $f"
  fi
done
echo "remux done: $(find "$OUT" -name '*.mp4' | wc -l) mp4, $(find "$OUT" -name '*.mkv' | wc -l) mkv left"
