from __future__ import annotations

import json
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

# CubismPhysicsSourceComponent
COMPONENT = {0: "X", 1: "Y", 2: "Angle"}


def find_rig(bundle: Path):
    env = UnityPy.load(crypto.decrypt(bundle.read_bytes(), bundle.name))
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            d = o.read()
        except Exception:
            continue
        if "_rig" in d.__dict__:
            return d._rig
    return None


def _scale(entry, component: str) -> float:
    """physics3.json carries one Scale; which Unity field it maps to depends on type."""
    if component == "Angle":
        return entry.AngleScale
    t = entry.TranslationScale
    return t.x if component == "X" else t.y


def convert(rig) -> dict:
    settings, dictionary = [], []
    inputs = outputs = vertices = 0

    for i, sub in enumerate(rig.SubRigs):
        sid = f"PhysicsSetting{i + 1}"
        dictionary.append({"Id": sid, "Name": sub.Name})

        sub_in = []
        for e in sub.Input:
            kind = COMPONENT.get(e.SourceComponent, "X")
            sub_in.append({
                "Source": {"Target": "Parameter", "Id": e.SourceId},
                "Weight": e.Weight,
                "Type": kind,
                "Reflect": bool(e.IsInverted),
            })

        sub_out = []
        for e in sub.Output:
            kind = COMPONENT.get(e.SourceComponent, "Angle")
            sub_out.append({
                "Destination": {"Target": "Parameter", "Id": e.DestinationId},
                "VertexIndex": e.ParticleIndex,
                "Scale": _scale(e, kind),
                "Weight": e.Weight,
                "Type": kind,
                "Reflect": bool(e.IsInverted),
            })

        sub_vertices = []
        for p in sub.Particles:
            sub_vertices.append({
                "Position": {"X": p.InitialPosition.x, "Y": p.InitialPosition.y},
                "Mobility": p.Mobility,
                "Delay": p.Delay,
                "Acceleration": p.Acceleration,
                "Radius": p.Radius,
            })

        n = sub.Normalization
        settings.append({
            "Id": sid,
            "Input": sub_in,
            "Output": sub_out,
            "Vertices": sub_vertices,
            "Normalization": {
                "Position": {
                    "Minimum": n.Position.Minimum,
                    "Default": n.Position.Default,
                    "Maximum": n.Position.Maximum,
                },
                "Angle": {
                    "Minimum": n.Angle.Minimum,
                    "Default": n.Angle.Default,
                    "Maximum": n.Angle.Maximum,
                },
            },
        })
        inputs += len(sub_in)
        outputs += len(sub_out)
        vertices += len(sub_vertices)

    return {
        "Version": 3,
        "Meta": {
            "PhysicsSettingCount": len(settings),
            "TotalInputCount": inputs,
            "TotalOutputCount": outputs,
            "VertexCount": vertices,
            "EffectiveForces": {
                "Gravity": {"X": rig.Gravity.x, "Y": rig.Gravity.y},
                "Wind": {"X": rig.Wind.x, "Y": rig.Wind.y},
            },
            "PhysicsDictionary": dictionary,
            "Fps": rig.Fps,
        },
        "PhysicsSettings": settings,
    }


def build(outfit: str, out_dir: Path) -> Path | None:
    """`outfit` is the full model key, e.g. 00018-nrml-0004-00.

    Each outfit carries its own rig - alt costumes have different hair and cloth -
    so the rig must come from that outfit's own bundle, not a shared one.
    """
    bundle = STAGE / f"live2d_mdl_{outfit}"
    if not bundle.exists():
        bundle = next(STAGE.glob(f"live2d_mdl_{outfit}*"), None)
    if bundle is None or not bundle.exists():
        return None
    rig = find_rig(bundle)
    if rig is None:
        return None
    data = convert(rig)
    dest = out_dir / f"{outfit}.physics3.json"
    dest.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return dest


if __name__ == "__main__":
    outfit = sys.argv[1] if len(sys.argv) > 1 else "00018-nrml-0004-00"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE
    dest = build(outfit, out)
    if dest is None:
        sys.exit(f"no physics rig found for {outfit}")
    meta = json.loads(dest.read_text(encoding="utf-8"))["Meta"]
    print(f"wrote {dest}")
    print(f"  {meta['PhysicsSettingCount']} settings, {meta['TotalInputCount']} inputs, "
          f"{meta['TotalOutputCount']} outputs, {meta['VertexCount']} vertices")
    print(f"  gravity {meta['EffectiveForces']['Gravity']}, fps {meta['Fps']}")
