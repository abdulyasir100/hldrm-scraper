
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

from holodori_asset_tools import catalog

HERE = Path(__file__).parent
EXTRACTED = HERE / "extracted"
OUT = HERE / "previews"

COS = "img_cos_2d_thumb_"
ICON = "img_chr_icon_mini_"


def first_png(name: str) -> Path | None:
    d = EXTRACTED / name
    return next(iter(sorted(d.glob("*.png"))), None) if d.is_dir() else None


def label_sheet(items: list[tuple[str, Path]], cols: int, cell: int) -> Image.Image:
    rows = (len(items) + cols - 1) // cols
    pad = 16
    sheet = Image.new("RGB", (cols * cell, rows * (cell + pad)), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (label, png) in enumerate(items):
        im = Image.open(png).convert("RGBA")
        im.thumbnail((cell - 6, cell - 6))
        x, y = (i % cols) * cell, (i // cols) * (cell + pad)
        sheet.paste(im, (x + 3, y + 3), im)
        draw.text((x + 3, y + cell + 2), label, fill="black")
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

    # per-outfit thumbnails, named by the id they belong to
    thumbs: dict[str, Path] = {}
    for outfit in outfits:
        src = first_png(f"{COS}{outfit}")
        if src is None:
            continue
        dest = OUT / "costumes" / f"{outfit}.png"
        shutil.copyfile(src, dest)
        thumbs[outfit] = dest

    # character icons
    chars = sorted({o.split("-")[0] for o in outfits})
    icons: dict[str, Path] = {}
    for cid in chars:
        src = first_png(f"{ICON}{cid}")
        if src is not None:
            icons[cid] = src

    if icons:
        sheet = label_sheet([(c, p) for c, p in sorted(icons.items())], 12, 150)
        sheet.save(OUT / "characters.png")

    # one strip per character, so costumes can be named in context
    for cid in chars:
        mine = [(o, thumbs[o]) for o in outfits if o.startswith(cid) and o in thumbs]
        if mine:
            label_sheet(mine, min(len(mine), 6), 230).save(
                OUT / "by-character" / f"{cid}.png"
            )

    # the fill-in sheet
    rows = []
    for outfit in outfits:
        cid = outfit.split("-")[0]
        bundle = f"live2d_mdl_{outfit}"
        rows.append({
            "character_id": cid,
            "outfit_id": outfit,
            "character_name": "",
            "costume_name": "",
            "has_thumbnail": "yes" if outfit in thumbs else "no",
            "on_disk": "yes" if bundle in local else "fetch",
        })
    with open(OUT / "naming.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"{len(outfits)} outfits across {len(chars)} characters")
    print(f"  thumbnails : {len(thumbs)}  ({len(outfits)-len(thumbs)} missing)")
    print(f"  icons      : {len(icons)}")
    print(f"  wrote      : {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
