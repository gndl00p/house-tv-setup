#!/usr/bin/env python3
"""Human-eye schedule review: what airs next on each channel, plus automatic red flags.
usage: review.py [--hours 8] [--days 3] [--only N,..]"""
import os
import argparse, collections, datetime, json, time, urllib.request
from zoneinfo import ZoneInfo

B = "http://127.0.0.1:8000"
CT = ZoneInfo(os.environ.get("HOUSE_TZ", "America/Chicago"))
get = lambda p: json.load(urllib.request.urlopen(B + p, timeout=300))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=8); ap.add_argument("--days", type=float, default=3)
    ap.add_argument("--only"); ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    only = {int(x) for x in a.only.split(",")} if a.only else None
    now = time.time() * 1000
    for c in sorted(get("/api/channels"), key=lambda c: c["number"]):
        if (only and c["number"] not in only) or c["number"] in (13, 15):
            continue
        d = get(f"/api/channels/{c['id']}/programming")
        progs, lineup = d["programs"], d["lineup"]
        pos = (now - c["startTime"]) % c["duration"]
        # rotate the lineup so it starts at "now"
        t, start_i = 0, 0
        for i, x in enumerate(lineup):
            if t + x["duration"] > pos:
                start_i = i; break
            t += x["duration"]
        seq = lineup[start_i:] + lineup[:start_i]
        clock = now - (pos - t)
        rows, flags = [], []
        seen_ep, last_show, show_times = collections.Counter(), None, collections.defaultdict(list)
        gaps = []
        for x in seq:
            if clock > now + a.days * 864e5:
                break
            when = datetime.datetime.fromtimestamp(clock / 1000, CT)
            if x["type"] == "content":
                p = progs.get(x["id"], {}); p = p.get("program") or p
                show = (p.get("show") or {}).get("title") or ("[movie] " + (p.get("title") or "?"))
                ep = (p.get("season") or {}).get("index"), p.get("episodeNumber")
                label = f"{show}" + (f" S{ep[0]}E{ep[1]} {p.get('title')}" if p.get("type") == "episode" else "")
                seen_ep[x["id"]] += 1
                if seen_ep[x["id"]] == 2:
                    flags.append(f"same item twice within {a.days:g}d: {label}")
                show_times[show].append(clock)
                if x["duration"] < 60_000:
                    flags.append(f"very short item {x['duration']/1000:.0f}s: {label} at {when:%a %H:%M}")
                if x["duration"] > 4 * 3.6e6:
                    flags.append(f"very long item {x['duration']/3.6e6:.1f}h: {label} at {when:%a %H:%M}")
                if clock < now + a.hours * 3.6e6:
                    rows.append(f"   {when:%a %I:%M %p}  {x['duration']/60000:5.1f}m  {label}")
            else:
                gaps.append(x["duration"])
            clock += x["duration"]
        for show, ts in show_times.items():
            per_day = len(ts) / a.days
            if per_day > 12 and not show.startswith("[movie]"):
                flags.append(f"{show} airs {per_day:.0f}x/day")
        print(f"\n== {c['number']} {c['name']}  (breaks: {min(gaps)/60000 if gaps else 0:.1f}-{max(gaps)/60000 if gaps else 0:.1f} min, "
              f"filler lists {len(get('/api/channels/' + c['id']).get('fillerCollections', []))})")
        if not a.quiet:
            print("\n".join(rows))
        for f in flags[:8]:
            print("   FLAG:", f)
        if len(flags) > 8:
            print(f"   ... {len(flags)-8} more flags")


if __name__ == "__main__":
    main()
