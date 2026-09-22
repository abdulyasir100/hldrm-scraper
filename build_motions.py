"""Convert the game's Unity AnimationClips into Cubism .motion3.json files.

The clips are Unity's optimised form: curves packed into streamed / dense /
constant arrays, bound by CRC32 hashes instead of names. Streamed keys carry the
cubic polynomial coefficients for each span, so the original curve shape can be
reconstructed exactly rather than flattened to straight lines between keys.

The motions carry no character id - they drive parameters every Cubism model
exposes - so one conversion serves any scraped character. The id argument only
names the rig used to turn the hashed curve bindings back into parameter names.

Usage:
    python build_motions.py 00018 [outdir] [--fps 30] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import warnings
import zlib
from pathlib import Path

import UnityPy
from UnityPy.exceptions import UnityVersionFallbackWarning

from holodori_asset_tools import crypto
from holodori_asset_tools.entrypoint.extract import UNITY_VERSION

UnityPy.config.FALLBACK_UNITY_VERSION = UNITY_VERSION
warnings.simplefilter("ignore", UnityVersionFallbackWarning)

HERE = Path(__file__).parent
STAGE = HERE / "staged"

SEGMENT_LINEAR = 0


def load_bundle(path: Path):
    return UnityPy.load(crypto.decrypt(path.read_bytes(), path.name))


def transform_paths(cid: str) -> dict[int, str]:
    """CRC32 of each hierarchy path -> path, so hashed bindings can be named."""
    bundle = next(STAGE.glob(f"live2d_mdl_{cid}-nrml-*"), None)
    if bundle is None:
        sys.exit(
            f"no staged live2d_mdl_{cid}-nrml-* bundle.\n"
            "the motion curves are bound by hash, so one model is needed to name "
            "them:\n"
            f"  python pull.py --filter 'live2d_mdl_{cid}-nrml' --fetch"
        )
    env = load_bundle(bundle)
    transforms, names = {}, {}
    for o in env.objects:
        if o.type.name == "Transform":
            transforms[o.path_id] = o.read()
        elif o.type.name == "GameObject":
            names[o.path_id] = o.read().m_Name

    out: dict[int, str] = {}
    for tf in transforms.values():
        parts, cur, guard = [], tf, 0
        while cur is not None and guard < 64:
            guard += 1
            parts.append(names.get(cur.m_GameObject.m_PathID, ""))
            father = cur.m_Father
            if father is None or father.m_PathID == 0:
                break
            cur = transforms.get(father.m_PathID)
        parts = [p for p in reversed(parts) if p]
        if len(parts) > 1:
            path = "/".join(parts[1:])
            out[zlib.crc32(path.encode())] = path
    return out


def read_streamed(streamed) -> dict[int, list[tuple[float, tuple]]]:
    """Frames of [time, keyCount, (curveIndex, coeff[4]) * keyCount]."""
    raw = getattr(streamed, "data", None)
    if not raw:
        return {}
    blob = struct.pack(f"<{len(raw)}I", *raw) if isinstance(raw[0], int) else bytes(raw)

    curves: dict[int, list[tuple[float, tuple]]] = {}
    frames = []
    off, end = 0, len(blob)
    while off + 8 <= end:
        time, key_count = struct.unpack_from("<fi", blob, off)
        off += 8
        if key_count < 0 or off + key_count * 20 > end:
            break
        keys = []
        for _ in range(key_count):
            index, c0, c1, c2, c3 = struct.unpack_from("<i4f", blob, off)
            off += 20
            keys.append((index, (c0, c1, c2, c3)))
        frames.append((time, keys))

    for time, keys in (frames[1:-1] if len(frames) > 2 else frames):
        for index, coeff in keys:
            curves.setdefault(index, []).append((time, coeff))
    return curves


def sample_streamed(keys: list[tuple[float, tuple]], duration: float, fps: float):
    """Evaluate the per-span cubic: v(dt) = c0*dt^3 + c1*dt^2 + c2*dt + c3."""
    keys = sorted(keys, key=lambda k: k[0])
    if not keys:
        return []
    if len(keys) == 1:
        return [(0.0, keys[0][1][3]), (duration, keys[0][1][3])]

    pts: list[tuple[float, float]] = []
    step = 1.0 / fps
    for i, (t, coeff) in enumerate(keys):
        t_next = keys[i + 1][0] if i + 1 < len(keys) else duration
        span = max(t_next - t, 0.0)
        n = max(1, int(round(span * fps)))
        for k in range(n):
            dt = span * k / n
            v = ((coeff[0] * dt + coeff[1]) * dt + coeff[2]) * dt + coeff[3]
            pts.append((t + dt, v))
    last_t, last_c = keys[-1]
    pts.append((max(duration, last_t), last_c[3]))
    return pts


def read_dense(dense, offset: int) -> dict[int, list[tuple[float, float]]]:
    count = getattr(dense, "m_CurveCount", 0) or 0
    frames = getattr(dense, "m_FrameCount", 0) or 0
    if not count or not frames:
        return {}
    rate = getattr(dense, "m_SampleRate", 30.0) or 30.0
    begin = getattr(dense, "m_BeginTime", 0.0) or 0.0
    samples = dense.m_SampleArray
    out: dict[int, list[tuple[float, float]]] = {}
    for f in range(frames):
        t = begin + f / rate
        for c in range(count):
            out.setdefault(offset + c, []).append((t, samples[f * count + c]))
    return out


def simplify(points: list[tuple[float, float]], eps: float = 1e-4):
    """Drop points that lie on the line between their neighbours."""
    if len(points) < 3:
        return points
    out = [points[0]]
    for prev, cur, nxt in zip(points, points[1:], points[2:]):
        span = nxt[0] - prev[0]
        if span <= 0:
            continue
        t = (cur[0] - prev[0]) / span
        if abs(prev[1] + (nxt[1] - prev[1]) * t - cur[1]) > eps:
            out.append(cur)
    out.append(points[-1])
    return out


def fade_times(env) -> tuple[float, float]:
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            d = o.read()
        except Exception:
            continue
        if "fadeInTime" in d.__dict__:
            return float(d.fadeInTime), float(d.fadeOutTime)
    return 0.5, 0.5


def convert(path: Path, names: dict[int, str], fps: float) -> dict | None:
    env = load_bundle(path)
    clip = next((o.read() for o in env.objects if o.type.name == "AnimationClip"), None)
    if clip is None:
        return None

    inner = clip.m_MuscleClip.m_Clip.data
    duration = float(clip.m_MuscleClip.m_StopTime)

    streamed_raw = read_streamed(inner.m_StreamedClip)
    n_streamed = getattr(inner.m_StreamedClip, "curveCount", 0) or 0
    curves: dict[int, list[tuple[float, float]]] = {
        i: sample_streamed(k, duration, fps) for i, k in streamed_raw.items()
    }
    n_dense = getattr(inner.m_DenseClip, "m_CurveCount", 0) or 0
    curves.update(read_dense(inner.m_DenseClip, n_streamed))
    constant = getattr(inner.m_ConstantClip, "data", None) or []
    for i, v in enumerate(constant):
        curves[n_streamed + n_dense + i] = [(0.0, float(v)), (duration, float(v))]

    fade_in, fade_out = fade_times(env)

    out_curves, segments, points = [], 0, 0
    for slot, binding in enumerate(clip.m_ClipBindingConstant.genericBindings):
        target = names.get(binding.path)
        pts = curves.get(slot)
        if not target or not pts:
            continue
        pts = simplify(pts)
        seg: list[float] = [round(pts[0][0], 4), round(pts[0][1], 4)]
        for t, v in pts[1:]:
            seg += [SEGMENT_LINEAR, round(t, 4), round(v, 4)]
        segments += len(pts) - 1
        points += len(pts)
        out_curves.append({
            "Target": "Parameter",
            "Id": target.split("/")[-1],
            "FadeInTime": -1.0,
            "FadeOutTime": -1.0,
            "Segments": seg,
        })

    if not out_curves:
        return None

    return {
        "Version": 3,
        "Meta": {
            "Duration": round(duration, 4),
            "Fps": fps,
            "Loop": False,
            "AreBeziersRestricted": True,
            "CurveCount": len(out_curves),
            "TotalSegmentCount": segments,
            "TotalPointCount": points,
            "UserDataCount": 0,
            "TotalUserDataSize": 0,
            "FadeInTime": fade_in,
            "FadeOutTime": fade_out,
        },
        "Curves": out_curves,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("character_id",
                    help="any scraped character, e.g. 00018 - only used to name curves")
    ap.add_argument("outdir", nargs="?", type=Path, default=HERE / "motions")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--limit", type=int, help="stop after N bundles (for a quick trial)")
    args = ap.parse_args()

    fps, limit, outdir = args.fps, args.limit, args.outdir
    outdir.mkdir(parents=True, exist_ok=True)

    names = transform_paths(args.character_id)
    bundles = sorted(STAGE.glob("live2d_mot_*"))[:limit]
    if not bundles:
        sys.exit("no staged live2d_mot bundles - run pull.py --filter '^live2d_mot_' --fetch")

    index, skipped = [], []
    for b in bundles:
        data = convert(b, names, fps)
        name = b.name.replace("live2d_mot_", "")
        if data is None:
            skipped.append(name)  # stub bundles carry fade data but no AnimationClip
            continue
        (outdir / f"{name}.motion3.json").write_text(json.dumps(data), encoding="utf-8")
        index.append({
            "name": name,
            "file": f"{name}.motion3.json",
            "duration": data["Meta"]["Duration"],
            "curves": data["Meta"]["CurveCount"],
            "loop": name.endswith("_lp"),
        })

    (outdir / "index.json").write_text(
        json.dumps(sorted(index, key=lambda e: e["name"]), indent=1), encoding="utf-8"
    )
    print(f"wrote {len(index)} motion3.json + index.json to {outdir}")
    if skipped:
        print(f"  skipped (no AnimationClip): {', '.join(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
