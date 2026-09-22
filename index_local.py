from __future__ import annotations

import argparse
import binascii
import collections
import json
import os
import re
from pathlib import Path

from holodori_asset_tools import catalog

HERE = Path(__file__).parent

CANDIDATES = [
    Path(p) / "steamapps/common/hololiveDreams"
    for p in (
        "C:/Program Files (x86)/Steam",
        "C:/SteamLibrary",
        "D:/SteamLibrary",
        "E:/SteamLibrary",
        "F:/SteamLibrary",
        Path.home() / ".steam/steam",
        Path.home() / ".local/share/Steam",
    )
]


def find_game(explicit: str | None) -> Path:
    for cand in ([Path(explicit)] if explicit else []) + CANDIDATES:
        if (cand / "hololive-Dreams_Data").is_dir():
            return cand
    raise SystemExit(
        "could not find the game - pass --game <path to hololiveDreams>\n"
        "(the folder containing hololive-Dreams_Data)"
    )


def refresh_catalog(path: Path):
    """Fetch the live catalogue, falling back to the cached copy when offline.

    catalog.get() returns the cached file whenever one exists and never checks
    for a newer revision, so after a game update every script silently kept
    using the old catalogue and missed everything new. This is the step you run
    after an update, so it is the one that always goes to the network.
    """
    old = None
    if path.exists():
        try:
            old = catalog.Catalog.load(path)
        except Exception:
            pass
    try:
        cat = catalog.fetch()
        cat.save(path)
    except Exception as e:
        if old is None:
            raise SystemExit(f"could not reach the catalogue and no cached copy exists: {e}")
        print(f"could not reach the catalogue ({e}) - using the cached copy")
        return old

    count = lambda c: len(c.assetBundles) + len(c.resources)
    if old is None:
        print(f"catalogue: revision {cat.revisionId}, {count(cat)} entries")
    elif old.revisionId == cat.revisionId:
        print(f"catalogue: revision {cat.revisionId}, already up to date")
    else:
        print(f"catalogue: revision {old.revisionId} -> {cat.revisionId}, "
              f"{count(cat) - count(old):+d} entries")
    return cat


def decode_name(fn: str) -> str | None:
    """Filenames are hex-encoded ASCII asset ids, e.g. 413230393730 -> A20970."""
    stem = fn.split(".")[0]
    if len(stem) % 2 or not re.fullmatch(r"[0-9a-f]+", stem):
        return None
    try:
        s = binascii.unhexlify(stem).decode("ascii")
    except Exception:
        return None
    return s if re.fullmatch(r"[AR]\d+", s) else None


def scan(game: Path):
    """Yield (kind, id, path) for every local octo blob."""
    for p in (game / "hololive-Dreams_Data" / "Octo").rglob("*"):
        if p.is_file() and (n := decode_name(p.name)):
            yield n[0], int(n[1:]), p
    # the runtime cache stores each asset as a directory holding the blob
    for d in (game / "octo" / "v1").rglob("*"):
        if d.is_dir() and (n := decode_name(d.name)):
            for f in d.iterdir():
                if f.name != ".meta":
                    yield n[0], int(n[1:]), f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default=os.environ.get("HOLODREAM_GAME"),
                    help="path to the hololiveDreams install")
    args = ap.parse_args()

    game = find_game(args.game)
    print(f"game: {game}")

    cat = refresh_catalog(HERE / "octo_list.json")
    byid = {("A", e.id): e for e in cat.assetBundles}
    byid.update({("R", e.id): e for e in cat.resources})

    found, missing = {}, 0
    for kind, i, path in scan(game):
        e = byid.get((kind, i))
        if e is None:
            missing += 1  # stale blob from an older catalog revision
            continue
        found[e.name] = str(path)

    print(f"local files matched: {len(found)}  unmatched: {missing}")
    print(f"catalog total: {len(byid)}")
    (HERE / "local_index.json").write_text(json.dumps(found, indent=1))

    for k, v in collections.Counter(n.split("_")[0] for n in found).most_common(12):
        print(f"  {k:10s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
