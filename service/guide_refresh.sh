#!/bin/bash
# House TV guide refresh: Tunarr XMLTV -> house-tv proxy (cache bypass) -> Plex reload, only if it changed.
set -u
curl -s -m60 -X POST -H "Content-Type: application/json" -d "{}" http://127.0.0.1:8000/api/xmltv/refresh >/dev/null
sleep 10
new=$(curl -s -m60 "http://127.0.0.1:8001/xmltv.xml?fresh=1" | sed 's/ date="[^"]*"//' | sha1sum | cut -d" " -f1)   # ignore the per-build date stamp
old=$(cat /var/lib/house-tv-guide.sha1 2>/dev/null || true)
if [ -n "$new" ] && [ "$new" != "$old" ]; then
  /usr/bin/python3 /opt/house-tv/service/plexdvr.py reload && echo "$new" > /var/lib/house-tv-guide.sha1
else
  echo "guide unchanged, no Plex reload"
fi
