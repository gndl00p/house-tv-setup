#!/usr/bin/env python3
"""Audit live Tunarr lineups: airtime share per show, back-to-back repeats,
half-hour start alignment, filler share. usage: audit.py [--only N,..] [--first-days D]"""
import argparse, collections, datetime, json, sys, urllib.request

B = "http://127.0.0.1:8000"
get = lambda p: json.load(urllib.request.urlopen(B + p, timeout=300))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--first-days", type=float, default=7, help="window used for the share/repeat stats")
    ap.add_argument("--cap", type=float, default=0.30, help="flag any show above this share")
    a = ap.parse_args()
    only = {int(x) for x in a.only.split(",")} if a.only else None
    bad = 0
    for c in sorted(get("/api/channels"), key=lambda c: c["number"]):
        if only and c["number"] not in only:
            continue
        d = get(f"/api/channels/{c['id']}/programming")
        progs, lineup = d["programs"], d["lineup"]
        start = c["startTime"]
        t, window = 0, a.first_days * 864e5
        share, kinds, seq, misaligned, content_n = collections.Counter(), collections.Counter(), [], 0, 0
        prev_type = "flex"
        for x in lineup:
            if t >= window:
                break
            kinds[x["type"]] += x["duration"]
            if x["type"] == "content":
                p = progs.get(x["id"], {}); p = p.get("program") or p
                k = p["show"]["title"] if p.get("type") == "episode" else f"[{p.get('type')}]"
                share[k] += x["duration"]; seq.append(k); content_n += 1
                m = datetime.datetime.fromtimestamp((start + t) / 1000)
                if prev_type != "content" and ((m.minute % 30) * 60 + m.second + 60) % 1800 > 120:
                    misaligned += 1
            t += x["duration"]; prev_type = x["type"]
        tot = sum(kinds.values())
        b2b = sum(1 for x, y in zip(seq, seq[1:]) if x == y) / max(1, len(seq) - 1)
        top = share.most_common(3)
        flag = [k for k, v in share.items() if v / tot > a.cap and not k.startswith("[")]
        bad += bool(flag)
        print(f"{c['number']:>2} {c['name']:16} loop {c['duration']/864e5:5.1f}d | first {a.first_days:g}d: "
              f"filler {kinds['filler']/tot*100 + kinds['flex']/tot*100:4.1f}% b2b {b2b*100:4.1f}% "
              f"off-clock starts {misaligned}/{content_n} | top: " +
              ", ".join(f"{k} {v/tot*100:.0f}%" for k, v in top) + (f"  OVER CAP: {flag}" if flag else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
