import json, sys, collections, glob
S = sys.argv[1]
for f in sorted(glob.glob(f"{S}/ch[0-9].json")):
    d = json.load(open(f)); progs, lineup = d["programs"], d["lineup"]
    tot = sum(x["duration"] for x in lineup)
    shows = collections.Counter(); n = collections.Counter(); seq = []
    fill = collections.Counter()
    for x in lineup:
        if x["type"] == "filler":
            fill[x.get("fillerListId") or x.get("id")] += 1
        if x["type"] != "content": continue
        p = progs.get(x["id"], {}); p = p.get("program") or p
        k = p["show"]["title"] if p.get("type") == "episode" else "[movies]"
        shows[k] += x["duration"]; n[k] += 1; seq.append(k)
    runs = sum(1 for a, b in zip(seq, seq[1:]) if a == b)
    print(f"\n=== {d['number']} {d['name']}  loop {tot/864e5:.1f}d  content items {len(seq)}  shows {len(shows)}  same-show-back-to-back {runs/len(seq)*100:.0f}%  filler slots {sum(fill.values())}")
    for k, v in shows.most_common(60):
        print(f"   {v/tot*100:5.1f}%  {n[k]:5d}  {k}")
    if len(shows) > 60: print(f"   ... +{len(shows)-60} more")
