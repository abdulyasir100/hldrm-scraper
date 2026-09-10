"""Recover the character id -> name table from the game's story scripts.

The game ships no name table - it lives server-side - which is why every id in
this toolkit is opaque and why previews/naming.csv exists to be filled in by
hand. But the story scripts name people out loud. Each line of dialogue carries
the speaker's name, and each voice clip carries the speaker's character id:

    [Voice voice=vo_react_04011_positive-normal-01-en ...]
    [Speech message=... name=ムメイ ...]

Line those two up across every script and most of the roster names itself.

Two signals, strongest first:
  episode  a per-character story episode (adv_chr_<id>_*) is dominated by that
           character speaking, so the top name in it is almost certainly them
  voice    in the main story, the name on the speech bubble that follows a
           voice clip belongs to whoever that clip's id is

Graduated members are the awkward case: their personal episodes are deleted, so
they only get the weaker signal, and one of them has no voice lines left at all
and cannot be named this way.

Usage:
    python pull.py --filter '^adv_(main|chr)_[0-9_.-]+$' --fetch --no-extract
    python names.py
"""
from __future__ import annotations

import collections
import csv
import re
import sys
import warnings
from pathlib import Path

import UnityPy
from UnityPy.exceptions import UnityVersionFallbackWarning

from holodori_asset_tools import crypto
from holodori_asset_tools.entrypoint.extract import UNITY_VERSION

UnityPy.config.FALLBACK_UNITY_VERSION = UNITY_VERSION
warnings.simplefilter("ignore", UnityVersionFallbackWarning)

HERE = Path(__file__).parent
STAGE = HERE / "staged"
OUT = HERE / "previews"

SPEECH = re.compile(r"\[Speech message=.*?name=([^\s\]]+)")
ACTOR = re.compile(r"\[Live2DActor[A-Za-z]* id=(\d{5})")
# how far after a voice clip its speech bubble may sit; a script line carries a
# few hundred characters of timeline JSON, so this spans a couple of lines
WINDOW = 3000


def script_text(path: Path) -> str:
    """The dialogue script inside a story bundle, as plain text."""
    try:
        env = UnityPy.load(crypto.decrypt(path.read_bytes(), path.name))
    except Exception:
        return ""
    for o in env.objects:
        if o.type.name == "TextAsset":
            s = o.read().m_Script
            return s if isinstance(s, str) else bytes(s).decode("utf-8", "ignore")
    return ""


def main() -> int:
    scripts = [p for p in STAGE.glob("adv_*") if "lang-" not in p.name]
    if not scripts:
        sys.exit(
            "no staged story scripts. The _lang-* bundles are translations only -\n"
            "the names live in the base scripts:\n"
            "  python pull.py --filter '^adv_(main|chr)_[0-9_.-]+$' --fetch --no-extract"
        )

    episode: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    voice: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    seen: set[str] = set()

    for path in scripts:
        text = script_text(path)
        if not text:
            continue
        seen.update(ACTOR.findall(text))

        if m := re.match(r"adv_chr_(\d{5})", path.name):
            for name in SPEECH.findall(text):
                episode[m.group(1)][name] += 1

        for hit in re.finditer(r"\[Voice voice=vo_[a-z_]*?_(\d{5})_", text):
            after = SPEECH.search(text, hit.start(), hit.start() + WINDOW)
            if after:
                voice[hit.group(1)][after.group(1)] += 1

    rows = []
    for cid in sorted(seen):
        if episode.get(cid):
            (name, n), = episode[cid].most_common(1)
            rows.append((cid, name, "episode", n, sum(episode[cid].values())))
        elif voice.get(cid):
            (name, n), = voice[cid].most_common(1)
            rows.append((cid, name, "voice", n, sum(voice[cid].values())))
        else:
            # no personal episode and no surviving voice lines: identify by eye
            rows.append((cid, "", "", 0, 0))

    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "names.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["character_id", "name", "source", "votes", "total"])
        w.writerows(rows)

    named = [r for r in rows if r[1]]
    weak = [r for r in named if r[2] == "voice"]
    print(f"{len(scripts)} story scripts, {len(seen)} characters")
    print(f"  named        : {len(named)}  ({len(weak)} from voice lines only - check these)")
    print(f"  unidentified : {len(rows) - len(named)}")
    print(f"  wrote        : {OUT / 'names.csv'}")
    for cid, name, source, n, total in rows:
        mark = "" if source == "episode" else f"   <- {source or 'no signal'}"
        print(f"    {cid}  {name or '?':<10} {n}/{total}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
