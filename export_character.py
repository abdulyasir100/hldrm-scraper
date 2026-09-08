"""
Usage:
    python export_character.py 00018 --out out/suisei --name "Display Name"
    python export_character.py 00018 --out out/x --outfits nrml-0004-00,uniq-0004-00
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def run(args: list[str]) -> None:
    subprocess.run([sys.executable, *args], cwd=HERE, check=True)


def readable(outfit: str) -> str:
    """00018-nrml-0004-00 -> 'nrml 0004' - a placeholder until it is renamed.
    """
    parts = outfit.split("-")
    return " ".join(parts[1:3]) if len(parts) > 2 else outfit


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("character_id", help="5-digit id, e.g. 00018")
    ap.add_argument("--out", type=Path, required=True, help="destination folder")
    ap.add_argument("--name", help="display name (defaults to the folder name)")
    ap.add_argument("--outfits", help="comma-separated outfit ids; default all")
    ap.add_argument("--motions", type=Path, default=HERE / "motions",
                    help="folder built by build_motions.py; skipped if absent")
    ap.add_argument("--no-build", action="store_true", help="reuse existing build output")
    args = ap.parse_args()

    cid = args.character_id
    built = HERE / "live2d" / cid

    if not args.no_build:
        print(f"[1/3] fetching + extracting live2d for {cid}")
        run(["pull.py", "--filter", f"live2d_.*_{cid}[-_]", "--fetch"])
        print("[2/3] rebuilding model files")
        run(["build_live2d.py", cid])

    if not built.is_dir():
        sys.exit(f"nothing built at {built} - drop --no-build")

    dest = args.out
    wanted = args.outfits.split(",") if args.outfits else None

    print(f"[3/3] exporting to {dest}")
    (dest / "costumes").mkdir(parents=True, exist_ok=True)
    costumes = []
    for src in sorted(built.iterdir()):
        if not src.is_dir():
            continue
        key = src.name.removeprefix(f"{cid}-")
        if wanted and key not in wanted:
            continue
        model3 = next(src.glob("*.model3.json"), None)
        if model3 is None:
            continue
        target = dest / "costumes" / key
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)
        costumes.append({
            "id": key,
            "name": readable(src.name),
            "model": f"costumes/{key}/{model3.name}",
        })

    if not costumes:
        sys.exit("no costumes matched")

    # motions are character-agnostic in this game, so one build serves everyone
    motions_rel = None
    if args.motions and (args.motions / "index.json").exists():
        target = dest / "motions"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(args.motions, target)
        motions_rel = "motions/index.json"

    voice_src = next((HERE / "extracted").glob(f"vo_react_{cid}_greet-*.wav"), None)
    if voice_src:
        (dest / "voice").mkdir(exist_ok=True)
        shutil.copyfile(voice_src, dest / "voice" / "greet.wav")

    default = next(
        (c["id"] for c in costumes if c["id"].startswith("nrml")), costumes[0]["id"]
    )
    character = {
        "id": dest.name,
        "name": args.name or dest.name,
        "defaultCostume": default,
        "costumes": costumes,
    }
    if motions_rel:
        character["motions"] = motions_rel
    if voice_src:
        character["voice"] = "voice"
    (dest / "character.json").write_text(json.dumps(character, indent=2), encoding="utf-8")

    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    print(f"\nexported {dest.name}: {len(costumes)} costume(s), {size/1e6:.1f} MB")
    if not motions_rel:
        print("  no motions - run build_motions.py first if you want them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
