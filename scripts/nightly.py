#!/usr/bin/env python3
"""House TV nightly upkeep (cron on the Plex host, ~4:15 AM Central).

1. Ask Tunarr to rescan Plex libraries (new episodes/movies/jukebox videos).
2. Auto-assign any show or movie that no channel claims, by rating/genre rules
   (kids channels only ever get kid ratings; unrated movies go to The Late Movie).
   Movies tagged in Plex with a channel's "plexLabel" (16: Christmas) join that channel too.
   Assignments persist in assignments.json; channels.json stays hand-authored.
3. Rebuild only channels whose inputs changed (content set, holiday season, CST/CDT offset),
   skipping any channel with a real viewer (retried next night).
4. Reshuffle The Jukebox on Sundays (and when new jukebox videos appear).
5. Tell Plex to reload the guide if anything changed.

usage: nightly.py [--dry-run] [--force]
"""
import argparse, datetime, fcntl, hashlib, json, os, subprocess, sys, time
from zoneinfo import ZoneInfo
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build import api, catalogue, load_config, season  # noqa: E402

TUNARR = "http://127.0.0.1:8000"
CONFIG = os.path.join(HERE, "channels.json")
if not os.path.exists(CONFIG):
    _root = os.path.normpath(os.path.join(HERE, "..", "channels.json"))
    if os.path.exists(_root):
        CONFIG = _root
ASSIGN = os.path.join(HERE, "assignments.json")
STATE = os.path.join(HERE, "state.json")
CENTRAL = ZoneInfo(os.environ.get("HOUSE_TZ", "America/Chicago"))
KID_TV = {"TV-Y": 3, "TV-Y7": 2}
DRAMA = {"Drama", "Crime", "Thriller", "Mystery", "Western", "Science Fiction"}
DOCS = {"Documentary", "Reality", "Game Show", "Talk Show", "History"}


def log(*a):
    print(datetime.datetime.now(CENTRAL).strftime("%F %T"), *a, flush=True)


def assign_show(title, info):
    r, g = info.get("rating"), set(info.get("genres", []))
    if "star trek" in title.lower():
        return 10
    if r in KID_TV:
        return KID_TV[r]
    if r == "TV-G" and "Animation" in g:
        return 2            # general-audience cartoons are Yard-safe (Tom and Jerry is TV-G)
    if r == "TV-PG" and "Animation" in g:
        return 12
    if g & DOCS:
        return 5
    if r in ("TV-14", "TV-MA") and g & DRAMA and "Comedy" not in g:
        return 11
    return 4


def assign_movie(title, rating):
    if "veggietales" in title.lower().replace(" ", ""):
        return [3]
    return {"G": [7, 6], "TV-G": [7, 6], "TV-Y": [7], "TV-Y7": [7], "PG": [6], "TV-PG": [6]}.get(rating, [8])


def scan_libraries():
    for ms in api(TUNARR, "/api/media-sources"):
        if ms.get("type") != "plex":
            continue
        for lib in ms.get("libraries", []):
            if lib.get("enabled"):
                try:
                    api(TUNARR, f"/api/media-sources/{ms['id']}/libraries/{lib['id']}/scan", {})
                except Exception as e:
                    log("scan failed", lib["name"], e)
    busy = []
    for _ in range(60):                     # wait up to ~10 min for scans to finish
        time.sleep(10)
        busy = [l["name"] for ms in api(TUNARR, "/api/media-sources") if ms.get("type") == "plex"
                for l in ms.get("libraries", []) if l.get("enabled") and
                api(TUNARR, f"/api/media-sources/{ms['id']}/{l['id']}/status").get("state") == "scanning"]
        if not busy:
            return
    log("scan still running after 10 min, continuing with current catalogue:", busy)


def auto_assign(cfg, shows, movies, dry):
    claimed = {t for ch in cfg["channels"] for t in ch.get("shows", ())} | set(cfg.get("skip_shows", []))
    claimed_m = {t for lst in cfg["movies"].values() for t in lst} | set(cfg.get("skip_movies", []))
    extra = json.load(open(ASSIGN)) if os.path.exists(ASSIGN) else {"shows": {}, "movies": {}}
    new = []
    for title, info in shows.items():
        if title not in claimed and title not in extra["shows"]:
            extra["shows"][title] = assign_show(title, info); new.append((title, extra["shows"][title]))
    for title, copies in movies.items():
        if title not in claimed_m and title not in extra["movies"]:
            extra["movies"][title] = assign_movie(title, copies[0][2]); new.append((title, extra["movies"][title]))
    for t, ch in new:
        log("auto-assigned", repr(t), "->", ch)
    if new and not dry:
        save_json(ASSIGN, extra)
    return bool(new)


def label_assign(cfg, movies, dry):
    """Channels with "plexLabel": every Plex movie carrying that label goes on the channel.
    Recomputed nightly, so removing the label in Plex takes the movie off again."""
    want = {str(ch["number"]): ch["plexLabel"] for ch in cfg["channels"] if ch.get("plexLabel")}
    if not want:
        return False
    try:
        sys.path.insert(0, os.path.join(HERE, "..", "service"))
        from plexdvr import plex
        sections = [d["key"] for d in plex("GET", "/library/sections")[1]["Directory"] if d["type"] == "movie"]
        found = {num: {x["ratingKey"] for k in sections
                       for x in plex("GET", f"/library/sections/{k}/all", {"type": 1, "label": label})[1].get("Metadata", [])}
                 for num, label in want.items()}
    except Exception as e:
        log("plex labels failed, keeping last list:", e)
        return False
    extra = json.load(open(ASSIGN)) if os.path.exists(ASSIGN) else {"shows": {}, "movies": {}}
    labeled = {num: sorted(t for t, copies in movies.items() if any(c[4] in keys for c in copies))
               for num, keys in found.items()}
    if labeled == extra.get("labeled"):
        return False
    for num, titles in labeled.items():
        log(f"label {want[num]!r} -> {num}:", len(titles), "movies")
    extra["labeled"] = labeled
    if not dry:
        save_json(ASSIGN, extra)
    return True


def fingerprints(cfg, shows, movies):
    """Per channel: hash of everything that should trigger a rebuild."""
    sz = season(cfg)
    offset = int(datetime.datetime.now(CENTRAL).utcoffset().total_seconds())
    out = {}
    for ch in cfg["channels"]:
        if ch.get("kind") in ("settings", "external"):
            continue
        ids = sorted(e[0] for t in ch.get("shows", ()) if t in shows for e in shows[t]["eps"])
        ids += sorted(m[0][0] for t in cfg["movies"].get(str(ch["number"]), []) if (m := movies.get(t)))
        key = json.dumps([ids, sz and sz["name"], offset if ch.get("dayparts") else 0, ch], sort_keys=True)
        out[str(ch["number"])] = hashlib.sha1(key.encode()).hexdigest()
    return out


def weather_music(month):
    """WeatherStar plays the holiday jazz set in December, the regular set otherwise."""
    target = "holiday" if month == 12 else "regular"
    link = "/opt/house-tv/weather/music/active"
    if os.path.realpath(link) != os.path.realpath(f"/opt/house-tv/weather/music/{target}"):
        tmp = link + ".new"
        os.symlink(target, tmp); os.replace(tmp, link)
        r = subprocess.run(["docker", "compose", "-f", "/opt/house-tv/weather/docker-compose.yml", "up", "-d",
                            "--force-recreate", "ws4channels"], capture_output=True, text=True)
        log("weather music ->", target, r.returncode)


def save_json(path, data):
    """Atomic write: a crash never leaves a half-written state file."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-scan", action="store_true")
    ap.add_argument("--seed", action="store_true", help="record current inputs as built, change nothing")
    a = ap.parse_args()
    lock = os.open("/run/lock/house-tv-nightly.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    failed = False
    if not a.no_scan and not a.dry_run and not a.seed:
        # render any new songs into jukebox videos, let Plex analyze new music files, then rescan Tunarr
        r = subprocess.run([sys.executable, "/opt/house-tv/jukebox/render.py", "--workers", "3"], capture_output=True, text=True)
        log("render:", (r.stdout.strip().splitlines() or ["?"])[-1])
        r = subprocess.run([sys.executable, "/opt/house-tv/service/plexdvr.py", "analyze"], capture_output=True, text=True)
        log("plex analyze:", " ".join((r.stdout + r.stderr).split()))
        time.sleep(600)       # give Plex time to analyze before Tunarr reads durations
        scan_libraries()
    cfg = load_config(CONFIG)
    shows, movies = catalogue(TUNARR)
    if not a.seed and auto_assign(cfg, shows, movies, a.dry_run):
        cfg = load_config(CONFIG)
    if not a.seed and label_assign(cfg, movies, a.dry_run):
        cfg = load_config(CONFIG)
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    fps = fingerprints(cfg, shows, movies)
    if a.seed:
        state["fp"] = fps; save_json(STATE, state); log("seeded", len(fps), "channels"); return 0
    changed = [n for n, fp in fps.items() if a.force or state.get("fp", {}).get(n) != fp]
    log("inputs changed since last build:", changed or "none")
    built = []
    # schedule.py rebuilds every TV channel nightly (3-minute breaks, episode cursors carry on),
    # skipping any channel with an open session; skipped ones retry tomorrow.
    if not a.dry_run:
        r = subprocess.run([sys.executable, os.path.join(HERE, "schedule.py"), "--config", CONFIG, "--tunarr", TUNARR, "--no-guide"],
                           capture_output=True, text=True)
        out = r.stdout + r.stderr
        for line in out.strip().splitlines():
            log("schedule:", line)
        if r.returncode != 0:
            failed = True
        else:
            for n in fps:
                if f"== {n} " in out and "SKIP" not in out.split(f"== {n} ", 1)[1].split("==", 1)[0]:
                    state.setdefault("fp", {})[n] = fps[n]; built.append(n)
    today = datetime.datetime.now(CENTRAL)
    jb_videos = len([x for ms in api(TUNARR, "/api/media-sources") for l in ms.get("libraries", [])
                     if l["name"] in ("Jukebox", "Music Videos Concerts") and l.get("enabled")
                     for x in api(TUNARR, f"/api/media-libraries/{l['id']}/programs")])
    if not a.dry_run and (a.force or today.weekday() == 6 or jb_videos != state.get("jukebox_videos")):
        r = subprocess.run([sys.executable, os.path.join(HERE, "jukebox.py"), "--tunarr", TUNARR], capture_output=True, text=True)
        log("jukebox:", r.returncode, (r.stdout + r.stderr).strip().splitlines()[-1:] or "")
        if r.returncode == 0:
            state["jukebox_videos"] = jb_videos; built.append("13")
        else:
            failed = True
    if built and not a.dry_run:
        # Tunarr's streamer caches lineups and ignores manual programming updates until restarted;
        # without this the TV plays the old lineup while the guide shows the new one.
        r = subprocess.run(["docker", "restart", "tunarr"], capture_output=True, text=True)
        log("tunarr restart:", r.returncode)
        time.sleep(15)
    if (built or state.get("guide_dirty")) and not a.dry_run:
        r = subprocess.run(["systemctl", "start", "house-tv-guide.service"], capture_output=True, text=True)
        log("guide refresh:", r.returncode, (r.stdout + r.stderr).strip())
        state["guide_dirty"] = r.returncode != 0
        failed |= r.returncode != 0
    if not a.dry_run:
        weather_music(today.month)
        state["last_run"] = today.isoformat()
        save_json(STATE, state)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
