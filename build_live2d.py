
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import build_physics

HERE = Path(__file__).parent
EXTRACTED = HERE / "extracted"

BLEND = {0: "Overwrite", 1: "Add", 2: "Multiply"}

MOC3_MAGIC = b"MOC3"


def moc_bytes(model_dir: Path) -> bytes | None:
    """Find the CubismMoc dump in a live2d_mdl bundle and return its raw moc3."""
    for f in model_dir.glob("*.json"):
        if f.stat().st_size < 100_000:
            continue
        obj = json.loads(f.read_text())
        raw = obj.get("_bytes")
        if raw and bytes(raw[:4]) == MOC3_MAGIC:
            return bytes(raw)
    return None


def expression(exp_dir: Path) -> tuple[str, dict] | None:
    """Convert an extracted expression MonoBehaviour into .exp3.json content."""
    src = next(exp_dir.glob("*.json"), None)
    if src is None:
        return None
    obj = json.loads(src.read_text())
    if "Parameters" not in obj:
        return None
    # live2d_exp_smile-01_00018_000 -> smile-01
    m = re.match(r"live2d_exp_(.+?)_\d{5}_", exp_dir.name)
    name = m.group(1) if m else exp_dir.name
    return name, {
        "Type": "Live2D Expression",
        "FadeInTime": obj.get("FadeInTime", 0.5),
        "FadeOutTime": obj.get("FadeOutTime", 0.5),
        "Parameters": [
            {
                "Id": p["Id"],
                "Value": p["Value"],
                "Blend": BLEND.get(p.get("Blend", 0), "Add"),
            }
            for p in obj["Parameters"]
        ],
    }


def build(cid: str, outroot: Path) -> None:
    models = sorted(EXTRACTED.glob(f"live2d_mdl_{cid}-*"))
    if not models:
        sys.exit(f"no extracted live2d_mdl bundles for {cid} - run pull.py first")

    exps = [e for d in sorted(EXTRACTED.glob(f"live2d_exp_*_{cid}_*")) if (e := expression(d))]

    for model_dir in models:
        outfit = model_dir.name.replace("live2d_mdl_", "")
        out = outroot / outfit
        (out / "expressions").mkdir(parents=True, exist_ok=True)

        moc = moc_bytes(model_dir)
        if moc is None:
            print(f"  {outfit}: no moc3 found, skipped")
            continue
        (out / f"{outfit}.moc3").write_bytes(moc)

        textures = []
        for i, png in enumerate(sorted(model_dir.glob("*.png"))):
            dest = out / "textures" / f"texture_{i:02d}.png"
            dest.parent.mkdir(exist_ok=True)
            shutil.copyfile(png, dest)
            textures.append(f"textures/{dest.name}")

        for name, data in exps:
            (out / "expressions" / f"{name}.exp3.json").write_text(
                json.dumps(data, indent=1)
            )

        physics = build_physics.build(outfit, out)

        files = {
            "Moc": f"{outfit}.moc3",
            "Textures": textures,
            "Expressions": [
                {"Name": n, "File": f"expressions/{n}.exp3.json"} for n, _ in exps
            ],
        }
        if physics is not None:
            files["Physics"] = physics.name

        model3 = {
            "Version": 3,
            "FileReferences": files,
            "Groups": [
                {
                    "Target": "Parameter",
                    "Name": "EyeBlink",
                    "Ids": ["ParamEyeLOpen", "ParamEyeROpen"],
                },
                {
                    "Target": "Parameter",
                    "Name": "LipSync",
                    "Ids": ["ParamMouthOpenY"],
                },
            ],
        }
        (out / f"{outfit}.model3.json").write_text(json.dumps(model3, indent=1))
        print(
            f"  {outfit}: moc3 {len(moc):,}B, {len(textures)} texture(s), "
            f"{len(exps)} expression(s)"
        )


if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else "00018"
    outroot = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "live2d" / cid
    print(f"building live2d for {cid} -> {outroot}")
    build(cid, outroot)
