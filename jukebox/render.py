#!/usr/bin/env python3
"""Render Plex music tracks into 720p "now playing" videos so Tunarr (which
can't stream audio-only items) can schedule them on The Jukebox.

Runs on the Plex host as root. Idempotent: skips tracks already rendered.
usage: render.py [--limit N] [--workers N]
Output: /mnt/media/Plex/Jukebox/<Artist>/<Artist> - <Title>.mp4  (Plex "Jukebox" library)
"""
import argparse, concurrent.futures as cf, hashlib, json, os, re, subprocess, sys, tempfile, urllib.parse, urllib.request

PLEX = "http://127.0.0.1:32400"
PREFS = "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml"
OUT = os.environ.get("JUKEBOX_OUT", "/mnt/media/Plex/Jukebox")
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MIN_MS, MAX_MS = 75_000, 15 * 60_000
SKIP_ARTISTS = {a.strip() for a in os.environ.get("JUKEBOX_SKIP_ARTISTS", "Pink noise: Ref. -14dB Fs").split("|") if a.strip()}
TOKEN = re.search(r'PlexOnlineToken="([^"]+)"', open(PREFS).read()).group(1)


def plex(path, raw=False):
    req = urllib.request.Request(PLEX + path, headers={"X-Plex-Token": TOKEN, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if raw else json.loads(data)["MediaContainer"]


def safe(s, n=80):
    return re.sub(r'[\\/:*?"<>|]+', "", s).strip()[:n] or "Unknown"


def music_section():
    forced = os.environ.get("PLEX_MUSIC_SECTION")
    if forced:
        return forced
    name = os.environ.get("PLEX_MUSIC_NAME", "Music")
    for d in plex("/library/sections").get("Directory", []):
        if d.get("type") == "artist" and d.get("title") == name:
            return str(d["key"])
    raise SystemExit(f"No Plex music library named {name!r}. Set PLEX_MUSIC_SECTION or PLEX_MUSIC_NAME.")


def tracks():
    out = []
    for t in plex(f"/library/sections/{music_section()}/all?type=10").get("Metadata", []):
        artist = t.get("originalTitle") or t.get("grandparentTitle") or "Unknown"
        dur = t.get("duration") or 0
        if artist in SKIP_ARTISTS or not (MIN_MS <= dur <= MAX_MS):
            continue
        part = t["Media"][0]["Part"][0]
        out.append({"artist": artist, "title": t.get("title", "Untitled"), "album": t.get("parentTitle", ""),
                    "year": t.get("parentYear"), "thumb": t.get("parentThumb") or t.get("grandparentThumb"),
                    "file": part["file"], "key": t["ratingKey"]})
    return out


def art(thumb):
    """Album art cached by thumb path; falls back to a plain card when missing."""
    if not thumb:
        return None
    path = f"{CACHE}/{hashlib.sha1(thumb.encode()).hexdigest()}.jpg"
    if not os.path.exists(path):
        try:
            data = plex(f"/photo/:/transcode?width=600&height=600&minSize=1&url={urllib.parse.quote(thumb)}", raw=True)
            open(path, "wb").write(data)
        except Exception:
            return None
    return path


def render(t):
    folder = f"{OUT}/{safe(t['artist'], 60)}"
    dest = f"{folder}/{safe(t['artist'], 60)} - {safe(t['title'])}.mp4"
    # MP4, not MKV: ffprobe reads 1 fps MKV as 1000 fps and Tunarr's encoder refuses that rate
    if os.path.exists(dest) or os.path.exists(dest[:-4] + ".mkv"):
        return "skip"
    os.makedirs(folder, exist_ok=True)
    cover = art(t["thumb"])
    with tempfile.TemporaryDirectory() as tmp:
        files = {}
        for k, v in (("a", t["artist"]), ("t", t["title"]),
                     ("b", f"{t['album']}  ({t['year']})" if t["year"] else t["album"])):
            files[k] = f"{tmp}/{k}.txt"; open(files[k], "w").write(v)
        card = f"{tmp}/card.png"
        # DejaVu Bold averages ~0.62 em per char; shrink each line to fit the 600px text column
        fit = lambda txt, size: max(18, min(size, int(600 / (0.62 * max(1, len(txt))))))
        fa, ft = fit(t["artist"], 52), fit(t["title"], 40)
        fb = fit(open(files["b"]).read(), 30)
        text = (f"drawtext=fontfile={FONT}:textfile={files['a']}:fontsize={fa}:fontcolor=white:x=640:y=250:"
                f"shadowcolor=black@0.6:shadowx=2:shadowy=2,"
                f"drawtext=fontfile={FONT}:textfile={files['t']}:fontsize={ft}:fontcolor=#FF5FAE:x=640:y=330:"
                f"shadowcolor=black@0.6:shadowx=2:shadowy=2,"
                f"drawtext=fontfile={FONT_REG}:textfile={files['b']}:fontsize={fb}:fontcolor=white@0.85:x=640:y=400")
        if cover:
            vf = (f"[0]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,boxblur=30:3,"
                  f"eq=brightness=-0.25[bg];[1]scale=480:480[fg];[bg][fg]overlay=110:120,{text}")
            inputs = ["-i", cover, "-i", cover]
        else:
            vf = f"[0]format=rgb24,{text}"
            inputs = ["-f", "lavfi", "-i", "color=c=#141414:s=1280x720"]
        subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", vf, "-frames:v", "1", card],
                       check=True, capture_output=True)
        tmp_out = f"{tmp}/out.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "1", "-i", card, "-i", t["file"],
                        "-map", "0:v", "-map", "1:a:0", "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast",
                        "-crf", "26", "-pix_fmt", "yuv420p", "-r", "1", "-g", "10",
                        "-c:a", "aac", "-b:a", "224k", "-ac", "2", "-shortest",
                        "-movflags", "+faststart", "-metadata", f"title={t['artist']} - {t['title']}", tmp_out],
                       check=True, capture_output=True)
        subprocess.run(["cp", tmp_out, dest + ".part"], check=True)
        os.rename(dest + ".part", dest)
    return "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    os.makedirs(CACHE, exist_ok=True); os.makedirs(OUT, exist_ok=True)
    todo = tracks()[: a.limit]
    print(f"{len(todo)} tracks", flush=True)
    done = {"ok": 0, "skip": 0, "fail": 0}
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for t, f in [(t, ex.submit(render, t)) for t in todo]:
            try:
                done[f.result()] += 1
            except Exception as e:
                done["fail"] += 1
                print("FAIL", t["artist"], "-", t["title"], getattr(e, "stderr", b"")[-300:], flush=True)
            n = sum(done.values())
            if n % 50 == 0:
                print(n, done, flush=True)
    print("done", done, flush=True)


if __name__ == "__main__":
    sys.exit(main())
