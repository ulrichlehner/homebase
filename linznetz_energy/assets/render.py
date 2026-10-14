#!/usr/bin/env python3
"""Renders the add-on images from their SVG sources.

  icon.svg -> ../icon.png (128 x 128, store tile)
  logo.svg -> ../logo.png (250 x 100, top of the add-on page)

`--check` renders into memory and compares with the committed PNGs instead: it fails when
a PNG does not match its SVG (pixel comparison with a small tolerance, because renderers
on different machines differ in the last bit of anti-aliasing).

Runs in the container of ./Dockerfile: `./homebase assets` (or `./homebase assets --check`).
"""

import argparse
import io
import sys
from pathlib import Path

import cairosvg
from PIL import Image, ImageChops, ImageStat

HERE = Path(__file__).resolve().parent
TARGETS = {'icon.svg': ('icon.png', 128, 128), 'logo.svg': ('logo.png', 250, 100)}
MAX_MEAN_DIFF = 1.0   # mean absolute difference per channel (0..255)


def render(svg: str, width: int, height: int) -> bytes:
    # unsafe: allows logo.svg to embed icon.svg (only our own files in this folder are rendered)
    return cairosvg.svg2png(url=str(HERE / svg), output_width=width, output_height=height, unsafe=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true', help='compare instead of writing')
    args = parser.parse_args()
    failed = 0
    for svg, (png, w, h) in TARGETS.items():
        data = render(svg, w, h)
        target = HERE.parent / png
        if not args.check:
            Image.open(io.BytesIO(data)).save(target, optimize=True)
            print(f'{svg} -> {target.name} ({w} x {h})')
            continue
        if not target.exists():
            print(f'{target.name} is missing, run `./homebase assets`')
            failed += 1
            continue
        new = Image.open(io.BytesIO(data)).convert('RGBA')
        old = Image.open(target).convert('RGBA')
        if new.size != old.size:
            print(f'{target.name}: size {old.size}, expected {new.size}')
            failed += 1
            continue
        diff = max(ImageStat.Stat(ImageChops.difference(new, old)).mean)
        ok = diff <= MAX_MEAN_DIFF
        print(f'{target.name}: mean difference {diff:.3f} {"ok" if ok else "TOO HIGH, run `./homebase assets`"}')
        failed += not ok
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
