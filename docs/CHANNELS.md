# channels.json

Rules, not a list of episodes. `scripts/schedule.py` resolves titles against whatever Tunarr has scanned from Plex and writes a manual lineup. `scripts/nightly.py` merges `assignments.json` (auto-filed new media) on top of this file without rewriting it.

Copy `channels.example.json` to `channels.json`. Titles must match Plex character for character, including the year Tunarr appends when two shows share a name (`Bluey (2018)`).

## Top level

| Field | Meaning |
|---|---|
| `defaults` | Inherited by every channel: `cap` (max fraction of airtime, default 0.2), `cooldownMs`, `maxRepeats`, `maxDays`, `padMs`, `fillerRepeatCooldownMs`, `timeZoneOffset` |
| `template_channel` | Number of the Tunarr channel new channels are cloned from. Create it in the UI first |
| `channels` | One object per Tunarr channel |
| `movies` | `{ "<number>": ["Movie Title", ...] }` |
| `pools` | Your names for Tunarr filler-collection ids. Empty until you create commercials |
| `ident_lists` | `{ "<number>": { "chime": "<collection id>", "voice": "<collection id>" } }` |
| `seasonal` | Keyed by month number. October and December in the example. A channel opts out with its number in `skip` |
| `skip_shows`, `skip_movies` | Titles nightly must not auto-assign |

`timeZoneOffset` is minutes west of UTC, used by the older random-slot builder (`scripts/build.py`) as a fixed offset so schedule generation does not cross the daylight-saving fold. `schedule.py` uses `HOUSE_TZ` instead. 360 is US Central standard time. 300 is Central daylight time. If you only run `schedule.py`, this value does not affect the grid.

## A channel

| Field | Meaning |
|---|---|
| `number`, `name` | Guide number and display name. 14 is reserved for weather in `service/housetv.py` |
| `slug` | Used in the icon filename `ch-<number>-<slug>-v4.png` |
| `shows` | Series titles on this channel |
| `filler` | `"none"`, or a mix of pool names to weights (`{ "prime": 75 }`) |
| `movieShare` | Fraction of airtime given to this channel's movie list. `1.0` is a movie channel |
| `cap`, `cooldownMs`, `maxRepeats` | Override the defaults for this channel |
| `programCount` | `{ "Some Show": 2 }` plays two episodes in a row. `programCountAll` does that for every show |
| `padMs` | Used by the older slot builder to pad onto a half-hour clock. `schedule.py` uses a flat 3-minute break instead |
| `dayparts` | Local-time blocks. See below |
| `kind` | Omit for a normal TV channel. `settings` means another script owns the lineup (13 and 15). `holiday` uses the holiday builder (16) |
| `plexLabel` | Every Plex movie with this label is added nightly (the label `Christmas` on channel 16) |

### Dayparts

```json
{
  "days": "sat",
  "start": "07:00",
  "end": "11:00",
  "shows": ["Your Kids Show"],
  "fixed": { "07:00": "Your Kids Show" }
}
```

- `days` is `all`, a single day (`sat`), a range (`mon-fri`), or a comma list. Days are local.
- A block that crosses midnight uses `start` later than `end` (`"20:00"` to `"02:00"`).
- `fixed` pins a show at a clock time, once that day. "7:00" means the next program that starts around then, not a hard splice onto the clock.
- `locked: true` keeps nightly from appending auto-assigned shows to that block.
- `movies: true` allows the movie list inside the block, at most one movie every few hours.
- `seasonalAt` is a list of clock times (`["16:30"]`) where a Halloween or Christmas episode is forced in season.

### Holiday channels (`kind: "holiday"`)

Used for a 24/7 seasonal channel. Least-recently-aired title goes next, never the same title twice in a row. History is kept in the cursor under `_aired`.

| Field | Meaning |
|---|---|
| `ratingAfter` | `{ "PG-13": "19:00", "R": "21:00" }` |
| `lateUntil` | Grown-up titles must finish by this time the next morning |
| `movieAt` | Clock times that should start a movie (`["21:00"]`) |
| `dated` | `{ "12-24 21:00": "Die Hard" }` a specific title on a specific evening |
| `movieGapH`, `noRepeatH` | Minimum hours between movies, and before any title may repeat |
| `ratings` | Override a title's rating when Plex has it wrong |
| `episodes` | Regex. Episodes whose titles match are added alongside the movie list |

Family ratings (`G`, `PG`, `TV-Y`, `TV-Y7`, `TV-G`, `TV-PG`) may air any hour.

## Where new media goes

`assign_show` in `scripts/nightly.py`, as shipped:

- A title containing "star trek" goes to 10
- `TV-Y` to 3, `TV-Y7` to 2, `TV-G` animation to 2, `TV-PG` animation to 12
- Documentary, reality, game show, talk, or history to 5
- `TV-14` or `TV-MA` drama (and not a comedy) to 11
- Everything else to 4

`assign_movie`: `G` and the youngest TV ratings go to 7 and 6, `PG` / `TV-PG` to 6, anything else (including unrated) to 8.

Edit those two functions. They are the original house's policy, not a default you should inherit by accident. Channels that do not exist in your `channels.json` are skipped for shows. Movies still get a list entry, which does nothing until that channel exists.

Anything you assign by hand in `channels.json` wins. The nightly file is only for titles no channel claimed.
