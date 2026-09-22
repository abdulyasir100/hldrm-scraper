"""Collect naming material for every Live2D outfit in the game.

Produces, under previews/:
  characters.png          every character, labelled by id
  by-character/<id>.png   one strip per character showing its outfits
  all-outfits.png         every outfit on one sheet, labelled with names
  costumes/<outfit>.png   a picture of each outfit
  naming.csv              the fill-in sheet that drives the character registry

The ids are opaque (00018-nrml-0004-00), so the point of all this is to let a
human look once and write down what each one actually is.

Some characters ship no storefront art at all - graduated members keep their
model and voice but lose their icon and thumbnails - so for those the picture
is taken from the model's own texture instead. Without that they would be
invisible here, which is precisely where you would go looking for them.
"""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from holodori_asset_tools import catalog

HERE = Path(__file__).parent
EXTRACTED = HERE / "extracted"
OUT = HERE / "previews"

COS = "img_cos_2d_thumb_"
ICON = "img_chr_icon_mini_"


def first_png(name: str) -> Path | None:
    d = EXTRACTED / name
    return next(iter(sorted(d.glob("*.png"))), None) if d.is_dir() else None


def head_tile(outfit: str) -> Image.Image | None:
    """A stand-in portrait cut from the model's own texture atlas.

    Used when the game ships no thumbnail for an outfit. The atlas is scattered
    mesh parts and unreadable as a whole, but face and hair are authored into
    the top-left block, which is enough to recognise someone by.
    """
    src = first_png(f"live2d_mdl_{outfit}")
    if src is None:
        return None  # the model was never pulled, so there is nothing to cut
    im = Image.open(src).convert("RGBA")
    return im.crop((0, 0, im.width // 2, im.height // 3))


def cjk_font(size: int) -> ImageFont.FreeTypeFont | None:
    """A font that can draw Japanese names; PIL's built-in one cannot."""
    for path in (
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return None


def label_sheet(items: list[tuple[str, Path]], cols: int, cell: int,
                font: ImageFont.FreeTypeFont | None = None, pad: int = 16) -> Image.Image:
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + pad)), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (label, png) in enumerate(items):
        im = Image.open(png).convert("RGBA")
        im.thumbnail((cell - 6, cell - 6))
        x, y = (i % cols) * cell, (i // cols) * (cell + pad)
        sheet.paste(im, (x + 3, y + 3), im)
        draw.multiline_text((x + 3, y + cell + 2), label, fill="black", font=font, spacing=2)
    return sheet


def main() -> int:
    cat = catalog.get(HERE / "octo_list.json")
    local = json.loads((HERE / "local_index.json").read_text())
    names = {e.name for e in cat.assetBundles}

    outfits = sorted(
        n.removeprefix("live2d_mdl_") for n in names if n.startswith("live2d_mdl_")
    )
    (OUT / "costumes").mkdir(parents=True, exist_ok=True)
    (OUT / "by-character").mkdir(parents=True, exist_ok=True)

    # per-outfit pictures, named by the id they belong to
    thumbs: dict[str, Path] = {}
    derived: set[str] = set()
    for outfit in outfits:
        dest = OUT / "costumes" / f"{outfit}.png"
        src = first_png(f"{COS}{outfit}")
        if src is not None:
            shutil.copyfile(src, dest)
        elif (tile := head_tile(outfit)) is not None:
            tile.save(dest)
            derived.add(outfit)
        else:
            continue
        thumbs[outfit] = dest

    # character icons, falling back to whatever picture the character does have
    chars = sorted({o.split("-")[0] for o in outfits})
    icons: dict[str, Path] = {}
    for cid in chars:
        src = first_png(f"{ICON}{cid}")
        if src is None:
            src = next((thumbs[o] for o in outfits if o.startswith(cid) and o in thumbs), None)
        if src is not None:
            icons[cid] = src

    if icons:
        sheet = label_sheet([(c, p) for c, p in sorted(icons.items())], 12, 150)
        sheet.save(OUT / "characters.png")

    # names.py recovers most of the roster from the story scripts; use whatever
    # it found so the sheets are labelled and naming.csv arrives part-filled
    known: dict[str, str] = {}
    if (src := OUT / "names.csv").exists():
        with open(src, encoding="utf-8") as f:
            known = {r["character_id"]: r["name"] for r in csv.DictReader(f) if r["name"]}

    # one strip per character, so costumes can be named in context
    for cid in chars:
        mine = [(o, thumbs[o]) for o in outfits if o.startswith(cid) and o in thumbs]
        if mine:
            label_sheet(mine, min(len(mine), 6), 230).save(
                OUT / "by-character" / f"{cid}.png"
            )

    # every outfit on one sheet, grouped by character. This is the file people
    # open first, so it is rebuilt on every run rather than drawn once by hand -
    # a hand-made one went stale and hid a whole update's worth of costumes
    font = cjk_font(15)
    everything = [
        # the built-in font draws Japanese as boxes, so fall back to bare ids
        (f"{known.get(o.split('-')[0], '') if font else ''}\n{o}".strip(), thumbs[o])
        for o in outfits if o in thumbs
    ]
    if everything:
        label_sheet(everything, 12, 170, font=font, pad=40).save(OUT / "all-outfits.png")

    # the fill-in sheet
    rows = []
    for outfit in outfits:
        cid = outfit.split("-")[0]
        bundle = f"live2d_mdl_{outfit}"
        rows.append({
            "character_id": cid,
            "outfit_id": outfit,
            "character_name": known.get(cid, ""),
            "costume_name": "",
            # "derived" means the game ships no thumbnail and the picture was
            # cut from the model texture — usually a graduated member
            "picture": ("derived" if outfit in derived
                        else "yes" if outfit in thumbs else "no"),
            "on_disk": "yes" if bundle in local else "fetch",
        })
    with open(OUT / "naming.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    gap = [o for o in outfits if o not in thumbs]
    print(f"{len(outfits)} outfits across {len(chars)} characters")
    print(f"  pictures   : {len(thumbs)}  ({len(derived)} cut from model art)")
    print(f"  icons      : {len(icons)}")
    if gap:
        # no thumbnail AND no model on disk — pull these to see who they are
        # plain ASCII: this prints to cp1252 consoles on Windows
        print(f"  no picture : {len(gap)} - pull their models, e.g.")
        print(f"      python pull.py --filter '^live2d_mdl_{gap[0]}$' --fetch")
    print(f"  wrote      : {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
