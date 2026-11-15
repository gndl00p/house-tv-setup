import sys, glob, os
from PIL import Image
src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
for p in sorted(glob.glob(f"{src}/bug-*.png")):
    im = Image.open(p).convert("RGB")
    bg = im.getpixel((5, 5))
    w, h = im.size
    right = max(x for x in range(w) for y in range(0, h, 4)
                if sum(abs(a - b) for a, b in zip(im.getpixel((x, y)), bg)) > 120)
    cut = min(w, right + 60)
    im.crop((0, 0, cut, h)).save(f"{out}/{os.path.basename(p)[:-4]}-v3.png")
    print(os.path.basename(p), w, "->", cut)
