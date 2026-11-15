# Scripts

Everything talks to Tunarr at `TUNARR_URL` (default `http://127.0.0.1:8000`) and reads the time zone from `HOUSE_TZ`. Run them from the install directory with `house.env` loaded (see [REPLICATE.md](REPLICATE.md#running-scripts-by-hand)).

## The ones the house actually runs

| Script | What it does |
|---|---|
| `scripts/schedule.py` | Builds every TV channel's lineup from `channels.json`. The nightly job calls this. `--dry-run` first |
| `scripts/jukebox.py` | Builds the Jukebox (13) and Stage (15) lineups. Weekly |
| `scripts/nightly.py` | The whole 4:15 AM routine. `--dry-run --no-scan` to try it safely, `--seed` to mark the current state as built |
| `service/housetv.py` | Guide proxy, fake HDHomeRun weather tuner, phone page. Runs as a service |
| `service/plexdvr.py` | Points the Plex DVR at the guide and maps channels. Root, on the Plex host |
| `service/guide_refresh.sh` | Refreshes the guide and reloads Plex only if it changed |
| `jukebox/render.py` | Turns music tracks into album-art videos so Tunarr can play them. Root, on the Plex host |

## Checking on things

All read-only.

| Script | What it tells you |
|---|---|
| `scripts/nowstate.py` | What every channel is airing this second, and how long is left |
| `scripts/guidecheck.py` | Whether the guide Plex sees matches what is actually playing |
| `scripts/review.py` | Next few hours per channel, with red flags (repeats, odd lengths, overplayed shows) |
| `scripts/audit.py` | Airtime share per show, back-to-back repeats, filler share. Exits 1 if a show is over its cap |
| `scripts/peek.py` | One channel, one weekday, one time window. `peek.py 2 5 7 11` is Saturday morning on 2 |
| `scripts/audit2.py` | Same idea as `audit.py`, against saved lineup JSON in a folder |

## Commercials and station IDs

| Script | What it does |
|---|---|
| `scripts/pools.py` | Splits your commercial clips into themed filler lists by folder name. Needs `COMMERCIAL_LISTS`. Edit `RULES` to match your folders |
| `scripts/interstitials.py` | Short-episode filler lists (toddler shorts, cartoon shorts, music videos). Edit the show sets at the top |
| `scripts/idlists.py` | Makes a filler list per station-ID clip and writes the ids into `channels.json` |

## Art

These need more than the standard library: `pip install pillow soundfile kokoro-onnx`, plus `ffmpeg`.

| Script | What it makes |
|---|---|
| `scripts/art.py` | One channel's guide tile and badge. Simple text style. `--upload` sends them to Tunarr |
| `scripts/art2.py` | Every channel's tile, badge, and 12-second animated station ID |
| `scripts/idents.py` | Simpler 5-second station IDs (chime and voiced) |
| `scripts/stage_intros.py` | "Up next" cards for the Stage's music blocks |
| `scripts/badge.py` | Sets each channel's on-screen badge in Tunarr |
| `scripts/wm.py` | Turns every badge on or off (`wm.py on` / `off`). Saves a backup JSON in the current folder |
| `scripts/trim.py` | Crops empty space off the right side of badge PNGs |

Output goes to `art/` (or `HOUSE_TV_ART`). Voice models go in `models/` (or `KOKORO_MODELS`): `kokoro-v1.0.onnx` and `voices-v1.0.bin` from the [kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases). `art2.py` also wants `art/src/fonts/Bungee-Regular.ttf` ([Google Fonts](https://fonts.google.com/specimen/Bungee)) and `art/src/emoji/<codepoint>.png` icons, for example from [Twemoji](https://github.com/jdecked/twemoji). Channel names, colors, and icons are in the `CHANNELS` table at the top of each art script.

## Old

`scripts/build.py` and `scripts/timeslots.py` are earlier generators (Tunarr random slots, weekly grid). Nightly does not use them, but `schedule.py` still imports helpers from `build.py`, so leave it. `jukebox/remux.sh` was a one-off mkv-to-mp4 fix.
