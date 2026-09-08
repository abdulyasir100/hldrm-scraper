from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from tqdm import tqdm

from holodori_asset_tools import catalog, crypto

HERE = Path(__file__).parent
STAGE = HERE / "staged"
OUT = HERE / "extracted"

PLAIN_MAGICS = (b"@UTF", b"AFS2", b"CRID")


def decrypt_asset(data: bytes, name: str) -> bytes:
    if data[:4] in PLAIN_MAGICS:
        return data
    return crypto.decrypt(data, name)


def resolve(cat: catalog.Catalog, names: set[str]) -> set[str]:
    """Add every dependency the requested assets need to load."""
    out = set(names)
    for group, entries in (
        ("assetbundles", cat.assetBundles),
        ("resources", cat.resources),
    ):
        for e in entries:
            if e.name in names:
                out.update(d.name for d in cat.required(e.name, group))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filter", default=".", help="regex over asset names")
    ap.add_argument("--list", action="store_true", help="only print matches")
    ap.add_argument("--fetch", action="store_true", help="download what isn't local")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--no-extract", action="store_true")
    args = ap.parse_args()

    idx_path = HERE / "local_index.json"
    if not idx_path.exists():
        sys.exit("run index_local.py first")
    idx: dict[str, str] = json.loads(idx_path.read_text())

    cat = catalog.get(HERE / "octo_list.json")
    entries = {e.name: e for e in cat.assetBundles}
    entries.update({e.name: e for e in cat.resources})

    pat = re.compile(args.filter, re.I)
    wanted = resolve(cat, {n for n in entries if pat.search(n)})
    # the game evicts and rewrites its own cache, so an indexed path can vanish
    have = {n: p for n in wanted if (p := idx.get(n)) and Path(p).exists()}
    local = sorted(have)
    remote = sorted(wanted - have.keys())

    print(f"{len(wanted)} assets match {args.filter!r}: {len(local)} local, {len(remote)} remote")
    if args.list:
        for n in sorted(wanted):
            print(f"  {'local ' if n in idx else 'remote'}  {n}")
        return 0
    if not wanted:
        return 1

    STAGE.mkdir(exist_ok=True)
    for n in local:
        (STAGE / n).write_bytes(decrypt_asset(Path(have[n]).read_bytes(), n))
    print(f"staged {len(local)} local")

    if remote and not args.fetch:
        print(f"skipping {len(remote)} not on disk (pass --fetch to download)")
    elif remote:
        client = httpx.Client(http2=True, timeout=120, follow_redirects=True)

        def grab(name: str) -> str | None:
            entry = entries[name]
            dest = STAGE / name
            if dest.exists() and dest.stat().st_size:
                return None  # already pulled by an earlier run
            for _ in range(3):
                try:
                    r = client.get(entry.url)
                    r.raise_for_status()
                    (STAGE / name).write_bytes(decrypt_asset(r.content, name))
                    return None
                except Exception as e:
                    err = e
            return f"{name}: {err}"

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            fails = [f for f in tqdm(
                pool.map(grab, remote), total=len(remote), unit="file"
            ) if f]
        print(f"fetched {len(remote) - len(fails)} remote, {len(fails)} failed")
        for f in fails[:10]:
            print("  ", f)

    if args.no_extract:
        return 0
    OUT.mkdir(exist_ok=True)
    return subprocess.call(
        [sys.executable, "-m", "holodori_asset_tools", "extract", str(STAGE), str(OUT)]
    )


if __name__ == "__main__":
    raise SystemExit(main())
