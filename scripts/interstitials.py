#!/usr/bin/env python3
"""Create the between-show filler lists that break up long commercial gaps:
  Toddler shorts  - Bluey episodes (Castle, Little Movies)
  Cartoon shorts  - Yard-safe cartoons under 13 min (Yard, Clubhouse)
  Music video breaks - music videos from the Stage library (grown-up channels only)
Prints the list ids and records them in channels.json "pools".
"""
import json, re, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build import api, ensure_custom_show  # noqa: F401  (api reused)

B = "http://127.0.0.1:8000"
TODDLER = {"Bluey (2018)"}
CARTOON = {"Tom and Jerry", "SpongeBob SquarePants", "Animaniacs", "Pinky and the Brain", "Dexter's Laboratory"}


def upsert(name, items):
    existing = {f["name"]: f["id"] for f in api(B, "/api/filler-lists")}
    body = {"name": name, "programs": items}
    if name in existing:
        api(B, f"/api/filler-lists/{existing[name]}", body, method="PUT"); return existing[name]
    api(B, "/api/filler-lists", body)
    return {f["name"]: f["id"] for f in api(B, "/api/filler-lists")}[name]


def main():
    ms = api(B, "/api/media-sources")[0]["id"]
    libs = {l["name"]: l["id"] for l in api(B, f"/api/media-sources/{ms}/libraries")}
    eps = api(B, f"/api/media-libraries/{libs['TV Shows']}/programs")
    toddler = [x for x in eps if x["program"]["show"]["title"] in TODDLER and x["duration"] <= 13 * 60000]
    cartoon = [x for x in eps if x["program"]["show"]["title"] in CARTOON and x["duration"] <= 13 * 60000]
    mv = [x for x in api(B, f"/api/media-libraries/{libs['Music Videos Concerts']}/programs")
          if 75_000 <= x["duration"] <= 9 * 60000 and not re.match(r"bonus\b", x["program"].get("title") or "", re.I)]
    cfg = json.load(open("channels.json"))
    for key, name, items in (("toddlershorts", "Toddler shorts", toddler), ("cartoonshorts", "Cartoon shorts", cartoon),
                             ("musicvideos", "Music video breaks", mv)):
        cfg["pools"][key] = upsert(name, items)
        print(f"{name}: {len(items)} items, {sum(x['duration'] for x in items)/3.6e6:.1f} h -> {cfg['pools'][key]}")
    json.dump(cfg, open("channels.json", "w"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(main())
