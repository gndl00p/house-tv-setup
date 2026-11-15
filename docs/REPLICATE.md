# Build your own

Do this on the machine that runs Plex. The weather tuner and the guide proxy only talk to Plex over localhost.

## 1. Plex

Libraries the scripts look up by name:

| Library | Type | Required |
|---|---|---|
| TV Shows | show | yes |
| Movies | movie | yes |
| Music | artist | only for the jukebox renderer |
| Jukebox | movies, or other video | the rendered song videos |
| Music Videos Concerts | movies, or other video | The Stage |

Create a DVR in Plex (any lineup is fine for now; step 6 points it at this guide). Leave the tuner count modest. Four is enough for a house, and a GPU doing NVENC runs out of encodes faster than you expect.

## 2. Tunarr

Run Tunarr in Docker next to Plex and add the Plex server as a media source. Scan `TV Shows` and `Movies`.

Create **one** channel in the Tunarr UI before the scripts create the rest. Match its number to `template_channel` in your config (the example uses 4). Set the transcode profile, the fallback slate, and the watermark on that channel. New channels are cloned from it.

Tunarr's container clock is UTC. Leave it. Daypart times in `channels.json` are local (`HOUSE_TZ`). The nightly rebuild is what keeps them lined up across daylight saving. Do not depend on Tunarr's own time-slot generator across the fall-back hour; it has stalled there. `scripts/schedule.py` writes a manual lineup instead, which is what the nightly job runs.

A watermark has to be an `http://` URL. A filesystem path is ignored.

## 3. Put the code on the box

```bash
sudo mkdir -p /opt/house-tv
sudo rsync -a ./ /opt/house-tv/ --exclude .git
cd /opt/house-tv
cp house.env.example house.env
cp channels.example.json channels.json
```

Edit both files. In `house.env`, `TUNARR_PUBLIC` is the address your TVs can reach, not `127.0.0.1`. `ZIP_CODE` is the weather channel. `HOUSE_TZ` is an IANA zone name. The Python defaults to `America/Chicago` if you skip it.

In `channels.json`, replace `Your Kids Show`, `Your Sitcom`, and `Your Movie` with titles copied from Plex. See [CHANNELS.md](CHANNELS.md).

`house.env` is gitignored. Do not commit it.

## 4. Build a lineup

From `/opt/house-tv`, against Tunarr on this machine:

```bash
python3 scripts/schedule.py --dry-run --config channels.json --tunarr http://127.0.0.1:8000
python3 scripts/schedule.py --config channels.json --tunarr http://127.0.0.1:8000
docker restart tunarr
```

Dry-run prints the plan and changes nothing. The real run skips a channel that already has an open session, and it refuses to publish a lineup shorter than a day (that usually means none of the titles matched).

Restart Tunarr after a lineup update. The API and the guide update immediately, but the process that feeds the TV keeps the previous lineup until the container restarts. Check a real stream, not only the guide.

`cursors.json` appears next to `channels.json` after the first successful build. It is how each show remembers the next episode. Do not commit it. Deleting it starts every series over.

`scripts/build.py` and `scripts/timeslots.py` are the older generators (Tunarr random slots, and a weekly clock). The nightly job does not use them. They are still useful if you want to read how a share is computed: airtime follows the square root of a show's total runtime, capped so nothing exceeds `cap` (default 20%).

## 5. Guide proxy and phone page

```bash
sudo cp service/house-tv.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now house-tv.service
```

- `http://<lan-address>:8001/` is the phone page. It refreshes once a minute.
- `http://127.0.0.1:8001/xmltv.xml` is the guide Plex should use.

The proxy fetches Tunarr's XMLTV and rewrites channel ids from `C4.1.tunarr.com` to `house4`. Plex drops a guide source whose ids contain extra dots, which is the whole reason this process exists. It also fills in category and description on music items, because Plex will not tune a program it cannot classify. The weather channel is appended here as channel 14, one hour at a time.

The rewritten guide is cached for five minutes. If Tunarr is down, the last good copy is what gets served.

## 6. Hand the guide to Plex

On the Plex host:

```bash
sudo python3 /opt/house-tv/service/plexdvr.py add-weather
sudo python3 /opt/house-tv/service/plexdvr.py switch-guide
sudo python3 /opt/house-tv/service/plexdvr.py map
sudo python3 /opt/house-tv/service/plexdvr.py reload
sudo python3 /opt/house-tv/service/plexdvr.py status
```

`map` sends the full channel map for every tuner device. A partial update turns the channels you left out off. Run it again after you add a Tunarr channel.

`switch-guide` points the DVR at `http://127.0.0.1:8001/xmltv.xml` and removes the previous lineup. If the server has more than one DVR, set `PLEX_DVR_ID` in `house.env`.

Refresh the guide when the lineup changes, but not on a timer that hammers Plex. The included timer reloads only when the XML actually changed:

```bash
sudo cp service/house-tv-guide.service service/house-tv-guide.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now house-tv-guide.timer
```

## 7. Weather channel

Channel 14 is [ws4kp](https://github.com/netbymatt/ws4kp) (a WeatherStar 4000 in a browser) screen-captured by ws4channels, then re-encoded by `housetv.py` to 720p H.264 4:2:0. The capture emits 4:4:4, which a Roku will not decode. Two weather tuners; a third viewer gets a short error.

```bash
# put a few mp3s in weather/music/regular first, or the channel is silent
sudo bash weather/setup.sh
```

`weather/travelcities.json` is four example cities (New York, Chicago, Denver, Los Angeles) with National Weather Service grid points. Replace it with cities you care about, then rerun `setup.sh`. The grid point is `gridX`, `gridY`, and `cwa` from `https://api.weather.gov/points/{lat},{lon}`.

The running house also injects an aviation page and an air-quality page into WeatherStar. Those patches are tied to one airport and are not in this repo. The channel works without them.

Confirm it:

```bash
curl -s http://127.0.0.1:9798/stream/stream.m3u8 | head
curl -s http://127.0.0.1:8001/lineup.json
```

Then tune channel 14 in Plex.

## 8. Nightly upkeep

```bash
sudo cp service/house-tv-nightly.service service/house-tv-nightly.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now house-tv-nightly.timer
```

At 4:15 in `HOUSE_TZ` it:

1. Renders any new songs into jukebox videos (`jukebox/render.py`) and asks Plex to analyze them
2. Waits ten minutes, then tells Tunarr to rescan
3. Assigns shows and movies that no channel claimed yet, into `assignments.json` next to the config. `channels.json` stays the file you edit
4. Pulls in movies carrying a Plex label named by a channel's `plexLabel` (the holiday channel uses `Christmas`)
5. Rebuilds TV lineups whose inputs changed, skipping any channel with a viewer
6. Reshuffles the jukebox on Sundays, or when the music library grew
7. Restarts Tunarr if anything was published, then refreshes the Plex guide
8. Switches the weather music to `weather/music/holiday` for December

Log: `/var/log/house-tv-nightly.log`.

Try it once by hand before you trust the timer:

```bash
sudo python3 /opt/house-tv/scripts/nightly.py --dry-run --no-scan
```

## 9. Commercials, station IDs, badges

Optional, and all local to your Tunarr.

Breaks are three minutes of Tunarr filler (`flex` items). With `"filler": "none"` those breaks have nothing to play, so Tunarr holds on whatever single clip it can find. When you are ready:

1. Put commercial clips in Plex and let Tunarr scan them
2. Group them into Tunarr filler collections (cartoon, preschool, prime, movie-night, or whatever you want to call them)
3. Put the collection ids in `pools` in `channels.json`
4. Set a channel's `filler` to weights, for example `{ "prime": 75, "cartoon": 25 }`

`scripts/pools.py` and `scripts/idents.py` are the tools that did this on the original server. They talk to `TUNARR_URL` (default `http://127.0.0.1:8000`); `pools.py` also needs `COMMERCIAL_LISTS` set. `ident_lists` attaches a short chime (about every 15 minutes) and a voiced station ID (about hourly) as extra filler.

Badges are PNGs uploaded to Tunarr (`scripts/badge.py`, `scripts/art.py`). A new channel looks for `http://<tunarr>/images/uploads/ch-<number>-<slug>-v4.png`.

## 10. Jukebox and Stage

Tunarr will not schedule an audio-only music track. `jukebox/render.py` (root, on the Plex host) writes a 720p video per track, album art plus the song title, to `JUKEBOX_OUT` (default `/mnt/media/Plex/Jukebox`). Point a Plex library named `Jukebox` at that folder.

`scripts/jukebox.py` builds channel 13 from that library and channel 15 from `Music Videos Concerts`, including one concert a night around 8 PM. The nightly job runs it on Sundays. You can run it yourself:

```bash
python3 scripts/jukebox.py --tunarr http://127.0.0.1:8000
docker restart tunarr
```

## Things that already went wrong once

- Verifying only the API. Pull a few seconds of the channel's `.ts` from Tunarr's `lineup.json` and look at a frame.
- Deleting Tunarr sessions. That leaks the ffmpeg process. Let them end.
- Editing the shared Default transcode config from a script. Loudness processing lives there; a casual PUT wipes it.
- Scheduling a file Tunarr imported before Plex had finished analyzing it. Duration comes back empty and the item is skipped. Analyze, wait, rescan.
- Publishing `channels.json` from a real house. It is a catalog of the library. This template ships placeholders for that reason.
