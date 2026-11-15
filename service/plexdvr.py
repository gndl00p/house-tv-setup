#!/usr/bin/env python3
"""Point the Plex DVR at the house-tv guide and map every channel. Runs on the Plex host as root.

usage: plexdvr.py status | add-weather | switch-guide | map | reload | analyze
Order for first setup: add-weather, switch-guide, map, reload. `map` is safe to rerun after
adding Tunarr channels. It always PUTs the FULL mapping per device (a partial PUT disables the rest).
"""
import json, os, re, sys, urllib.parse, urllib.request

PLEX = "http://127.0.0.1:32400"
PREFS = "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml"
GUIDE = "http://127.0.0.1:8001/xmltv.xml"
WEATHER_URI = "http://127.0.0.1:8001"
TOKEN = re.search(r'PlexOnlineToken="([^"]+)"', open(PREFS).read()).group(1)


def dvr_key():
    """Plex DVR id. Set PLEX_DVR_ID when the server has more than one."""
    forced = os.environ.get("PLEX_DVR_ID")
    if forced:
        return forced
    _, mc = plex("GET", "/livetv/dvrs")
    dvrs = mc.get("Dvr") or [] if isinstance(mc, dict) else []
    if not dvrs:
        raise SystemExit("No Plex DVR found. Create one in Plex, or set PLEX_DVR_ID.")
    return str(dvrs[0].get("key") or dvrs[0].get("id"))


def plex(method, path, params=None):
    q = ("?" + urllib.parse.urlencode(params, doseq=True)) if params else ""
    req = urllib.request.Request(PLEX + path + q, method=method,
                                 headers={"X-Plex-Token": TOKEN, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read()
        return r.status, (json.loads(body)["MediaContainer"] if body.strip().startswith(b"{") else body[:200])


def lineup_id(url):
    return "lineup://tv.plex.providers.epg.xmltv/" + urllib.parse.quote(url, safe="") + "#House"


def dvr():
    return plex("GET", f"/livetv/dvrs/{dvr_key()}")[1]["Dvr"][0]


def status():
    d = dvr()
    print("lineup:", d.get("lineup"))
    for dev in d.get("Device", []):
        maps = dev.get("ChannelMapping", [])
        print(f"device key={dev['key']} uri={dev['uri']} status={dev.get('status')} "
              f"mapped={sum(1 for m in maps if m.get('enabled') in ('1', 1, True))}/{len(maps)}")
        for m in maps:
            print("   ", m.get("deviceIdentifier"), "->", m.get("channelKey"), "enabled" if m.get("enabled") in ("1", 1, True) else "off")


def add_weather():
    if any(dv["uri"] == WEATHER_URI for dv in dvr().get("Device", [])):
        print("weather device already on the DVR"); return
    print("register:", plex("POST", "/media/grabbers/devices", {"uri": WEATHER_URI})[0])
    devs = plex("GET", "/media/grabbers/devices")[1].get("Device", [])
    key = next(dv["key"] for dv in devs if dv["uri"] == WEATHER_URI)
    print("add to dvr:", plex("PUT", f"/livetv/dvrs/{dvr_key()}/devices/{key}")[0], "device key", key)


def switch_guide():
    new, old = lineup_id(GUIDE), dvr()["lineup"]
    if old == new:
        print("guide already switched"); return
    print("add lineup:", plex("PUT", f"/livetv/dvrs/{dvr_key()}/lineups", {"lineup": new})[0])
    print("remove old:", plex("DELETE", f"/livetv/dvrs/{dvr_key()}/lineups", {"lineup": old})[0])


def tuner_channels(key):
    """Channel numbers the tuner device reports (asks Plex to re-read the tuner's lineup)."""
    _, mc = plex("GET", f"/media/grabbers/devices/{key}/channels")
    return [c["identifier"] for c in mc.get("DeviceChannel", [])] if isinstance(mc, dict) else []


def map_channels():
    for dev in dvr().get("Device", []):
        nums = tuner_channels(dev["key"])
        params = {"channelsEnabled": ",".join(nums)}
        for n in nums:
            params[f"channelMapping[{n}]"] = n
            params[f"channelMappingByKey[{n}]"] = f"house{n}"
        print(f"device {dev['key']} map {nums}:", plex("PUT", f"/media/grabbers/devices/{dev['key']}/channelmap", params)[0])


def analyze():
    """Ask Plex to analyze the music video + jukebox libraries so new files get durations (Tunarr needs them)."""
    _, mc = plex("GET", "/library/sections")
    for d in mc["Directory"]:
        if d["title"] in ("Jukebox", "Music Videos Concerts"):
            plex("GET", f"/library/sections/{d['key']}/refresh")
            print(d["title"], "analyze:", plex("PUT", f"/library/sections/{d['key']}/analyze")[0])


def reload():
    print("reloadGuide:", plex("POST", f"/livetv/dvrs/{dvr_key()}/reloadGuide")[0])


if __name__ == "__main__":
    {"status": status, "add-weather": add_weather, "switch-guide": switch_guide,
     "map": map_channels, "reload": reload, "analyze": analyze}[sys.argv[1]]()
