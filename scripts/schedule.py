#!/usr/bin/env python3
"""House TV scheduler: builds every TV channel's lineup directly (Tunarr "manual" programming).

Lineup rules:
  * commercials: exactly BREAK_MS between programs, never longer (no clock padding)
  * shows rotate by airtime share (sqrt of runtime, capped; see build.plan_channel), never the same
    show twice in a row, episodes in order, and each show remembers where it left off (cursors.json)
  * dayparts (Yard Saturday morning, Castle bedtime, Den evenings) are followed by local time;
    blocks start when the previous program ends, so "7:00" means around 7:00
  * seasonal holiday items (October/December) mix in at their share, and at "seasonalAt" times
  * "kind": "holiday" channels (16 The North Pole) air their movie list (plus matching episodes if "episodes" is set), 24/7

usage: schedule.py [--only N,..] [--dry-run] [--force] [--config channels.json] [--tunarr URL]
"""
import argparse, datetime, json, math, os, random, re, sys, time
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build import (api, catalogue, channel_settings, ensure_channel, load_config, plan_channel,  # noqa: E402
                   put_channel, season, seasonal_items)

BREAK_MS = 180_000           # 3 minutes of commercials between programs
DAYS = 21                    # lineup length; the nightly job rebuilds well before it loops
CENTRAL = ZoneInfo(os.environ.get("HOUSE_TZ", "America/Chicago"))
DOW = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MOVIE_GAP_H = 4              # daypart channels with movies: at most one movie per this many hours
NO_REPEAT_MS = 4 * 864e5     # a movie or holiday item never airs twice within 4 days
FAMILY = {"G", "PG", "TV-Y", "TV-Y7", "TV-G", "TV-PG", "Approved"}   # holiday channel: fine any hour


def day_set(spec):
    if spec in ("all", "mon-sun"):
        return set(range(7))
    out = set()
    for part in spec.split(","):
        if "-" in part:
            a, b = (DOW.index(x) for x in part.split("-"))
            out |= set(range(a, b + 1)) if a <= b else set(range(a, 7)) | set(range(0, b + 1))
        else:
            out.add(DOW.index(part))
    return out


def mins(hhmm):
    h, m = map(int, hhmm.split(":")); return h * 60 + m


def in_part(part, local):
    day, minute = local.weekday(), local.hour * 60 + local.minute
    s, e = mins(part["start"]), mins(part["end"])
    if s < e:
        return day in day_set(part["days"]) and s <= minute < e
    return (day in day_set(part["days"]) and minute >= s) or (((day - 1) % 7) in day_set(part["days"]) and minute < e)


class Picker:
    """Smooth weighted round robin over keys, skipping the previous pick when possible."""
    def __init__(self, weights):
        self.w = {k: v for k, v in weights.items() if v > 0}
        self.credit = {k: 0.0 for k in self.w}

    def pick(self, allowed, prev):
        pool = [k for k in allowed if k in self.w]
        if not pool:
            return None
        for k in pool:
            self.credit[k] += self.w[k]
        cand = [k for k in pool if k != prev] or pool
        k = max(cand, key=lambda k: self.credit[k])
        self.credit[k] -= sum(self.w[x] for x in pool)
        return k


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "channels.json"))
    ap.add_argument("--tunarr", default="http://127.0.0.1:8000")
    ap.add_argument("--only"); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-guide", action="store_true", help="skip the guide refresh (nightly does one at the end)")
    a = ap.parse_args()
    cfg = load_config(a.config)
    cursor_path = os.path.join(os.path.dirname(os.path.abspath(a.config)), "cursors.json")
    cursors = json.load(open(cursor_path)) if os.path.exists(cursor_path) else {}
    only = {int(x) for x in a.only.split(",")} if a.only else None
    shows, movies = catalogue(a.tunarr)
    existing = {c["number"]: c for c in api(a.tunarr, "/api/channels")}
    now = int(time.time()) // 60 * 60_000
    # cursors are saved at the next 04:15 Central (when tomorrow's nightly run starts from)
    nl = datetime.datetime.fromtimestamp(now / 1000, CENTRAL).replace(hour=4, minute=15, second=0, microsecond=0)
    if nl.timestamp() * 1000 <= now:
        nl += datetime.timedelta(days=1)
    snap_at = int(nl.timestamp() * 1000) - now

    for ch in cfg["channels"]:
        n = ch["number"]
        if (only and n not in only) or ch.get("kind") in ("settings", "external"):
            continue
        key = str(n)
        cur = dict(cursors.get(key, {}))   # a copy: a skipped channel must not save its advanced cursor
        if ch.get("kind") == "holiday":
            lineup, t_ms, snapshot = holiday_lineup(ch, cfg, shows, movies, cur, now, snap_at)
            missing = [t for t in cfg["movies"].get(key, []) if t not in movies]
            if publish(a, ch, cfg, lineup, t_ms, missing, existing, now):
                cursors[key] = snapshot; save_cursors(cursor_path, cursors)
            continue
        _, shares, missing = plan_channel(ch, cfg, shows, movies, None)   # airtime shares only
        movie_titles = [t for t in cfg["movies"].get(key, []) if t in movies]
        sz, sitems = seasonal_items(ch, cfg, shows, movies)
        sitems = sitems if len(sitems) >= 3 else []
        rng = random.Random(f"{n}-{now // 864e5}")

        def per_show(t):
            eps = shows[t]["eps"]; avg = sum(e[1] for e in eps) / len(eps)
            if t in ch.get("programCount", {}):
                return ch["programCount"][t]
            auto = max(1, int(26 * 60000 // avg)) if avg < 15 * 60000 else 1
            return max(auto, ch.get("programCountAll", 1))

        # weights = share of airtime / average airtime of one pick (so shares come out right)
        weights = {}
        for t, sh in shares.items():
            if t in shows:
                eps = shows[t]["eps"]; weights[t] = sh / (sum(e[1] for e in eps) / len(eps) * per_show(t) + BREAK_MS)
        if movie_titles and "[movies]" in shares:
            weights["[movies]"] = shares["[movies]"] / (sum(movies[t][0][1] for t in movie_titles) / len(movie_titles) + BREAK_MS)
        if sitems:
            sk = next((k for k in shares if k.startswith("[" + sz["name"])), None)
            if sk:
                # each holiday item at most about once per NO_REPEAT window
                share = min(shares[sk], sum(d for _, d in sitems) / (NO_REPEAT_MS + sum(d for _, d in sitems)))
                weights["[seasonal]"] = share / (sum(d for _, d in sitems) / len(sitems) + BREAK_MS)
        if ch.get("dayparts"):
            # daypart channels weight by runtime^0.25: big libraries still air a bit more,
            # but a few shows don't dominate a small pool
            rt = {t: sum(e[1] for e in shows[t]["eps"]) for p in ch["dayparts"] for t in p.get("shows", []) if t in shows}
            weights = {t: r ** 0.25 / (r / len(shows[t]["eps"]) * per_show(t)) for t, r in rt.items()}
        picker = Picker(weights)

        # movie order: persistent shuffled cycle
        morder = [t for t in cur.get("_movie_order", []) if t in movie_titles]
        morder += [t for t in movie_titles if t not in morder]
        if not cur.get("_movie_order"):
            rng.shuffle(morder)
        mpos = cur.get("_movie_pos", 0)
        spos = cur.get("_seasonal_pos", 0)
        if sitems:
            rng.shuffle(sitems)

        lineup, t_ms, prev, last_movie, snapshot = [], 0, None, -10**12, None
        aired = {}               # uid -> t_ms last aired (movies and holiday items)

        def next_fresh(order, pos, uid_of, fallback=True):
            """Advance through a cyclic order to the first item not aired within NO_REPEAT_MS."""
            for k in range(len(order)):
                item = order[(pos + k) % len(order)]
                if t_ms - aired.get(uid_of(item), -10**15) >= NO_REPEAT_MS:
                    return item, pos + k + 1
            if not fallback:
                return None, pos
            # small libraries can't cover the window: take whatever aired longest ago
            k = min(range(len(order)), key=lambda k: aired.get(uid_of(order[(pos + k) % len(order)]), -10**15))
            return order[(pos + k) % len(order)], pos + k + 1
        used_fixed = set()

        def add(uid, dur):
            nonlocal t_ms
            lineup.append({"type": "content", "id": uid, "duration": dur}); t_ms += dur

        while t_ms < DAYS * 864e5:
            if snapshot is None and t_ms >= snap_at:
                snapshot = dict(cur, _movie_pos=mpos, _seasonal_pos=spos, _movie_order=morder)
            local = datetime.datetime.fromtimestamp((now + t_ms) / 1000, CENTRAL)
            hhmm_now = local.hour * 60 + local.minute
            choice = None
            if ch.get("dayparts"):
                part = next((p for p in ch["dayparts"] if in_part(p, local)), ch["dayparts"][-1])
                if local.weekday() in day_set(part["days"]):
                    # a block with a fixed lineup plays it in order, back to back, once per day
                    for hhmm, show in sorted(part.get("fixed", {}).items(), key=lambda kv: mins(kv[0])):
                        tag = (local.date(), hhmm)
                        if tag not in used_fixed and show in shows:
                            choice = show; used_fixed.add(tag); break
                    for hhmm in part.get("seasonalAt", []) if sitems else []:
                        tag = (local.date(), "S" + hhmm)
                        if choice is None and tag not in used_fixed and 0 <= hhmm_now - mins(hhmm) < 25:
                            choice = "[seasonal]"; used_fixed.add(tag)
                if choice is None and part.get("movies") and movie_titles and t_ms - last_movie >= MOVIE_GAP_H * 3.6e6 \
                        and hhmm_now % 60 < 40:
                    choice = "[movies]"
                if choice is None:
                    choice = picker.pick([s for s in part.get("shows", []) if s in shows], prev)
            else:
                choice = picker.pick(list(weights), prev)
            if choice is None:
                break
            if choice == "[movies]":
                title, mpos = next_fresh(morder, mpos, lambda t: movies[t][0][0])
                if title is None:            # every movie aired recently: play a show instead
                    prev = choice; continue
                aired[movies[title][0][0]] = t_ms
                add(movies[title][0][0], movies[title][0][1]); last_movie = t_ms
            elif choice == "[seasonal]":
                item, spos = next_fresh(sitems, spos, lambda it: it[0], fallback=False)
                if item is None:
                    prev = choice; continue
                aired[item[0]] = t_ms
                add(item[0], item[1])
            else:
                eps = shows[choice]["eps"]
                for _ in range(per_show(choice)):
                    i = cur.get(choice, 0) % len(eps)
                    for _skip in range(len(eps)):          # skip an episode that just aired as a holiday pick
                        if t_ms - aired.get(eps[i][0], -10**15) >= NO_REPEAT_MS:
                            break
                        i = (i + 1) % len(eps)
                    aired[eps[i][0]] = t_ms
                    add(eps[i][0], eps[i][1]); cur[choice] = i + 1
            prev = choice
            lineup.append({"type": "flex", "duration": BREAK_MS}); t_ms += BREAK_MS

        if publish(a, ch, cfg, lineup, t_ms, missing, existing, now):
            cursors[key] = snapshot or dict(cur, _movie_pos=mpos, _seasonal_pos=spos, _movie_order=morder)
            save_cursors(cursor_path, cursors)
    if not a.dry_run and not a.no_guide:
        refresh_guide(a.tunarr)


def publish(a, ch, cfg, lineup, t_ms, missing, existing, now):
    """Print the summary and (unless dry run) POST the lineup. True when it was applied."""
    content = [x for x in lineup if x["type"] == "content"]
    print(f"== {ch['number']} {ch['name']}: {len(content)} programs over {t_ms/864e5:.1f} days, breaks {BREAK_MS//60000} min"
          + (f", MISSING {missing}" if missing else ""))
    if a.dry_run:
        return False
    if t_ms < 864e5:
        print("   SKIP: lineup under a day (empty pool?), keeping the old one"); return False
    cid = ensure_channel(a.tunarr, ch, cfg, existing)
    if api(a.tunarr, "/api/sessions").get(cid) and not a.force:
        print("   SKIP: channel has an open session (someone may be watching)"); return False
    # set the clock first: changing startTime afterwards makes Tunarr regenerate from any old saved schedule
    put_channel(a.tunarr, cid, dict(channel_settings(ch, cfg), startTime=now), restart_clock=False)
    api(a.tunarr, f"/api/channels/{cid}/programming", {"type": "manual", "lineup": lineup}, timeout=600)
    # a manual programming POST clears fillerCollections; without them the 3-minute breaks
    # have nothing to play and Tunarr loops whatever single clip it can find (the station ID)
    put_channel(a.tunarr, cid, channel_settings(ch, cfg), restart_clock=False)
    after = api(a.tunarr, f"/api/channels/{cid}")
    ok = after["startTime"] == now and len(after.get("fillerCollections", [])) == len(channel_settings(ch, cfg)["fillerCollections"])
    print(f"   applied: {after['duration']/864e5:.1f} days, clock+commercials {'ok' if ok else 'MISMATCH'}")
    return True


def save_cursors(path, cursors):
    tmp = path + ".tmp"
    json.dump(cursors, open(tmp, "w"), indent=1); os.replace(tmp, path)


def holiday_items(ch, cfg, shows, movies):
    """Every episode in the library whose title matches the channel's pattern, plus its movie list.
    Each item: (uid, duration, rating, group) where group is the show or movie title."""
    items = []
    if ch.get("episodes"):           # no pattern: movies only
        rx = re.compile(ch["episodes"], re.I)
        drop, skip = set(ch.get("excludeEpisodes", [])), set(ch.get("skipShows", [])) | set(cfg.get("skip_shows", []))
        items = [(e[0], e[1], sh["rating"], t) for t, sh in shows.items() if t not in skip
                 for e in sh["eps"] if rx.search(e[2]) and e[2] not in drop]
    for t in cfg["movies"].get(str(ch["number"]), []):
        if t in movies:
            m = movies[t][0]
            items.append((m[0], m[1], ch.get("ratings", {}).get(t, m[2]), t))
    return items


def holiday_lineup(ch, cfg, shows, movies, cur, now, snap_at):
    """A 24/7 holiday channel: least-recently-aired item next, never the same show twice in a row,
    a movie at most every movieGapH hours (plus one at each movieAt time and any dated pick),
    and grown-up ratings only after their evening start time.
    Airing history carries across nightly rebuilds in the cursor (_aired: uid -> epoch ms)."""
    items = holiday_items(ch, cfg, shows, movies)
    movie_ids = {u for t in cfg["movies"].get(str(ch["number"]), []) if t in movies for u in [movies[t][0][0]]}
    after = {r: mins(h) for r, h in ch.get("ratingAfter", {}).items()}
    late_end = mins(ch.get("lateUntil", "05:00"))
    gap, fresh = ch.get("movieGapH", 3) * 3.6e6, ch.get("noRepeatH", 36) * 3.6e6
    aired = {u: ts - now for u, ts in cur.get("_aired", {}).items()}     # ms relative to now (past = negative)
    rng = random.Random(f"{ch['number']}-{now // 864e5}")
    jitter = {it[0]: rng.random() for it in items}
    lineup, t_ms, prev, last_movie, history = [], 0, None, -10**15, []

    used = set()

    def allowed(rating, minute, dur=0):
        if rating in FAMILY:
            return True
        start = after.get(rating, after.get("default", mins("21:00")))   # unrated or unknown: late night only
        # after midnight it must also end by lateUntil, so nothing grown-up runs into the morning
        return minute >= start and minute + dur / 60000 < 1440 + late_end or minute + dur / 60000 <= late_end

    def due(tag, hhmm, minute):
        """True once per tag, for the first program starting at or up to 2 hours after hhmm."""
        if tag in used or not 0 <= minute - mins(hhmm) < 120:
            return False
        used.add(tag); return True

    while t_ms < DAYS * 864e5 and items:
        local = datetime.datetime.fromtimestamp((now + t_ms) / 1000, CENTRAL)
        minute = local.hour * 60 + local.minute
        # dated picks ("12-24 21:00": "Die Hard"), then the nightly movie, then least recently aired
        title = next((t for when, t in ch.get("dated", {}).items()
                      if local.strftime("%m-%d") == when.split()[0] and due((local.date(), when), when.split()[1], minute)), None)
        ok = [it for it in items if it[3] == title]
        night = any([due((local.date(), h), h, minute) for h in ch.get("movieAt", [])])   # a dated pick counts as it
        if not ok and night:
            ok = [it for it in items if it[0] in movie_ids and allowed(it[2], minute, it[1]) and it[3] != prev]
        if not ok:
            ok = [it for it in items if allowed(it[2], minute, it[1]) and it[3] != prev
                  and (it[0] not in movie_ids or t_ms - last_movie >= gap)]
            ok = [it for it in ok if t_ms - aired.get(it[0], -10**15) >= fresh] or ok   # small pool: allow early repeats
        if not ok:
            ok = [it for it in items if allowed(it[2], minute, it[1])] or [it for it in items if it[2] in FAMILY]
        if not ok:
            break                    # nothing safe to air: publish() refuses an empty lineup
        it = min(ok, key=lambda it: (aired.get(it[0], -10**15), jitter[it[0]]))
        aired[it[0]] = t_ms; history.append((it[0], t_ms))
        if it[0] in movie_ids:
            last_movie = t_ms
        lineup.append({"type": "content", "id": it[0], "duration": it[1]}); t_ms += it[1]
        prev = it[3]
        lineup.append({"type": "flex", "duration": BREAK_MS}); t_ms += BREAK_MS
    # tomorrow's run starts at snap_at: remember what will have aired by then
    past = dict(cur.get("_aired", {}))
    for u, ts in history:
        if ts < snap_at:
            past[u] = now + ts
    return lineup, t_ms, {"_aired": past}


def refresh_guide(base):
    """On the host: run the guide refresh unit (XMLTV, proxy, Plex if changed). Remote: regenerate
    Tunarr's XMLTV only; the 30-minute timer on the host finishes the job."""
    try:
        if base.startswith("http://127.0.0.1"):
            import subprocess
            subprocess.run(["systemctl", "start", "house-tv-guide.service"], check=False)
        else:
            api(base, "/api/xmltv/refresh", {})
    except Exception as e:
        print("guide refresh failed:", e)


if __name__ == "__main__":
    sys.exit(main())
