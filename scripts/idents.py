#!/usr/bin/env python3
"""Render station-ID clips per channel: a 5s card in the channel color with a three-note
chime (quiet version) and the same card with a voiced "You're watching ..." line.

usage: python idents.py   (needs pillow, soundfile, kokoro-onnx; KOKORO_MODELS=<dir with kokoro-v1.0.onnx + voices-v1.0.bin>) [--voice am_michael]
Output: art/idents/id-<N>-<slug>-chime.mp4 and id-<N>-<slug>-voice.mp4
"""
import argparse, os, subprocess, sys, tempfile
from PIL import Image, ImageDraw, ImageFont
import soundfile as sf
from kokoro_onnx import Kokoro

MODELS = os.environ.get("KOKORO_MODELS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models"))
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
OUT = os.path.join(os.environ.get("HOUSE_TV_ART", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "art")), "idents")
CHANNELS = [  # number, slug, on-card name, spoken name, bg, fg
    (2, "yard", "THE YARD", "The Yard", "#1f6a3a", "#ffffff"),
    (3, "castle", "THE CASTLE", "The Castle", "#793c8a", "#ffffff"),
    (4, "couch", "THE COUCH", "The Couch", "#c4602c", "#ffffff"),
    (5, "shop", "THE SHOP", "The Shop", "#1d4c7a", "#ffffff"),
    (6, "family", "THE FAMILY ROOM", "The Family Room", "#0d6a6b", "#ffffff"),
    (7, "little", "LITTLE MOVIES", "Little Movies", "#c43a4d", "#ffffff"),
    (8, "late", "THE LATE MOVIE", "The Late Movie", "#191919", "#E6C36A"),
    (9, "hearth", "THE HEARTH", "The Hearth", "#8b2d2d", "#ffffff"),
    (10, "bridge", "THE BRIDGE", "The Bridge", "#0b2a5b", "#E6C36A"),
    (11, "den", "THE DEN", "The Den", "#3b2a1a", "#EAD7B0"),
    (12, "clubhouse", "THE CLUBHOUSE", "The Clubhouse", "#9a2f7a", "#ffffff"),
]


def card(num, name, bg, fg, path):
    im = Image.new("RGB", (1280, 720), bg); d = ImageDraw.Draw(im)
    small = ImageFont.truetype(FONT, 46)
    size = 150
    while d.textlength(name, font=ImageFont.truetype(FONT, size)) > 1100:
        size -= 6
    big = ImageFont.truetype(FONT, size)
    w = d.textlength("YOU'RE WATCHING", font=small); d.text(((1280 - w) / 2, 220), "YOU'RE WATCHING", font=small, fill=fg)
    w = d.textlength(name, font=big); d.text(((1280 - w) / 2, 300), name, font=big, fill=fg)
    d.text((60, 40), str(num), font=ImageFont.truetype(FONT, 110), fill=fg)
    d.rectangle((440, 300 + size + 50, 840, 300 + size + 58), fill=fg)
    im.save(path)


def chime(path):
    """Three soft descending notes (G5, E5, C5), like an old network chime."""
    notes = [(784, 0.0), (659, 0.35), (523, 0.7)]
    srcs = [f"sine=f={f}:d=1.6,afade=t=out:st=0.2:d=1.4,volume=0.18,adelay={int(t*1000)}|{int(t*1000)}" for f, t in notes]
    graph = ";".join(f"{s}[n{i}]" for i, s in enumerate(srcs)) + ";" + "".join(f"[n{i}]" for i in range(3)) + \
        "amix=inputs=3:normalize=0,apad=whole_dur=5,atrim=0:5[a]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-filter_complex", graph, "-map", "[a]", "-ac", "2", "-ar", "48000", path], check=True)


def clip(png, audio, out):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-i", png, "-i", audio,
                    "-vf", "fade=t=in:st=0:d=0.4,fade=t=out:st=4.5:d=0.5", "-af", "afade=t=out:st=4.5:d=0.5",
                    "-t", "5", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", out], check=True)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--voice", default="am_michael"); a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    tts = Kokoro(f"{MODELS}/kokoro-v1.0.onnx", f"{MODELS}/voices-v1.0.bin")
    with tempfile.TemporaryDirectory() as tmp:
        bell = f"{tmp}/chime.wav"; chime(bell)
        for num, slug, name, spoken, bg, fg in CHANNELS:
            png = f"{tmp}/{num}.png"; card(num, name, bg, fg, png)
            clip(png, bell, f"{OUT}/id-{num}-{slug}-chime.mp4")
            samples, rate = tts.create(f"You're watching {spoken}.", voice=a.voice, speed=0.95, lang="en-us")
            vo = f"{tmp}/{num}-vo.wav"; sf.write(vo, samples, rate)
            mix = f"{tmp}/{num}-mix.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", bell, "-i", vo, "-filter_complex",
                            "[1]adelay=1300|1300,aresample=48000,volume=1.6[v];[0][v]amix=inputs=2:normalize=0,atrim=0:5[a]",
                            "-map", "[a]", "-ac", "2", mix], check=True)
            clip(png, mix, f"{OUT}/id-{num}-{slug}-voice.mp4")
            print("ok", num, slug, flush=True)


if __name__ == "__main__":
    sys.exit(main())
