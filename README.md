# holodream-scraper

Rebuild runnable **Live2D Cubism** models — moc3, textures, expressions,
physics and motions — out of a *hololive Dreams* install.

The game ships its Cubism data baked into Unity objects rather than as the
usual on-disk files. These scripts unbake it back into the standard layout that
Cubism Viewer, the Web SDK and Unity all load directly.

> **No assets are included, and none ever should be.** Every model, texture and
> voice clip belongs to COVER Corp. This repo is only the converter; you point
> it at your own copy of the game and it produces files on your own machine.
> Whatever it produces stays yours to keep, not to redistribute.

## The easy way

Double-click **`Holodream.bat`**. It opens a window with four numbered steps —
refresh the catalogue, work out who's who, get a character (Live2D or 3D
chibi), export a ready-to-use folder — and runs the scripts below for you with
the right Python and settings. Everything after this section is what those
buttons do, for when you'd rather use the command line.

## What it produces

```
out/<character>/
    character.json       manifest: display name, costume list, default
    costumes/<outfit>/
        <outfit>.moc3
        <outfit>.model3.json
        <outfit>.physics3.json
        textures/texture_00.png
        expressions/*.exp3.json
    motions/             *.motion3.json + index.json
    voice/greet.wav

out/chibi/<outfit>.glb   the 3D park model: rigged, skinned, textured glTF
```

## Setup

Needs Python 3.10+ and the game installed via Steam.

```bash
git clone https://github.com/HolodoriDB/holodori-asset-tools
pip install -e ./holodori-asset-tools
```

That one dependency provides the decrypt and catalog layer, and pulls in
UnityPy, httpx, cryptography, Pillow, tqdm and cricodecs — everything else
these scripts need.

## Running it

```bash
# 1. map the game's octo blobs to catalog names, so already-downloaded
#    assets are reused instead of re-fetched
python index_local.py                     # or --game "D:/Games/hololiveDreams"

# 2. see what a character's ids look like before committing to a download
python pull.py --filter 'live2d_.*_00018[-_]' --list

# 3. recover the id -> name table from the story scripts
python pull.py --filter '^adv_(main|chr)_[0-9_.-]+$' --fetch --no-extract
python names.py

# 4. thumbnails + previews/naming.csv, now arriving part-filled
python pull.py --filter '^img_(chr_icon_mini|cos_2d_thumb)_' --fetch
python gather_previews.py

# 5. the shared motion library — 189 clips, character-independent, so this
#    only needs running once no matter how many characters you export
python pull.py --filter '^live2d_mot_' --fetch
python build_motions.py 00018

# 6. fetch, rebuild and package one character
python export_character.py 00018 --out out/mychar --name "Display Name"

# 3D chibi: the separate park/minigame model, as a .glb for Blender or three.js
python pull.py --filter '^mdl_chr_drs_00018-' --fetch --no-extract
python build_chibi.py 00018 nrml-0004-00
python build_chibi.py 00018 cmmn-0002-00 --hair base-0000-00   # outfit with no hair of its own
```

Step 6 runs `pull.py` and `build_live2d.py` for you; pass `--no-build` to
re-package from what you already have. `build_live2d.py` and `build_physics.py`
also run standalone if you want a single outfit.

### Finding the character you want

Ids are opaque (`00018-nrml-0004-00`) and the game ships no name table with the
models. It does, however, ship the story — and dialogue names people out loud,
while voice clips carry the speaker's id. `names.py` lines those two up and
recovers most of the roster on its own; `gather_previews.py` then writes the
contact sheets with `naming.csv` already part-filled, leaving you to check the
rows it flags and name the costumes.

Worth knowing: `--fetch` pulls from the CDN by catalog id, not by what your
account owns, so you get the full roster regardless of what you have unlocked
in-game. A partial local cache does not limit you.

## How each piece works

| Script | What it solves |
|---|---|
| `index_local.py` | Octo filenames are hex-encoded ASCII ids; decodes them and matches against the catalog |
| `pull.py` | Stages and decrypts, resolves bundle dependencies, downloads what is missing |
| `names.py` | Recovers the id → name table by pairing story dialogue with voice-clip ids |
| `gather_previews.py` | Contact sheets + `naming.csv`, part-filled from `names.py` |
| `build_live2d.py` | moc3 out of a `CubismMoc` byte array, textures, `exp3.json` from MonoBehaviours |
| `build_physics.py` | `physics3.json` out of the baked `CubismPhysicsController` rig |
| `build_motions.py` | `motion3.json` out of Unity's optimised AnimationClips |
| `export_character.py` | Collects one character into a portable folder |
| `build_chibi.py` | The 3D chibi as one skinned glTF: body + hair rigs merged, outline shells dropped, mouth decal rebuilt as a mesh |
| `holodream_ui.pyw` | The window behind `Holodream.bat` |

Two details cost real debugging time and are worth reading the comments for:

- **Expression blend modes are not in the same order in both formats.** Unity's
  `CubismParameterBlendMode` is `(Override, Additive, Multiply)`; `exp3.json`
  spells them `(Overwrite, Add, Multiply)`. Get it wrong and every expression
  loads without error and does nothing at all.
- **Motion curves are bound by CRC32 hash, not name.** Unity discards the
  strings, so the names have to be recovered by rebuilding the model's
  transform paths and re-hashing them — which is why `build_motions.py` needs a
  character id even though the motions themselves are shared.

The 3D chibi is a separate asset from the Live2D model — a chibi-proportioned
rig used in the park and minigames, with the hair on its own small skeleton
that the game attaches to the head at runtime. `build_chibi.py` merges the two
and keeps the game's bone names, so animation can be driven by name later.

Physics rigs are **per outfit**, not per character: alt costumes have different
hair and cloth, so each one's rig comes from its own bundle.

## Layout

The scripts keep their working data beside themselves, all of it gitignored:

```
staged/       decrypted bundles
extracted/    unpacked Unity objects
live2d/       rebuilt models, per character id
motions/      converted motion library
previews/     contact sheets + naming.csv
out/          finished exports
```

Nothing under those paths belongs in version control. `octo_list.json` and
`local_index.json` are likewise regenerated on first run.
