#!/usr/bin/env python3
"""Render The Stage's block intro cards (5 s, yellow Stage card, voiced "Up next ...").
usage: ~/code/ai/claude-voice/.venv/bin/python stage_intros.py"""
import os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from idents import card, chime, MODELS, OUT
import soundfile as sf
from kokoro_onnx import Kokoro

BLOCKS = {  # slug: (card text, spoken line)
    "mj": ("MJ HOUR", "This is The Stage. Up next, the M J Hour."),
    "abba": ("ABBA GOLD", "This is The Stage. Up next, Abba Gold."),
    "alt": ("ALTERNATIVE NATION", "This is The Stage. Up next, Alternative Nation."),
    "hiphop": ("YO! HIP-HOP", "This is The Stage. Up next, an hour of hip hop."),
    "electronic": ("ELECTRONICA", "This is The Stage. Up next, Electronica."),
    "headbangers": ("HEADBANGERS", "This is The Stage. It's late. Time for Headbangers."),
    "concert": ("CONCERT NIGHT", "This is The Stage. And now, tonight's concert."),
}

tts = Kokoro(f"{MODELS}/kokoro-v1.0.onnx", f"{MODELS}/voices-v1.0.bin")
with tempfile.TemporaryDirectory() as tmp:
    bell = f"{tmp}/chime.wav"; chime(bell)
    for slug, (text, line) in BLOCKS.items():
        png = f"{tmp}/{slug}.png"; card(15, text, "#f2c200", "#111111", png)
        samples, rate = tts.create(line, voice="am_michael", speed=0.95, lang="en-us")
        vo = f"{tmp}/{slug}.wav"; sf.write(vo, samples, rate)
        dur = max(5.0, len(samples) / rate + 1.8)
        mix = f"{tmp}/{slug}-mix.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", bell, "-i", vo, "-filter_complex",
                        f"[1]adelay=900|900,aresample=48000,volume=1.6[v];[0][v]amix=inputs=2:normalize=0,apad,atrim=0:{dur}[a]",
                        "-map", "[a]", "-ac", "2", mix], check=True)
        out = f"{OUT}/stage-{slug}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-i", png, "-i", mix,
                        "-vf", f"fade=t=in:st=0:d=0.4,fade=t=out:st={dur-0.5}:d=0.5", "-t", str(dur),
                        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", out], check=True)
        print("ok", slug, f"{dur:.1f}s")
