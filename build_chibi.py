"""Export a 3D chibi (SD) character from *hololive Dreams* as one skinned glTF binary.

    python pull.py --filter "mdl_chr_drs_00018-nrml-0004-00" --no-extract      # stage the bundles
    python build_chibi.py 00018 nrml-0004-00 --out out/chibi

A chibi ships as two bundles with two rigs: `<id>-<outfit>_body` (body, face, eyes on the main
skeleton) and `<id>-<outfit>_hair` (its own small rig rooted at `jnt_C_headHair00_00`, which the
game parents onto the body's `jnt_C_head00_00`). This merges them into one node tree, keeps the
highest LOD, drops the outline passes, embeds the base-colour textures and writes `<id>-<outfit>.glb`
that three.js / Blender / any glTF viewer loads directly. Bones keep their game names, so motion
can be driven by name later.

The face is not all geometry. The mouth is a URP **DecalProjector** under `jnt_C_head00_00` that projects
one cell of a 5x8 mouth sprite sheet onto the face; it is rebuilt here as a small grid mesh shrink-wrapped
onto the face along the projector's axis (material extras `{"decal": true}`, UVs baked to the default cell —
shift `map.offset` by whole cells to change expression). Eyebrows are real meshes that sit behind the bangs
and that the game draws over the hair: their node carries extras `{"overlay": true}` so a viewer can do the
same. Blush/pale/cheek decals (faded out by default) and blendshapes are not exported yet.

Unity is left-handed, glTF right-handed: X is negated throughout (positions, normals, translations,
quaternions as (x, -y, -z, w), matrices as S*M*S) and triangle winding is flipped.
"""

from __future__ import annotations

import argparse
import io
import json
import struct
import sys
from pathlib import Path

import UnityPy
from UnityPy.helpers.MeshHelper import MeshHandler

from holodori_asset_tools.entrypoint.extract import UNITY_VERSION

UnityPy.config.FALLBACK_UNITY_VERSION = UNITY_VERSION

HERE = Path(__file__).parent
STAGE = HERE / "staged"
HEAD_BONE = "jnt_C_head00_00"
HAIR_ROOT = "jnt_C_headHair00_00"
TEXTURE_MAX = 1024  # the game ships 2048px atlases; a browser office does not need them

FLOAT, USHORT, UINT = 5126, 5123, 5125
ARRAY_BUFFER, ELEMENT_BUFFER = 34962, 34963


class Glb:
    """Accumulates one binary buffer plus the glTF JSON that indexes into it."""

    def __init__(self):
        self.json: dict = {"asset": {"version": "2.0", "generator": "holodream build_chibi.py"},
                           "extensionsUsed": ["KHR_materials_unlit"], "scene": 0, "scenes": [{"nodes": []}],
                           "nodes": [], "meshes": [], "skins": [], "materials": [], "textures": [], "images": [],
                           "samplers": [{"magFilter": 9729, "minFilter": 9987}], "accessors": [], "bufferViews": []}
        self.blob = bytearray()

    def view(self, data: bytes, target: int | None = None) -> int:
        self.blob += b"\0" * (-len(self.blob) % 4)
        view = {"buffer": 0, "byteOffset": len(self.blob), "byteLength": len(data)}
        if target:
            view["target"] = target
        self.blob += data
        self.json["bufferViews"].append(view)
        return len(self.json["bufferViews"]) - 1

    def accessor(self, values: list, fmt: str, component: int, kind: str, target: int | None = None, bounds: bool = False) -> int:
        width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[kind]
        flat = [x for row in values for x in row] if width > 1 else list(values)
        accessor = {"bufferView": self.view(struct.pack(f"<{len(flat)}{fmt}", *flat), target), "componentType": component,
                    "count": len(values), "type": kind}
        if bounds:
            accessor["min"] = [min(row[i] for row in values) for i in range(width)]
            accessor["max"] = [max(row[i] for row in values) for i in range(width)]
        self.json["accessors"].append(accessor)
        return len(self.json["accessors"]) - 1

    def write(self, path: Path) -> None:
        self.json["buffers"] = [{"byteLength": len(self.blob)}]
        document = {k: v for k, v in self.json.items() if v or k in ("asset", "scene")}
        text = json.dumps(document, separators=(",", ":")).encode()
        text += b" " * (-len(text) % 4)
        blob = bytes(self.blob) + b"\0" * (-len(self.blob) % 4)
        path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(text) + 8 + len(blob))
                         + struct.pack("<I4s", len(text), b"JSON") + text + struct.pack("<I4s", len(blob), b"BIN\0") + blob)


def load(bundle: str, extra: list[str]) -> UnityPy.Environment:
    path = STAGE / bundle
    if not path.exists():
        sys.exit(f"{bundle} is not staged — run: python pull.py --filter \"{bundle}\" --no-extract")
    return UnityPy.load(str(path), *(str(STAGE / name) for name in extra if (STAGE / name).exists()))


def name_of(transform) -> str:
    return transform.m_GameObject.read().m_Name


def find(transform, name: str):
    if name_of(transform) == name:
        return transform
    for child in transform.m_Children:
        hit = find(child.read(), name)
        if hit:
            return hit
    return None


def add_bones(glb: Glb, transform, nodes_by_path: dict[int, int]) -> int:
    """Copy a Transform subtree into glTF nodes (handedness-converted). Returns the root node index."""
    p, r, s = transform.m_LocalPosition, transform.m_LocalRotation, transform.m_LocalScale
    index = len(glb.json["nodes"])
    glb.json["nodes"].append({"name": name_of(transform), "translation": [-p.x, p.y, p.z],
                              "rotation": [r.x, -r.y, -r.z, r.w], "scale": [s.x, s.y, s.z]})
    nodes_by_path[transform.object_reader.path_id] = index
    children = [add_bones(glb, child.read(), nodes_by_path) for child in transform.m_Children]
    if children:
        glb.json["nodes"][index]["children"] = children
    return index


def bind_matrix(m) -> list[float]:
    """Unity bind pose -> glTF inverse bind matrix: S*M*S, flattened column-major."""
    rows = [[m.e00, m.e01, m.e02, m.e03], [m.e10, m.e11, m.e12, m.e13], [m.e20, m.e21, m.e22, m.e23], [m.e30, m.e31, m.e32, m.e33]]
    sign = (-1, 1, 1, 1)
    return [rows[row][col] * sign[row] * sign[col] for col in range(4) for row in range(4)]


def add_material(glb: Glb, material, cache: dict) -> int:
    key = material.object_reader.path_id
    if key in cache:
        return cache[key]
    entry = {"name": material.m_Name, "doubleSided": True, "extensions": {"KHR_materials_unlit": {}},
             "pbrMetallicRoughness": {"metallicFactor": 0, "roughnessFactor": 1}}
    textures = {str(name): env.m_Texture for name, env in material.m_SavedProperties.m_TexEnvs}
    pointer = textures.get("_BaseMap") or textures.get("_MainTex")
    if pointer and pointer.path_id:
        try:
            image = pointer.read().image.convert("RGBA")
            image.thumbnail((TEXTURE_MAX, TEXTURE_MAX))
            low, _high = image.getchannel("A").getextrema()
            if low < 250:
                entry["alphaMode"], entry["alphaCutoff"] = "MASK", 0.5
            else:
                image = image.convert("RGB")
            data = io.BytesIO()
            image.save(data, "PNG", optimize=True)
            glb.json["images"].append({"bufferView": glb.view(data.getvalue()), "mimeType": "image/png"})
            glb.json["textures"].append({"sampler": 0, "source": len(glb.json["images"]) - 1})
            entry["pbrMetallicRoughness"]["baseColorTexture"] = {"index": len(glb.json["textures"]) - 1}
        except Exception as e:  # a texture living in a bundle that is not staged
            print(f"  ! texture of {material.m_Name} unavailable ({type(e).__name__}); left untextured")
    glb.json["materials"].append(entry)
    cache[key] = len(glb.json["materials"]) - 1
    return cache[key]


def is_outline(material_name: str) -> bool:
    """The game redraws each mesh as an inverted, slightly larger shell for the toon outline
    (`SubMeshOutlineMaterial`). Exported as ordinary geometry it would swallow the model."""
    return "outline" in material_name.lower()


def top_lod_renderers(env) -> list:
    renderers = [o.read() for o in env.objects if o.type.name == "SkinnedMeshRenderer"]
    return [r for r in renderers if not name_of_go(r).upper().endswith(tuple(f"_LOD{i}" for i in range(1, 6)))]


def name_of_go(component) -> str:
    return component.m_GameObject.read().m_Name


def add_renderer(glb: Glb, renderer, nodes_by_path: dict[int, int], materials: dict, skins: dict) -> int | None:
    mesh = renderer.m_Mesh.read()
    handler = MeshHandler(mesh)
    handler.process()
    if not handler.m_Vertices or not handler.m_BoneIndices:
        return None
    count = len(handler.m_Vertices)
    attributes = {"POSITION": glb.accessor([(-x, y, z) for x, y, z in handler.m_Vertices], "f", FLOAT, "VEC3", ARRAY_BUFFER, bounds=True)}
    if handler.m_Normals:
        attributes["NORMAL"] = glb.accessor([(-n[0], n[1], n[2]) for n in handler.m_Normals], "f", FLOAT, "VEC3", ARRAY_BUFFER)
    if handler.m_UV0:
        attributes["TEXCOORD_0"] = glb.accessor([(uv[0], 1 - uv[1]) for uv in handler.m_UV0], "f", FLOAT, "VEC2", ARRAY_BUFFER)
    # A mesh bound rigidly to one bone stores an index and no weights.
    weights = handler.m_BoneWeights or [(1.0,)] * count
    joints, normalised = [], []
    for index, weight in zip(handler.m_BoneIndices, weights):
        index, weight = (tuple(index) + (0, 0, 0, 0))[:4], (tuple(weight) + (0.0, 0.0, 0.0, 0.0))[:4]
        total = sum(weight) or 1.0
        weight = tuple(w / total for w in weight)
        joints.append(tuple(i if w > 0 else 0 for i, w in zip(index, weight)))
        normalised.append(weight)
    attributes["JOINTS_0"] = glb.accessor(joints, "H", USHORT, "VEC4", ARRAY_BUFFER)
    attributes["WEIGHTS_0"] = glb.accessor(normalised, "f", FLOAT, "VEC4", ARRAY_BUFFER)

    primitives = []
    for triangles, material in zip(handler.get_triangles(), renderer.m_Materials):
        if not triangles:
            continue
        try:
            source = material.read()
        except Exception as e:  # shared effect materials that live in bundles we did not stage
            print(f"  ! skipped a submesh of {mesh.m_Name}: material unavailable ({type(e).__name__})")
            continue
        if is_outline(source.m_Name):
            continue
        material_index = add_material(glb, source, materials)
        flat = [corner for a, b, c in triangles for corner in (a, c, b)]
        primitives.append({"attributes": attributes, "material": material_index,
                           "indices": glb.accessor(flat, "I", UINT, "SCALAR", ELEMENT_BUFFER)})
    if not primitives:
        return None

    glb.json["meshes"].append({"name": mesh.m_Name, "primitives": primitives})
    node = {"name": name_of_go(renderer), "mesh": len(glb.json["meshes"]) - 1, "skin": skin_for(glb, renderer, nodes_by_path, skins)}
    if "brow" in node["name"].lower():
        node["extras"] = {"overlay": True}  # the game draws brows over the bangs; a viewer should too
    glb.json["nodes"].append(node)
    return len(glb.json["nodes"]) - 1


def skin_for(glb: Glb, renderer, nodes_by_path: dict, skins: dict) -> int:
    bone_nodes = tuple(nodes_by_path[bone.path_id] for bone in renderer.m_Bones)
    matrices = [bind_matrix(m) for m in renderer.m_Mesh.read().m_BindPose]
    skin_key = (bone_nodes, tuple(round(v, 5) for m in matrices for v in m))
    if skin_key not in skins:
        glb.json["skins"].append({"joints": list(bone_nodes), "inverseBindMatrices": glb.accessor(matrices, "f", FLOAT, "MAT4")})
        skins[skin_key] = len(glb.json["skins"]) - 1
    return skins[skin_key]


# --- face decals ----------------------------------------------------------------------------

def _multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _local_matrix(transform):
    p, q, s = transform.m_LocalPosition, transform.m_LocalRotation, transform.m_LocalScale
    x, y, z, w = q.x, q.y, q.z, q.w
    rotation = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    scale = (s.x, s.y, s.z)
    return [[rotation[i][j] * scale[j] for j in range(3)] + [(p.x, p.y, p.z)[i]] for i in range(3)] + [[0, 0, 0, 1]]


def _mesh_space_matrix(transform, stop_name: str):
    """Transform -> mesh space. The meshes and `stop_name` (the skeleton root) share one parent at identity."""
    matrix = _local_matrix(transform)
    while name_of(transform) != stop_name:
        transform = transform.m_Father.read()
        matrix = _multiply(_local_matrix(transform), matrix)
    return matrix


def _apply(matrix, point, w=1.0):
    return tuple(sum(matrix[i][j] * (point + (w,))[j] for j in range(4)) for i in range(3))


def _ray_hit(origin, direction, triangle):
    """Moller-Trumbore. Distance along the ray, or None."""
    a, b, c = triangle
    e1, e2 = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
    h = (direction[1] * e2[2] - direction[2] * e2[1], direction[2] * e2[0] - direction[0] * e2[2], direction[0] * e2[1] - direction[1] * e2[0])
    det = sum(e1[i] * h[i] for i in range(3))
    if abs(det) < 1e-9:
        return None
    s = [origin[i] - a[i] for i in range(3)]
    u = sum(s[i] * h[i] for i in range(3)) / det
    if u < 0 or u > 1:
        return None
    q = (s[1] * e1[2] - s[2] * e1[1], s[2] * e1[0] - s[0] * e1[2], s[0] * e1[1] - s[1] * e1[0])
    v = sum(direction[i] * q[i] for i in range(3)) / det
    if v < 0 or u + v > 1:
        return None
    return sum(e2[i] * q[i] for i in range(3)) / det


DECAL_GRID = (8, 6)      # quads across / down: enough to follow a chibi's face curvature
DECAL_LIFT = 0.004       # metres off the skin, so it never z-fights the face


def add_face_decals(glb: Glb, env, nodes_by_path: dict, materials: dict, skins: dict) -> list[int]:
    """Rebuild the visible DecalProjectors (the mouth) as meshes pinned to the head bone."""
    renderers = {name_of_go(r): r for r in top_lod_renderers(env)}
    face = renderers.get("Geo_Eye_LOD0")
    if face is None:
        return []
    # Which submesh carries the face skin differs per character, so every surface of the head
    # meshes is a candidate; each projector only tests the triangles near it.
    surfaces = []
    for mesh_name in ("Geo_Eye_LOD0", "Geo_Body_LOD0"):
        renderer = renderers.get(mesh_name)
        if renderer is None:
            continue
        handler = MeshHandler(renderer.m_Mesh.read())
        handler.process()
        for triangles, material in zip(handler.get_triangles(), renderer.m_Materials):
            try:
                if is_outline(material.read().m_Name):
                    continue
            except Exception:
                continue
            surfaces += [tuple(handler.m_Vertices[i] for i in tri) for tri in triangles]
    head_joint = next((i for i, bone in enumerate(face.m_Bones) if name_of(bone.read()) == HEAD_BONE), None)
    if head_joint is None:
        return []

    added = []
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        if "m_UVScale" not in tree or not tree.get("m_FadeFactor"):
            continue  # not a decal projector, or one the game keeps faded out until an expression needs it
        game_object = obj.read().m_GameObject.read()
        try:
            source = next(o for o in env.objects if o.path_id == tree["m_Material"]["m_PathID"]).read()
        except StopIteration:
            continue
        matrix = _mesh_space_matrix(game_object.m_Transform.read(), "jnt_C_root00_00")
        size, offset, scale, bias = tree["m_Size"], tree["m_Offset"], tree["m_UVScale"], tree["m_UVBias"]
        direction = _apply(matrix, (0.0, 0.0, 1.0), w=0.0)
        length = sum(d * d for d in direction) ** 0.5
        direction = tuple(d / length for d in direction)
        depth = size["z"] * length
        centre = _apply(matrix, (offset["x"], offset["y"], offset["z"]))
        reach = max(size.values()) * length
        skin_triangles = [tri for tri in surfaces if all(abs(tri[0][i] - centre[i]) < reach for i in range(3))]

        columns, rows = DECAL_GRID
        hits, points = [], []
        for row in range(rows + 1):
            for column in range(columns + 1):
                s, t = column / columns, row / rows
                origin = _apply(matrix, ((s - 0.5) * size["x"] + offset["x"], (t - 0.5) * size["y"] + offset["y"], offset["z"] - size["z"] / 2))
                distances = [d for d in (_ray_hit(origin, direction, tri) for tri in skin_triangles) if d is not None and 0 <= d <= depth]
                hits.append(min(distances) if distances else None)
                points.append((origin, s, t))
        found = [h for h in hits if h is not None]
        if not found:
            print(f"  ! decal {game_object.m_Name}: no face under the projector, skipped")
            continue
        fallback = sum(found) / len(found)  # grid points that overshoot the face's edge stay in its plane
        positions, uvs = [], []
        for (origin, s, t), hit in zip(points, hits):
            distance = (hit if hit is not None else fallback) - DECAL_LIFT
            position = tuple(origin[i] + direction[i] * distance for i in range(3))
            positions.append((-position[0], position[1], position[2]))
            uvs.append((bias["x"] + scale["x"] * s, 1 - (bias["y"] + scale["y"] * t)))
        indices = []
        for row in range(rows):
            for column in range(columns):
                a = row * (columns + 1) + column
                b, c, d = a + 1, a + columns + 1, a + columns + 2
                indices += [a, b, c, b, d, c]

        material_index = add_material(glb, source, materials)
        material = glb.json["materials"][material_index]
        material.update(alphaMode="BLEND", doubleSided=True, extras={"decal": True, "cell": [scale["x"], scale["y"]]})
        material.pop("alphaCutoff", None)
        count = len(positions)
        attributes = {"POSITION": glb.accessor(positions, "f", FLOAT, "VEC3", ARRAY_BUFFER, bounds=True),
                      "TEXCOORD_0": glb.accessor(uvs, "f", FLOAT, "VEC2", ARRAY_BUFFER),
                      "JOINTS_0": glb.accessor([(head_joint, 0, 0, 0)] * count, "H", USHORT, "VEC4", ARRAY_BUFFER),
                      "WEIGHTS_0": glb.accessor([(1.0, 0.0, 0.0, 0.0)] * count, "f", FLOAT, "VEC4", ARRAY_BUFFER)}
        glb.json["meshes"].append({"name": f"Decal_{game_object.m_Name}", "primitives": [
            {"attributes": attributes, "material": material_index, "indices": glb.accessor(indices, "I", UINT, "SCALAR", ELEMENT_BUFFER)}]})
        glb.json["nodes"].append({"name": f"Decal_{game_object.m_Name}", "mesh": len(glb.json["meshes"]) - 1,
                                  "skin": skin_for(glb, face, nodes_by_path, skins), "extras": {"decal": True}})
        added.append(len(glb.json["nodes"]) - 1)
        print(f"  decal {game_object.m_Name}: {len(found)}/{count} grid points on the face")
    return added


def build(character: str, outfit: str, hair_outfit: str | None, out_dir: Path) -> Path:
    shared = ["m_fef", "submeshoutlinematerial", "t_chr_drs_00000-base-0000-00_eye_rmp", "t_chr_drs_00000-base-0000-00_rmp"]
    body_env = load(f"mdl_chr_drs_{character}-{outfit}_body", shared)
    hair_env = load(f"mdl_chr_drs_{character}-{hair_outfit or outfit}_hair", shared)

    glb, nodes_by_path, materials, skins = Glb(), {}, {}, {}
    scene_nodes = glb.json["scenes"][0]["nodes"]

    def rig_root(env, bone_name: str):
        roots = [t for t in (o.read() for o in env.objects if o.type.name == "Transform") if not t.m_Father.path_id]
        for root in roots:
            hit = find(root, bone_name)
            if hit:
                return hit
        sys.exit(f"{bone_name} not found")

    scene_nodes.append(add_bones(glb, rig_root(body_env, "jnt_C_root00_00"), nodes_by_path))
    hair_index = add_bones(glb, rig_root(hair_env, HAIR_ROOT), nodes_by_path)
    head = next(n for n in glb.json["nodes"] if n["name"] == HEAD_BONE)
    head.setdefault("children", []).append(hair_index)

    for env in (body_env, hair_env):
        for renderer in top_lod_renderers(env):
            node = add_renderer(glb, renderer, nodes_by_path, materials, skins)
            if node is not None:
                scene_nodes.append(node)
    scene_nodes += add_face_decals(glb, body_env, nodes_by_path, materials, skins)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{character}-{outfit}.glb"
    glb.write(path)
    print(f"{path}  {path.stat().st_size / 1e6:.2f} MB  meshes={len(glb.json['meshes'])} bones={len(nodes_by_path)} "
          f"materials={len(glb.json['materials'])} textures={len(glb.json['images'])}")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("character", help="character id, e.g. 00018")
    parser.add_argument("outfit", help="outfit id, e.g. nrml-0004-00")
    parser.add_argument("--hair", help="hair outfit id when it differs from the body's (e.g. base-0000-00)")
    parser.add_argument("--out", type=Path, default=HERE / "out" / "chibi")
    args = parser.parse_args()
    build(args.character, args.outfit, args.hair, args.out)
