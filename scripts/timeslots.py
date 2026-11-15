#!/usr/bin/env python3
"""Daypart channels: generate a weekly half-hour grid from channels.json "dayparts" and POST it
as a Tunarr time-slot schedule (same show at the same time every week, like real TV).

usage: timeslots.py [--only N,..] [--dry-run]

A daypart: {"days": "mon-sun"|"sat"|..., "start": "19:00", "end": "07:00",
            "shows": [...], "fixed": {"07:00": "Show"}, "movies": true, "blockMin": 30}
Inside a daypart every block picks a show by weighted rotation (never the same show twice in a row);
"fixed" pins a show at a time. Times are US Central, converted to UTC with the current CST/CDT
offset (Tunarr runs on UTC and ignores timeZoneOffset), so the nightly job rebuilds these channels
when daylight saving flips.
"""
import os
import argparse, datetime, json, math, sys, time, urllib.request, uuid
from zoneinfo import ZoneInfo
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from build import api, catalogue, load_config, channel_settings, ensure_channel, ensure_custom_show, put_channel, seasonal_items  # noqa: E402

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]   # Tunarr week starts Sunday; converted below
CENTRAL = ZoneInfo(os.environ.get("HOUSE_TZ", "America/Chicago"))


def day_set(spec):
    if spec in ("all", "mon-sun"):
        return set(range(7))
    out = set()
    for part in spec.split(","):
        if "-" in part:
            a, b = (DAYS.index(x) for x in part.split("-"))
            out |= set(range(a, b + 1)) if a <= b else set(range(a, 7)) | set(range(0, b + 1))
        else:
            out.add(DAYS.index(part))
    return out


def minutes(hhmm):
    h, m = map(int, hhmm.split(":")); return h * 60 + m


def in_part(part, day, minute):
    s, e = minutes(part["start"]), minutes(part["end"])
    if s < e:
        return day in day_set(part["days"]) and s <= minute < e
    # crosses midnight: the part "belongs" to the day it starts on
    return (day in day_set(part["days"]) and minute >= s) or (((day - 1) % 7) in day_set(part["days"]) and minute < e)


def grid(ch, shows, movies_ok, seasonal_ok=False):
    """Return [(week_minute_from_monday, kind, title)] covering the week in blocks."""
    parts = ch["dayparts"]
    weights = {t: math.sqrt(sum(e[1] for e in shows[t]["eps"])) for p in parts for t in p.get("shows", []) if t in shows}
    debt = {t: 0.0 for t in weights}
    out, prev, m = [], None, 0
    while m < 7 * 1440:
        day, minute = divmod(m, 1440)
        part = next((p for p in parts if in_part(p, day, minute)), parts[-1])
        hhmm = f"{minute // 60:02d}:{minute % 60:02d}"
        block = part.get("blockMin", 30)
        if seasonal_ok and hhmm in part.get("seasonalAt", []):
            out.append((m, "seasonal", None)); prev = None; m += 30; continue
        if hhmm in part.get("fixed", {}) and day in day_set(part["days"]):
            out.append((m, "show", part["fixed"][hhmm])); prev = part["fixed"][hhmm]; m += 30; continue
        if part.get("movies") and movies_ok and minute % 120 == 0 and part.get("movieEvery", 0) and \
                (m // 120) % part["movieEvery"] == 0:
            out.append((m, "movie", None)); prev = None; m += 120; continue
        pool = [t for t in part["shows"] if t in weights]
        # weighted rotation: add each show's weight to its credit, play the richest that isn't the previous
        for t in pool:
            debt[t] += weights[t]
        pick = max((t for t in pool if t != prev or len(pool) == 1), key=lambda t: debt[t])
        debt[pick] -= sum(weights[t] for t in pool)
        out.append((m, "show", pick)); prev = pick; m += block
    return out


def schedule_body(ch, cfg, shows, movies, base=None):
    movie_titles = cfg["movies"].get(str(ch["number"]), [])
    sz, items = seasonal_items(ch, cfg, shows, movies)
    seasonal_id = None
    if sz and len(items) >= 3:
        seasonal_id = ensure_custom_show(base, f"{sz['name']} - {ch['name']}", items) if base else "dry-run"
    g = grid(ch, shows, bool(movie_titles), bool(seasonal_id))
    offset = -int(datetime.datetime.now(CENTRAL).utcoffset().total_seconds() // 60)   # CDT 300, CST 360
    slots, programs, seen = [], [], set()
    for m, kind, title in g:
        # Tunarr's week starts Sunday 00:00 in the container's zone (UTC; it ignores timeZoneOffset).
        # Grid is Monday-based Central time, so shift a day and add the current UTC offset.
        start_ms = ((m + 1440 + offset) % (7 * 1440)) * 60000
        base = {"startTime": start_ms, "id": str(uuid.uuid4()), "order": "next", "direction": "asc",
                "linkMode": "continue", "rerunOverflow": "flex", "padMs": 0, "latenessMs": 20 * 60000,
                "overflow": {"type": "duration", "maxMs": 3 * 60000}}
        if kind == "seasonal":
            slots.append({**base, "type": "custom-show", "customShowId": seasonal_id, "order": "shuffle"})
            if "seasonal" not in seen:
                programs += [u for u, _ in items]; seen.add("seasonal")
        elif kind == "movie":
            # movies may run up to 25 min past their 2 h block instead of leaving it empty
            slots.append({**base, "type": "movie", "order": "shuffle", "overflow": {"type": "duration", "maxMs": 25 * 60000}})
            if "movies" not in seen:
                programs += [movies[t][0][0] for t in movie_titles if t in movies]; seen.add("movies")
        else:
            slots.append({**base, "type": "show", "showId": shows[title]["id"], "seasonFilter": [], "seasonExcludeFilter": []})
            if title not in seen:
                programs += [e[0] for e in shows[title]["eps"]]; seen.add(title)
    slots.sort(key=lambda s: s["startTime"])
    sched = {"type": "time", "period": "week", "slots": slots, "flexPreference": "end", "latenessMs": 20 * 60000,
             "maxDays": cfg["defaults"]["maxDays"], "padMs": 60000, "overflow": {"type": "duration", "maxMs": 3 * 60000},
             "timeZoneOffset": offset, "startTomorrow": False}
    return {"type": "time", "programs": programs, "schedule": sched}, g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="channels.json")
    ap.add_argument("--tunarr", default="http://127.0.0.1:8000")
    ap.add_argument("--only"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.config)
    only = {int(x) for x in a.only.split(",")} if a.only else None
    shows, movies = catalogue(a.tunarr)
    existing = {c["number"]: c for c in api(a.tunarr, "/api/channels")}
    for ch in cfg["channels"]:
        if "dayparts" not in ch or (only and ch["number"] not in only):
            continue
        body, g = schedule_body(ch, cfg, shows, movies, None if a.dry_run else a.tunarr)
        print(f"\n== {ch['number']} {ch['name']}: {len(body['schedule']['slots'])} weekly slots")
        for m, kind, title in g:
            day, minute = divmod(m, 1440)
            if DAYS[day] in ("sat",) and 6 * 60 <= minute <= 12 * 60 or (day == 0 and minute in (18 * 60, 19 * 60, 20 * 60, 21 * 60, 2 * 60)):
                print(f"   {DAYS[day]} {minute // 60:02d}:{minute % 60:02d}  {title or '[movie]'}")
        if a.dry_run:
            continue
        cid = ensure_channel(a.tunarr, ch, cfg, existing)
        if api(a.tunarr, "/api/sessions").get(cid):      # any session may be a real Plex viewer
            print("   SKIP: channel has an open session (someone may be watching)"); continue
        api(a.tunarr, f"/api/channels/{cid}/programming", body, timeout=600)
        put_channel(a.tunarr, cid, channel_settings(ch, cfg), restart_clock=False)
        after = api(a.tunarr, f"/api/channels/{cid}")
        print(f"   applied: duration {after['duration']/864e5:.1f} days")


if __name__ == "__main__":
    sys.exit(main())
