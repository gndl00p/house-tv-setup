#!/usr/bin/env python3
"""Render a channel's guide tile (640x640, word centered, number in corner) and
on-screen badge (300px tall, number + name, trimmed to text) in the house style.

usage: art.py <number> <slug> "<TILE WORD(S), | for line break>" "<BADGE NAME>" <bg #hex> [<fg #hex>] [--upload]
"""
import json, subprocess, sys, os
from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
OUT = os.environ.get("HOUSE_TV_ART", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "art"))
TUNARR = os.environ.get("TUNARR_URL", "http://127.0.0.1:8000")


def fit(draw, text, size, max_w):
    while size > 20:
        f = ImageFont.truetype(FONT, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 4
    return ImageFont.truetype(FONT, size)


def tile(num, slug, word, bg, fg):
    im = Image.new("RGB", (640, 640), bg); d = ImageDraw.Draw(im)
    lines = word.split("|")
    fonts = [fit(d, l, 168 if len(lines) == 1 else 118, 560) for l in lines]
    size = min(f.size for f in fonts); f = ImageFont.truetype(FONT, size)
    h = sum(f.getbbox(l)[3] - f.getbbox(l)[1] for l in lines) + 20 * (len(lines) - 1)
    y = (640 - h) // 2 + 30
    for l in lines:
        bb = f.getbbox(l); w = bb[2] - bb[0]
        d.text(((640 - w) // 2 - bb[0], y - bb[1]), l, font=f, fill=fg)
        y += bb[3] - bb[1] + 20
    d.text((28, 16), str(num), font=ImageFont.truetype(FONT, 76), fill=fg)
    path = f"{OUT}/tiles/ch-{num}-{slug}-v2.png"; im.save(path); return path


def badge(num, slug, name, bg, fg):
    nf, tf = ImageFont.truetype(FONT, 150), ImageFont.truetype(FONT, 128 if len(name) <= 8 else 104)
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    name_x = max(210, 36 + int(tmp.textlength(str(num), font=nf)) + 54)
    w = name_x + int(tmp.textlength(name, font=tf)) + 60
    im = Image.new("RGB", (w, 300), bg); d = ImageDraw.Draw(im)
    for text, font, x in ((str(num), nf, 36), (name, tf, name_x)):
        bb = font.getbbox(text)
        d.text((x, (300 - (bb[3] - bb[1])) // 2 - bb[1]), text, font=font, fill=fg)
    path = f"{OUT}/badges/bug-{num}-{slug}-v3.png"; im.save(path); return path


def upload(path):
    out = subprocess.run(["curl", "-s", "-m", "20", "-F", f"file=@{path}", f"{TUNARR}/api/upload/image"],
                         capture_output=True, text=True).stdout
    return json.loads(out)["fileUrl"]


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if x != "--upload"]
    num, slug, word, name, bg = args[:5]; fg = args[5] if len(args) > 5 else "#ffffff"
    paths = [tile(int(num), slug, word, bg, fg), badge(int(num), slug, name, bg, fg)]
    for p in paths:
        print(upload(p) if "--upload" in sys.argv else p)
