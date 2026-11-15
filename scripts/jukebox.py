#!/usr/bin/env python3
"""Build the music channels as one-week manual lineups.

  13 The Jukebox  audio channel: rendered song videos (album art + now playing), artists spread apart
  15 The Stage    music videos with artists spread apart, plus a concert every night at 20:00 Central
                  (rotating through the concert library; long ones simply run late)

usage: jukebox.py [--only 13,15] [--dry-run] [--tunarr URL] [--seed N]
Rerun weekly (the nightly job does Sundays, and whenever either library grows).
"""
import os
import argparse, collections, datetime, json, math, random, re, sys, time, urllib.request
from zoneinfo import ZoneInfo

JUKEBOX_LIB, STAGE_LIB = "Jukebox", "Music Videos Concerts"
MIN_MS, MAX_MS = 75_000, 15 * 60_000
CONCERT_MIN = 30 * 60_000
SKIP_ARTISTS = {a.strip() for a in os.environ.get("JUKEBOX_SKIP_ARTISTS", "Pink noise: Ref. -14dB Fs").split("|") if a.strip()}
ARTIST_GAP = 6
WEEK = 7 * 864e5
CENTRAL = ZoneInfo(os.environ.get("HOUSE_TZ", "America/Chicago"))


def api(base, path, body=None, method=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def artist_of(title):
    """'01. Aphex Twin - Come to Daddy' -> 'Aphex Twin'."""
    return re.sub(r"^\d+[.)]\s*", "", (title or "?")).split(" - ")[0].strip()


def spread(items, rng):
    """Interleave artists: weighted by sqrt(remaining), never within ARTIST_GAP of the same artist."""
    by = collections.defaultdict(list)
    for t in items:
        by[t["artist"]].append(t)
    for v in by.values():
        rng.shuffle(v)
    out, recent = [], collections.deque(maxlen=ARTIST_GAP)
    while any(by.values()):
        pool = [a for a, v in by.items() if v and a not in recent] or [a for a, v in by.items() if v]
        a = rng.choices(pool, weights=[math.sqrt(len(by[x])) for x in pool])[0]
        out.append(by[a].pop()); recent.append(a)
    return out


def endless(items, rng):
    """Yield items forever, reshuffling (artist-spread) on every pass."""
    while True:
        yield from spread(items, rng)


def week_lineup(stream, events):
    """Fill one week from `stream`, placing (offset_ms, item) events exactly; flex fills small gaps."""
    lineup, t = [], 0
    events = sorted(events, key=lambda e: e[0])
    while t < WEEK:
        nxt = events[0] if events else None
        if nxt and t >= nxt[0] - 1:
            lineup.append({"type": "content", "id": nxt[1]["id"], "duration": nxt[1]["duration"]})
            t += nxt[1]["duration"]; events.pop(0)
            while events and events[0][0] < t:      # a long concert swallowed the next start
                events.pop(0)
            continue
        item = next(stream)
        limit = nxt[0] if nxt else WEEK
        if t + item["duration"] > limit:
            lineup.append({"type": "flex", "duration": limit - t}); t = limit
            continue
        lineup.append({"type": "content", "id": item["id"], "duration": item["duration"]}); t += item["duration"]
    return lineup


def nightly_offsets(now, hour):
    """Offsets (ms from now) of the next seven `hour`:00 Central starts."""
    local = datetime.datetime.fromtimestamp(now / 1000, CENTRAL)
    first = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if first <= local:
        first += datetime.timedelta(days=1)
    return [int((first + datetime.timedelta(days=i)).timestamp() * 1000) - now for i in range(7)]


def apply(base, number, lineup, now, force=False):
    cid = {c["number"]: c["id"] for c in api(base, "/api/channels")}[number]
    if api(base, "/api/sessions").get(cid) and not force:
        print(f"   SKIP {number}: channel has an open session (someone may be watching)"); return
    api(base, f"/api/channels/{cid}/programming", {"type": "manual", "lineup": lineup}, timeout=600)
    chan = api(base, f"/api/channels/{cid}")
    for k in ("programCount", "sessions", "fallback", "transcoding"):
        chan.pop(k, None)
    chan["startTime"] = now
    chan["guideMinimumDuration"] = 60_000     # songs/videos are 3-5 min; the 5-min default hid them from the guide
    api(base, f"/api/channels/{cid}", chan, method="PUT", timeout=600)
    after = api(base, f"/api/channels/{cid}")
    print(f"   applied: {after['duration']/864e5:.3f} days")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tunarr", default=os.environ.get("TUNARR_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--only", default="13,15")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    only = {int(x) for x in a.only.split(",")}
    ms = api(a.tunarr, "/api/media-sources")[0]["id"]
    libs = {l["name"]: l["id"] for l in api(a.tunarr, f"/api/media-sources/{ms}/libraries")}
    now = int(time.time()) // 60 * 60000
    song = lambda x: {"id": x["id"], "duration": x["duration"], "artist": artist_of(x["program"].get("title"))}
    songs = [song(x) for x in api(a.tunarr, f"/api/media-libraries/{libs[JUKEBOX_LIB]}/programs")
             if MIN_MS <= x["duration"] <= MAX_MS]
    songs = [s for s in songs if s["artist"] not in SKIP_ARTISTS]

    if 13 in only and not songs:
        print("13 Jukebox: no rendered songs scanned yet, leaving lineup alone")
    elif 13 in only:
        lineup = week_lineup(endless(songs, rng), [])
        print(f"13 Jukebox: {len(songs)} songs, {sum(1 for x in lineup if x['type'] == 'content')} plays this week")
        if not a.dry_run:
            apply(a.tunarr, 13, lineup, now, force=a.force)

    if 15 in only:
        lineup, summary = stage_lineup(a.tunarr, libs, now, rng)
        print("15 Stage:", summary)
        if not a.dry_run:
            apply(a.tunarr, 15, lineup, now, force=a.force)


# ---------------- The Stage: programmed like a VJ would ----------------
HIPHOP = ("tribe called quest", "busta rhymes", "dr. dre", "ice cube", "lauryn hill", "missy elliott", "ol' dirty bastard",
          "snoop", "notorious b.i.g", "pharcyde", "wu tang", "beastie boys")
ELECTRONIC = ("aphex twin", "daft punk", "chemical brothers", "fatboy slim", "prodigy", "squarepusher", "massive attack",
              "unkle", "air", "bjork", "björk", "jamiroquai")
ZOMBIE = ("dragula", "living dead girl", "more human than human", "superbeast", "thunder kiss", "demonoid phenomenom",
          "spookshow baby", "never gonna stop", "meet the creeper", "return of the phantom stranger", "ballad of resurrection joe")
LATE_ONLY = ("come to daddy", "smack my bitch up", "closer", "freak on a leash")      # edgy videos: after 10 PM only
DAY_ROTATION = ["abba", "alt", "mj", "electronic", "hiphop", "alt"]
LATE_ROTATION = ["headbangers", "hiphop", "electronic", "alt"]
CONCERT_HOURS = (13, 20)          # afternoon matinee + night show
BLOCK_MS = 52 * 60_000


def classify(title):
    t = re.sub(r"^\d+[.)]\s*", "", title or "").lower()
    if "jackson" in t:
        return "mj"
    if t.startswith("abba"):
        return None if "gallery" in t else "abba"
    if any(z in t for z in ZOMBIE) or any(x in t for x in ("korn", "nine inch nails")) or any(x in t for x in LATE_ONLY):
        return "headbangers"
    if any(h in t for h in HIPHOP):
        return "hiphop"
    if any(t.startswith(e) for e in ELECTRONIC):
        return "electronic"
    return "alt"


def year_of(title):
    m = re.search(r"\b(19[5-9]\d|20[0-2]\d)\b", title or "")
    return int(m.group(1)) if m else 0


def stage_lineup(base, libs, now, rng):
    items = api(base, f"/api/media-libraries/{libs[STAGE_LIB]}/programs")
    blocks = collections.defaultdict(list)
    concerts = []
    for x in items:
        title = x["program"].get("title") or ""
        if x["duration"] >= CONCERT_MIN:
            concerts.append(x); continue
        if re.match(r"bonus\b", title, re.I) or x["duration"] < 150_000:   # extras and 30-90 s album interludes
            continue
        b = classify(title)
        if b:
            blocks[b].append({"id": x["id"], "duration": x["duration"], "artist": artist_of(title), "title": title})
    for b, lst in blocks.items():
        if b in ("mj", "abba"):
            lst.sort(key=lambda v: (year_of(v["title"]), v["title"]))      # release order, like a retrospective
        else:
            lst[:] = spread(lst, rng)
    src = [m for m in api(base, "/api/media-sources") if m["name"] == "Station IDs"][0]
    intros = {x["program"]["title"].replace("stage-", ""): x
              for x in api(base, f"/api/media-libraries/{src['libraries'][0]['id']}/programs")
              if x["program"]["title"].startswith("stage-")}
    rng.shuffle(concerts)
    pos = collections.Counter()
    events = [(off, concerts[i % len(concerts)]) for i, off in enumerate(
        sorted(o for h in CONCERT_HOURS for o in nightly_offsets(now, h)))] if concerts else []
    lineup, t, rot_day, rot_late, prev_block = [], 0, 0, 0, None

    def add(x):
        nonlocal t
        lineup.append({"type": "content", "id": x["id"], "duration": x["duration"]}); t += x["duration"]

    while t < WEEK:
        if events and t >= events[0][0] - 60_000:
            if "concert" in intros:
                add(intros["concert"])
            add(events[0][1]); events.pop(0)
            while events and events[0][0] < t:
                events.pop(0)
            continue
        local = datetime.datetime.fromtimestamp((now + t) / 1000, CENTRAL)
        late = local.hour >= 22 or local.hour < 4
        rot, idx = (LATE_ROTATION, rot_late) if late else (DAY_ROTATION, rot_day)
        b = rot[idx % len(rot)]
        if b == prev_block or not blocks.get(b):
            b = rot[(idx + 1) % len(rot)]
        if late: rot_late += 1
        else: rot_day += 1
        if not blocks.get(b):
            if not any(blocks.values()):
                raise SystemExit("Stage: no music videos classified")
            continue
        prev_block = b
        limit = events[0][0] if events else WEEK
        if limit - t < 15 * 60_000:        # too close to the next concert for a block: commercials until then
            lineup.append({"type": "flex", "duration": limit - t}); t = limit
            continue
        if b in intros and t + intros[b]["duration"] < limit:
            add(intros[b])
        block_start = t
        lst = blocks[b]
        while t - block_start < BLOCK_MS:
            v = lst[pos[b] % len(lst)]
            if t + v["duration"] > limit:
                break
            add(v); pos[b] += 1
        if t == block_start or (events and limit - t < 10 * 60_000 and t < limit):   # pad up to the concert
            step = max(60_000, limit - t) if events else 60_000
            lineup.append({"type": "flex", "duration": step}); t += step
    sizes = {b: len(v) for b, v in blocks.items()}
    return lineup, f"blocks {sizes}, {len(concerts)} concerts at {CONCERT_HOURS}"


if __name__ == "__main__":
    sys.exit(main())
