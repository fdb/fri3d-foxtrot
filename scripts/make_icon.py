# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Scale the pixel-art source in artwork/ up to the launcher icon.

The launcher wants 64x64 at the app root: com.enigmeta.foxtrot/icon_64x64.png.
The art is 16x16, so the scale is an exact 4x and NEAREST keeps every pixel a
crisp 4x4 block -- anything smoother turns the 1px outline into grey mush.

Usage: uv run scripts/make_icon.py
"""

import pathlib

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "artwork" / "foxtrot.png"
DST = ROOT / "com.enigmeta.foxtrot" / "icon_64x64.png"
SIZE = 64

img = Image.open(SRC).convert("RGBA")
if SIZE % img.width or SIZE % img.height:
    raise SystemExit(f"{SRC.name} is {img.size}: not an integer scale to {SIZE}x{SIZE}")

img.resize((SIZE, SIZE), Image.NEAREST).save(DST, optimize=True)
print(
    f"{SRC.relative_to(ROOT)} {img.size} -> {DST.relative_to(ROOT)} {SIZE}x{SIZE} NEAREST"
)
