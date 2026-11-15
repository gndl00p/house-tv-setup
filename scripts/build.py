#!/usr/bin/env python3
"""Build House TV channel schedules in Tunarr from channels.json rules.

usage: build.py [--dry-run] [--only N[,N...]] [--config channels.json] [--tunarr URL]

For each channel: resolve its shows and movies against Tunarr's scanned Plex
catalogue, compute slot weights so airtime share follows sqrt(total runtime)
capped at `cap`, then POST a server-side random-slot schedule so Tunarr
generates the lineup. Stdlib only so it runs on the Plex host unchanged.
"""
import os
import argparse, datetime, json, math, re, sys, time, urllib.request, uuid
from zoneinfo import ZoneInfo

MOVIES_LIB = "Movies"
SHOWS_LIB = "TV Shows"
HALF_HOUR = 1800000


def api(base, path, body=None, method=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def catalogue(base):
    """Return (shows, movies): shows[title] = {id, rating, genres, eps:[(uuid, dur)]}, movies[title] = [(uuid, dur, rating, genres, plex ratingKey)]."""
    ms = api(base, "/api/media-sources")[0]["id"]
    libs = {l["name"]: l["id"] for l in api(base, f"/api/media-sources/{ms}/libraries")}
    shows, movies = {}, {}
    for item in api(base, f"/api/media-libraries/{libs[SHOWS_LIB]}/programs"):
        p = item["program"]; s = p["show"]
        if p.get("state") == "missing":      # file moved or deleted in Plex; Tunarr can't play it
            continue
        sh = shows.setdefault(s["title"], {"id": s["uuid"], "rating": s.get("rating"),
                                           "genres": [g["name"] for g in s.get("genres", [])], "eps": []})
        sh["eps"].append((p["uuid"], item["duration"], p.get("title") or "",
                          ((p.get("season") or {}).get("index") or 0, p.get("episodeNumber") or 0)))
    for title in list(shows):
        # season 0 is featurettes/bonus clips (1-minute Courage bits, interviews); not TV
        shows[title]["eps"] = [e for e in shows[title]["eps"] if e[3][0] != 0 and e[1] >= 180_000]
        if not shows[title]["eps"]:
            del shows[title]
    for sh in shows.values():
        sh["eps"].sort(key=lambda e: e[3])                    # season/episode order
    for item in api(base, f"/api/media-libraries/{libs[MOVIES_LIB]}/programs"):
        p = item["program"]
        if p.get("state") == "missing":
            continue
        movies.setdefault(p["title"], []).append((p["uuid"], item["duration"], p.get("rating"),
                                                  [g["name"] for g in p.get("genres", [])], p.get("externalId")))
    return shows, movies


def load_config(path):
    """channels.json plus nightly auto-assignments (assignments.json next to it, if present)."""
    cfg = json.load(open(path))
    try:
        extra = json.load(open(path.rsplit("/", 1)[0] + "/assignments.json" if "/" in path else "assignments.json"))
    except FileNotFoundError:
        return cfg
    by = {ch["number"]: ch for ch in cfg["channels"]}
    for title, num in extra.get("shows", {}).items():
        ch = by.get(num)
        if not ch or title in ch["shows"]:
            continue
        ch["shows"].append(title)
        for part in ch.get("dayparts", []):
            if not part.get("locked") and len(part.get("shows", [])) >= 3 and title not in part["shows"]:
                part["shows"].append(title)
    for num, titles in extra.get("labeled", {}).items():     # movies tagged with a channel's plexLabel in Plex
        lst = cfg["movies"].setdefault(num, [])
        lst += [t for t in titles if t not in lst]
    for title, nums in extra.get("movies", {}).items():
        for num in nums:
            lst = cfg["movies"].setdefault(str(num), [])
            if title not in lst:
                lst.append(title)
    return cfg


def season(cfg, month=None):
    """The active seasonal config (by calendar month in Central time) or None."""
    month = month or datetime.datetime.now(ZoneInfo(os.environ.get("HOUSE_TZ", "America/Chicago"))).month
    return cfg.get("seasonal", {}).get(str(month))


def seasonal_items(ch, cfg, shows, movies):
    """[(uuid, dur)] holiday episodes from this channel's own shows plus matching movies on its list."""
    sz = season(cfg)
    if not sz or ch["number"] in sz.get("skip", []):
        return sz, []
    rx = re.compile(sz["episodes"], re.I)
    items = [(e[0], e[1]) for t in ch["shows"] if t in shows for e in shows[t]["eps"] if rx.search(e[2])]
    genres = set(sz.get("movieGenres", []))
    for t in cfg["movies"].get(str(ch["number"]), []):
        for m in movies.get(t, [])[:1]:
            if rx.search(t) or genres & set(m[3]):
                items.append((m[0], m[1]))
    return sz, items


def ensure_custom_show(base, name, items):
    shows = {c["name"]: c["id"] for c in api(base, "/api/custom-shows")}
    body = {"name": name, "programs": [{"type": "content", "id": u, "duration": d} for u, d in items],
            "syncMediaSourceId": None, "syncMediaSourceType": None, "syncExternalPlaylistId": None,
            "enableSync": False}
    if name in shows:
        api(base, f"/api/custom-shows/{shows[name]}", body, method="PUT")
        return shows[name]
    api(base, "/api/custom-shows", body)
    return {c["name"]: c["id"] for c in api(base, "/api/custom-shows")}[name]


def padded(ms, n=1):
    """Airtime one pick takes once padded up to the next half hour."""
    return math.ceil(ms * n / HALF_HOUR) * HALF_HOUR


def cap_shares(raw, caps):
    """Normalize raw scores to shares, redistributing anything above each key's cap.
    If the caps can't fill 100% (small library), scale the capped shares up to fill it."""
    if sum(caps.values()) <= 1:
        return {k: c / sum(caps.values()) for k, c in caps.items()}
    shares = {k: v / sum(raw.values()) for k, v in raw.items()}
    for _ in range(50):
        over = {k for k, v in shares.items() if v > caps[k] + 1e-9}
        if not over:
            break
        free = sum(shares[k] - caps[k] for k in over)
        rest = sum(v for k, v in shares.items() if shares[k] < caps[k] - 1e-9 and k not in over)
        for k in shares:
            if k in over:
                shares[k] = caps[k]
            elif shares[k] < caps[k] - 1e-9 and rest:
                shares[k] += free * shares[k] / rest
    return shares


def plan_channel(ch, cfg, shows, movies, base=None):
    d = cfg["defaults"]
    cap, cooldown = ch.get("cap", d["cap"]), ch.get("cooldownMs", d["cooldownMs"])
    movie_titles = cfg["movies"].get(str(ch["number"]), [])
    mshare = ch.get("movieShare", 0) if movie_titles else 0
    missing = [t for t in ch["shows"] if t not in shows] + [t for t in movie_titles if t not in movies]
    def pcount(t):
        """Episodes per pick: explicit config wins; shorts are bundled to fill most of a half hour."""
        if t in ch.get("programCount", {}):
            return ch["programCount"][t]
        eps = shows[t]["eps"]
        avg = sum(e[1] for e in eps) / len(eps)
        auto = max(1, int(26 * 60000 // avg)) if avg < 15 * 60000 else 1
        return max(auto, ch.get("programCountAll", 1))
    runtime = {t: sum(e[1] for e in shows[t]["eps"]) for t in ch["shows"] if t in shows}
    # airtime the show side fills over the generated window; a show may fill at most `maxRepeats` passes of itself
    window = ch.get("maxDays", d["maxDays"]) * 864e5 * (1 - mshare)
    reps = ch.get("maxRepeats", d["maxRepeats"])
    caps = {t: min(cap, r * reps / window) for t, r in runtime.items()}
    raw = {t: math.sqrt(r) for t, r in runtime.items()}
    shares = {k: v * (1 - mshare) for k, v in cap_shares(raw, caps).items()} if raw else {}
    slots, programs, report = [], [], {}
    for t, share in shares.items():
        eps = shows[t]["eps"]
        pick = padded(sum(e[1] for e in eps) / len(eps), pcount(t))
        slots.append({"id": str(uuid.uuid4()), "type": "show", "showId": shows[t]["id"], "order": "next",
                      "direction": "asc", "weight": share / pick * 1e7, "cooldownMs": cooldown,
                      "durationSpec": {"type": "dynamic", "programCount": pcount(t)},
                      "linkMode": "continue", "rerunOverflow": "flex", "seasonFilter": [], "seasonExcludeFilter": []})
        programs += [e[0] for e in eps]
        report[t] = share
    if mshare:
        ms = [m for t in movie_titles if t in movies for m in movies[t][:1]]
        pick = padded(sum(m[1] for m in ms) / len(ms))
        slots.append({"id": str(uuid.uuid4()), "type": "movie", "order": "shuffle", "direction": "asc",
                      "weight": mshare / pick * 1e7, "cooldownMs": 0 if mshare == 1 else cooldown,
                      "durationSpec": {"type": "dynamic", "programCount": 1},
                      "linkMode": "continue", "rerunOverflow": "flex"})
        programs += [m[0] for m in ms]
        report["[movies]"] = mshare
    sz, items = seasonal_items(ch, cfg, shows, movies)
    if sz and len(items) >= 3:
        share = sz.get("shareByChannel", {}).get(str(ch["number"]), sz.get("share", 0.12))
        # never more than ~8 airings of each holiday item across the generated window
        share = min(share, sum(d for _, d in items) * 8 / (ch.get("maxDays", d["maxDays"]) * 864e5))
        cs_id = ensure_custom_show(base, f"{sz['name']} - {ch['name']}", items) if base else "dry-run"
        pick = padded(sum(d for _, d in items) / len(items))
        slots.append({"id": str(uuid.uuid4()), "type": "custom-show", "customShowId": cs_id, "order": "shuffle",
                      "direction": "asc", "weight": share / pick * 1e7, "cooldownMs": cooldown,
                      "durationSpec": {"type": "dynamic", "programCount": 1},
                      "linkMode": "continue", "rerunOverflow": "flex"})
        programs += [u for u, _ in items]
        report[f"[{sz['name']}: {len(items)} items]"] = share
    # Tunarr treats weights as coarse integers; scale so the heaviest slot is 100.
    top = max((sl["weight"] for sl in slots), default=1)
    for sl in slots:
        sl["weight"] = max(1, round(sl["weight"] / top * 100))
    schedule = {"type": "random", "flexPreference": "end", "maxDays": ch.get("maxDays", d["maxDays"]),
                "padMs": ch.get("padMs", d["padMs"]), "padStyle": "slot",
                "randomDistribution": "weighted", "lockWeights": False, "slots": slots,
                # fixed offset: Tunarr stalls generating across the DST fall-back hour
                "timeZoneOffset": ch.get("timeZoneOffset", d.get("timeZoneOffset", 300))}
    return {"type": "random", "programs": programs, "schedule": schedule}, report, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="channels.json")
    ap.add_argument("--tunarr", default=os.environ.get("TUNARR_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--only")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--settings-only", action="store_true", help="update channel fields, keep lineup")
    a = ap.parse_args()
    cfg = load_config(a.config)
    only = {int(x) for x in a.only.split(",")} if a.only else None
    shows, movies = catalogue(a.tunarr)
    existing = {c["number"]: c for c in api(a.tunarr, "/api/channels")}
    for ch in cfg["channels"]:
        if only and ch["number"] not in only:
            continue
        if ch.get("dayparts"):
            continue      # time-slot channel: timeslots.py owns its lineup
        if ch.get("kind") in ("external", "holiday"):
            continue      # lineup owned by another script (jukebox.py, schedule.py) or not a Tunarr channel
        body, report, missing = plan_channel(ch, cfg, shows, movies, None if a.dry_run else a.tunarr)
        print(f"\n== {ch['number']} {ch['name']}: {len(body['schedule']['slots'])} slots, {len(body['programs'])} programs")
        for t, s in sorted(report.items(), key=lambda kv: -kv[1]):
            print(f"   {s*100:5.1f}%  {t}")
        if missing:
            print("   MISSING:", missing)
        if a.dry_run:
            continue
        cid = ensure_channel(a.tunarr, ch, cfg, existing)
        if ch.get("kind") == "settings":
            put_channel(a.tunarr, cid, channel_settings(ch, cfg), restart_clock=False)
            print("   settings applied"); continue
        # Any open session counts: Plex viewers arrive through Tunarr's internal relay as 127.0.0.1,
        # so the address can't tell a real TV from a test pull.
        if api(a.tunarr, "/api/sessions").get(cid) and not ch.get("force"):
            print("   SKIP: channel has an open session (someone may be watching)"); continue
        if not a.settings_only:
            api(a.tunarr, f"/api/channels/{cid}/programming", body, timeout=600)
        # The generated lineup's half-hour padding assumes it starts now, but playback
        # position is (now - startTime) % duration. Re-anchoring startTime makes Tunarr
        # regenerate from the saved schedule aligned to the wall clock.
        put_channel(a.tunarr, cid, channel_settings(ch, cfg),
                    restart_clock=not a.settings_only)
        after = api(a.tunarr, f"/api/channels/{cid}")
        print(f"   applied: duration {after['duration']/864e5:.1f} days, "
              f"filler {[f['id'][:8] for f in after.get('fillerCollections', [])]}")


def channel_settings(ch, cfg):
    """Per-channel fields build.py owns: name and commercial pool."""
    mix = {} if ch["filler"] == "none" else ch["filler"]
    pools = [{"id": cfg["pools"][k], "weight": w, "cooldownSeconds": 0} for k, w in mix.items()]
    ids = cfg.get("ident_lists", {}).get(str(ch["number"]), {})
    if "chime" in ids:
        pools.append({"id": ids["chime"], "weight": 15, "cooldownSeconds": 900})
    if "voice" in ids:
        pools.append({"id": ids["voice"], "weight": 10, "cooldownSeconds": 3600})
    return {"name": ch["name"], "fillerRepeatCooldown": cfg["defaults"]["fillerRepeatCooldownMs"],
            "fillerCollections": pools}


def put_channel(base, cid, fields, restart_clock):
    chan = api(base, f"/api/channels/{cid}")
    for k in ("programCount", "sessions", "fallback", "transcoding"):
        chan.pop(k, None)
    chan.update(fields)
    if restart_clock:
        chan["startTime"] = int(time.time()) // 60 * 60000
    api(base, f"/api/channels/{cid}", chan, method="PUT", timeout=600)


def ensure_channel(base, ch, cfg, existing):
    """Return the channel id, creating it from the template channel if missing."""
    if ch["number"] in existing:
        return existing[ch["number"]]["id"]
    tmpl = api(base, f"/api/channels/{existing[cfg['template_channel']]['id']}")
    for k in ("id", "programCount", "sessions", "fallback", "transcoding", "watermark"):
        tmpl.pop(k, None)
    tmpl.update(channel_settings(ch, cfg), number=ch["number"], duration=0,
                startTime=int(time.time()) // 60 * 60000, id=str(uuid.uuid4()))
    tmpl["icon"] = dict(tmpl["icon"], path=f"{base}/images/uploads/ch-{ch['number']}-{ch.get('slug', ch['number'])}-v4.png")
    api(base, "/api/channels", {"type": "new", "channel": tmpl}, timeout=120)
    created = {c["number"]: c for c in api(base, "/api/channels")}
    existing.update(created)
    print(f"   created channel {ch['number']}")
    return created[ch["number"]]["id"]


if __name__ == "__main__":
    sys.exit(main())
