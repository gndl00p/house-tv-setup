#!/usr/bin/env python3
"""House TV art v2: icon guide tiles, pill badges, and 12 s animated station IDs (sunburst, icon pop,
name slide, per-channel jingle). usage: python art2.py [--only N,..] [--no-ids]
Outputs to art/v4/: ch-N-slug-v4.png (tile), bug-N-slug-v4.png (badge), id-N-slug-{chime,voice}-v4.mp4
"""
import argparse, math, os, subprocess, sys, tempfile
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.environ.get("HOUSE_TV_ART", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "art"))
OUT = f"{ROOT}/v4"
BUNGEE = f"{ROOT}/src/fonts/Bungee-Regular.ttf"
FPS, SECS = 30, 12
CHANNELS = {  # number: (slug, name, spoken, color, accent, emoji, jingle root Hz)
    2: ("yard", "THE YARD", "The Yard", "#1f8a45", "#ffe14d", "1f6dd", 392),
    3: ("castle", "THE CASTLE", "The Castle", "#8a46b0", "#ffd1f5", "1f3f0", 523),
    4: ("couch", "THE COUCH", "The Couch", "#d9692e", "#ffe2b8", "1f6cb", 349),
    5: ("shop", "THE SHOP", "The Shop", "#1f5b96", "#ffcc33", "1f527", 294),
    6: ("family", "FAMILY ROOM", "The Family Room", "#0f7f80", "#fff2a8", "1f37f", 440),
    7: ("little", "LITTLE MOVIES", "Little Movies", "#d6455a", "#fff0f3", "1f9f8", 587),
    8: ("late", "LATE MOVIE", "The Late Movie", "#1b1b2e", "#e6c36a", "1f319", 262),
    9: ("hearth", "THE HEARTH", "The Hearth", "#9e3326", "#ffcf7a", "1f525", 330),
    10: ("bridge", "THE BRIDGE", "The Bridge", "#0d2f6b", "#e6c36a", "1f596", 220),
    11: ("den", "THE DEN", "The Den", "#4a3220", "#ead7b0", "1f943", 196),
    12: ("clubhouse", "CLUBHOUSE", "The Clubhouse", "#b0358c", "#b8ff5c", "1f6f9", 466),
    13: ("jukebox", "JUKEBOX", "The Jukebox", "#1a1a1a", "#ff5fae", "1f4fb", 415),
    14: ("weather", "WEATHER", "The Weather", "#1f4fb0", "#ffffff", "26c5", 370),
    15: ("stage", "THE STAGE", "The Stage", "#c79a00", "#ffffff", "1f3b8", 311),
    16: ("northpole", "NORTH POLE", "The North Pole", "#1d5c3a", "#ffd76a", "1f384", 392),
}


def rgb(h):
    h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def background(w, h, color, rot=0.0, rays=True):
    """Radial glow + retro sunburst rays."""
    base = rgb(color)
    light, dark = mix(base, (255, 255, 255), 0.35), mix(base, (0, 0, 0), 0.45)
    im = Image.new("RGB", (w, h), dark)
    d = ImageDraw.Draw(im)
    cx, cy, R = w / 2, h * 0.45, math.hypot(w, h) * 0.6
    if rays:
        n = 18
        for i in range(n):
            a0 = rot + i * 2 * math.pi / n
            a1 = a0 + math.pi / n
            pts = [(cx, cy), (cx + R * math.cos(a0), cy + R * math.sin(a0)), (cx + R * math.cos(a1), cy + R * math.sin(a1))]
            d.polygon(pts, fill=mix(base, dark, 0.35))
    glow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(glow).ellipse((cx - w * 0.45, cy - h * 0.45, cx + w * 0.45, cy + h * 0.45), fill=255)
    glow = glow.filter(ImageFilter.GaussianBlur(min(w, h) * 0.18))
    im = Image.composite(Image.new("RGB", (w, h), light), im, glow)
    return im


def text_outline(d, xy, text, font, fill, outline, width):
    d.text(xy, text, font=font, fill=fill, stroke_width=width, stroke_fill=outline)


def fit_font(text, max_w, start):
    size = start
    while size > 20:
        f = ImageFont.truetype(BUNGEE, size)
        if f.getlength(text) <= max_w:
            return f
        size -= 4
    return ImageFont.truetype(BUNGEE, size)


def icon(emoji, size, shadow=True):
    ic = Image.open(f"{ROOT}/src/emoji/{emoji}.png").convert("RGBA").resize((size, size), Image.LANCZOS)
    if not shadow:
        return ic
    sh = Image.new("RGBA", (size + 40, size + 40), (0, 0, 0, 0))
    alpha = ic.split()[3].point(lambda a: int(a * 0.55))
    sh.paste((0, 0, 0, 255), (24, 28), alpha)
    sh = sh.filter(ImageFilter.GaussianBlur(10))
    sh.alpha_composite(ic, (20, 20))
    return sh


def tile(n):
    slug, name, _, color, accent, emoji, _ = CHANNELS[n]
    im = background(640, 640, color).convert("RGBA")
    ic = icon(emoji, 330)
    im.alpha_composite(ic, ((640 - ic.width) // 2, 40))
    d = ImageDraw.Draw(im)
    f = fit_font(name, 580, 92)
    w = f.getlength(name)
    text_outline(d, ((640 - w) / 2, 448), name, f, "#ffffff", "#111111", 7)
    # number roundel
    d.ellipse((22, 22, 132, 132), fill=accent, outline="#111111", width=6)
    nf = ImageFont.truetype(BUNGEE, 64 if n < 10 else 52)
    nw = nf.getlength(str(n))
    d.text((77 - nw / 2, 36), str(n), font=nf, fill="#111111")
    path = f"{OUT}/ch-{n}-{slug}-v4.png"; im.convert("RGB").save(path); return path


def badge(n):
    slug, name, _, color, accent, emoji, _ = CHANNELS[n]
    h = 300
    nf, tf = ImageFont.truetype(BUNGEE, 150), fit_font(name, 2000, 120)
    text_w = 300 + int(nf.getlength(str(n))) + 50 + int(tf.getlength(name)) + 70
    bg = background(text_w, h, color, rays=False).convert("RGBA")
    mask = Image.new("L", (text_w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, text_w - 1, h - 1), radius=h // 2, fill=255)
    im = Image.new("RGBA", (text_w, h), (0, 0, 0, 0)); im.paste(bg, (0, 0), mask)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((6, 6, text_w - 7, h - 7), radius=h // 2 - 6, outline=accent, width=10)
    ic = icon(emoji, 230, shadow=False)
    im.alpha_composite(ic, (40, (h - 230) // 2))
    x = 300
    for text, font, fill in ((str(n), nf, accent), (name, tf, "#ffffff")):
        bb = font.getbbox(text)
        text_outline(d, (x, (h - (bb[3] - bb[1])) // 2 - bb[1]), text, font, fill, "#111111", 6)
        x += int(font.getlength(text)) + 50
    path = f"{OUT}/bug-{n}-{slug}-v4.png"; im.save(path); return path


def ease_out_back(t):
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def ident_frames(n, out_raw):
    slug, name, _, color, accent, emoji, _ = CHANNELS[n]
    W, H = 1280, 720
    big = Image.open(f"{ROOT}/src/emoji/{emoji}.png").convert("RGBA").resize((380, 380), Image.LANCZOS)
    small_f, name_f = ImageFont.truetype(BUNGEE, 44), fit_font(name, 1100, 120)
    with open(out_raw, "wb") as fh:
        for i in range(FPS * SECS):
            t = i / FPS
            im = background(W, H, color, rot=t * 0.25).convert("RGBA")
            # icon pop 0.3-1.3 s with overshoot, then gentle bob
            p = min(1, max(0, (t - 0.3) / 1.0))
            s = max(0.01, ease_out_back(p)) if p < 1 else 1 + 0.03 * math.sin((t - 1.3) * 2.2)
            sz = max(2, int(380 * s))
            ic = big.resize((sz, sz), Image.LANCZOS)
            im.alpha_composite(ic, ((W - sz) // 2, int(235 - sz / 2)))
            d = ImageDraw.Draw(im)
            # "YOU'RE WATCHING" fades in 1.4 s, name slides up 1.8-2.6 s
            a = min(1, max(0, (t - 1.4) / 0.5))
            if a > 0:
                txt = "YOU'RE WATCHING"; w = small_f.getlength(txt)
                text_outline(d, ((W - w) / 2, 448), txt, small_f, mix(rgb(accent), (255, 255, 255), 0.3) + (int(255 * a),), (17, 17, 17, int(255 * a)), 4)
            q = min(1, max(0, (t - 1.8) / 0.8))
            if q > 0:
                y = 620 - 100 * (1 - (1 - q) ** 3)          # slides up to y=520, below "YOU'RE WATCHING"
                w = name_f.getlength(name)
                text_outline(d, ((W - w) / 2, y), name, name_f, (255, 255, 255, int(255 * q)), (17, 17, 17, int(255 * q)), 8)
            # channel number roundel
            d.ellipse((40, 36, 170, 166), fill=accent, outline="#111111", width=6)
            nf = ImageFont.truetype(BUNGEE, 76 if n < 10 else 60); nw = nf.getlength(str(n))
            d.text((105 - nw / 2, 52), str(n), font=nf, fill="#111111")
            # fade in/out
            fade = min(1, t / 0.4, (SECS - t) / 0.6)
            frame = im.convert("RGB")
            if fade < 1:
                frame = Image.blend(Image.new("RGB", (W, H), (0, 0, 0)), frame, max(0, fade))
            fh.write(frame.tobytes())


def jingle(root, path):
    """Bright 6-note arpeggio + held chord, unique key per channel."""
    notes = [1, 5 / 4, 3 / 2, 2, 3 / 2, 2]
    parts, delays = [], []
    for k, r in enumerate(notes):
        f = root * r
        parts.append(f"sine=f={f}:d=0.9,afade=t=out:st=0.05:d=0.85,volume=0.16")
        delays.append(int(300 + k * 180))
    for r in (1, 5 / 4, 3 / 2):     # held chord
        parts.append(f"sine=f={root * r}:d=4.5,afade=t=in:d=0.3,afade=t=out:st=2.5:d=2.0,volume=0.07")
        delays.append(1500)
    graph = ";".join(f"{p},adelay={d}|{d}[n{i}]" for i, (p, d) in enumerate(zip(parts, delays)))
    graph += ";" + "".join(f"[n{i}]" for i in range(len(parts))) + f"amix=inputs={len(parts)}:normalize=0,apad,atrim=0:{SECS}[a]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-filter_complex", graph, "-map", "[a]", "-ac", "2", "-ar", "48000", path], check=True)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--only"); ap.add_argument("--no-ids", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    nums = [int(x) for x in a.only.split(",")] if a.only else sorted(CHANNELS)
    tts = None
    for n in nums:
        tile(n); badge(n)
        if a.no_ids or n in (13, 14, 15):
            print("art", n, flush=True); continue
        if tts is None:
            import soundfile as sf
            from kokoro_onnx import Kokoro
            models = os.environ.get("KOKORO_MODELS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models"))
            tts = Kokoro(f"{models}/kokoro-v1.0.onnx", f"{models}/voices-v1.0.bin")
        slug, _, spoken, *_ , root = CHANNELS[n]
        with tempfile.TemporaryDirectory() as tmp:
            raw = f"{tmp}/v.raw"; ident_frames(n, raw)
            jg = f"{tmp}/j.wav"; jingle(root, jg)
            samples, rate = tts.create(f"You're watching {spoken}.", voice="am_michael", speed=0.95, lang="en-us")
            import soundfile as sf
            vo = f"{tmp}/vo.wav"; sf.write(vo, samples, rate)
            voiced = f"{tmp}/voiced.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", jg, "-i", vo, "-filter_complex",
                            "[1]adelay=2400|2400,aresample=48000,volume=1.7[v];[0][v]amix=inputs=2:normalize=0,atrim=0:12[a]",
                            "-map", "[a]", "-ac", "2", voiced], check=True)
            for kind, audio in (("chime", jg), ("voice", voiced)):
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1280x720",
                                "-r", str(FPS), "-i", raw, "-i", audio, "-t", str(SECS), "-c:v", "libx264", "-preset", "medium",
                                "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                                f"{OUT}/id-{n}-{slug}-{kind}-v4.mp4"], check=True)
        print("art+ids", n, flush=True)


if __name__ == "__main__":
    sys.exit(main())
