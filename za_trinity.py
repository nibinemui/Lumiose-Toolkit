"""Trinity engine formats (Pokémon Legends: Z-A / Scarlet & Violet).

Reads models (.trmdl/.trmsh/.trmbf), skeletons (.trskl), materials (.trmtr) and skeletal
animations (.tranm) and writes binary glTF (.glb).

Format knowledge ported from pkZukan's GFTool (GPL-2.0, https://github.com/pkZukan/gftool):
GFTool.Core/Flatbuffers/TR/* and GFTool.Renderer/Scene/GraphicsObjects/{Model,Armature,Animation}.cs
"""
import io
import json
import math
import os
import struct

import numpy as np

import za_formats as zf

# ---------------------------------------------------------------------------
# minimal FlatBuffer reader
# ---------------------------------------------------------------------------


class Table:
    __slots__ = ('b', 'pos', 'vt', 'vlen')

    def __init__(self, buf, pos):
        self.b = buf
        self.pos = pos
        self.vt = pos - struct.unpack_from('<i', buf, pos)[0]
        self.vlen = struct.unpack_from('<H', buf, self.vt)[0]

    @classmethod
    def root(cls, buf):
        return cls(buf, struct.unpack_from('<I', buf, 0)[0])

    def off(self, slot):
        o = 4 + 2 * slot
        return struct.unpack_from('<H', self.b, self.vt + o)[0] if o < self.vlen else 0

    def has(self, slot):
        return self.off(slot) != 0

    def scalar(self, slot, fmt, default=0):
        o = self.off(slot)
        return struct.unpack_from('<' + fmt, self.b, self.pos + o)[0] if o else default

    def u32(self, slot, default=0):
        return self.scalar(slot, 'I', default)

    def i32(self, slot, default=0):
        return self.scalar(slot, 'i', default)

    def table(self, slot):
        o = self.off(slot)
        if not o:
            return None
        p = self.pos + o
        return Table(self.b, p + struct.unpack_from('<I', self.b, p)[0])

    def string(self, slot, default=''):
        o = self.off(slot)
        if not o:
            return default
        p = self.pos + o
        q = p + struct.unpack_from('<I', self.b, p)[0]
        n = struct.unpack_from('<I', self.b, q)[0]
        return bytes(self.b[q + 4:q + 4 + n]).decode('utf-8', 'replace')

    def vector(self, slot):
        """(start, count) of a vector, or (0, 0)."""
        o = self.off(slot)
        if not o:
            return 0, 0
        p = self.pos + o
        v = p + struct.unpack_from('<I', self.b, p)[0]
        return v + 4, struct.unpack_from('<I', self.b, v)[0]

    def tables(self, slot):
        s, n = self.vector(slot)
        out = []
        for k in range(n):
            e = s + 4 * k
            out.append(Table(self.b, e + struct.unpack_from('<I', self.b, e)[0]))
        return out

    def strings(self, slot):
        s, n = self.vector(slot)
        out = []
        for k in range(n):
            e = s + 4 * k
            q = e + struct.unpack_from('<I', self.b, e)[0]
            ln = struct.unpack_from('<I', self.b, q)[0]
            out.append(bytes(self.b[q + 4:q + 4 + ln]).decode('utf-8', 'replace'))
        return out

    def array(self, slot, dtype, width=1):
        """Vector of scalars or fixed-size structs as a numpy array."""
        s, n = self.vector(slot)
        dt = np.dtype(dtype)
        a = np.frombuffer(self.b, dtype=dt, count=n * width, offset=s) if n else np.zeros(0, dt)
        return a.reshape(n, width) if width > 1 else a

    def bytes(self, slot):
        s, n = self.vector(slot)
        return bytes(self.b[s:s + n])

    def struct_pos(self, slot):
        o = self.off(slot)
        return self.pos + o if o else None

    def vec3(self, slot, default=(0.0, 0.0, 0.0)):
        """Vector3f stored inline (struct) - or as a table on older files."""
        p = self.struct_pos(slot)
        if p is None:
            return default
        return struct.unpack_from('<3f', self.b, p)


_OVERRIDE = {'root': None, 'romfs': None}


def set_override(game_root, mod_romfs):
    """Read files from a mod's romfs instead of the game folder where the mod has them."""
    _OVERRIDE['root'] = os.path.normcase(os.path.abspath(game_root)) if game_root else None
    _OVERRIDE['romfs'] = mod_romfs if mod_romfs and os.path.isdir(mod_romfs) else None


def resolve(path):
    root, romfs = _OVERRIDE['root'], _OVERRIDE['romfs']
    if root and romfs:
        p = os.path.abspath(path)
        if os.path.normcase(p).startswith(root + os.sep):
            cand = os.path.join(romfs, os.path.relpath(p, root))
            if os.path.isfile(cand):
                return cand
    return path


def read_file(path):
    with open(resolve(path), 'rb') as f:
        return f.read()


# ---------------------------------------------------------------------------
# skeleton
# ---------------------------------------------------------------------------

def quat_from_euler_xyz(x, y, z):
    """q = qZ * qY * qX (X applied first), returned as (x, y, z, w)."""
    cx, sx = math.cos(x / 2), math.sin(x / 2)
    cy, sy = math.cos(y / 2), math.sin(y / 2)
    cz, sz = math.cos(z / 2), math.sin(z / 2)
    qx = (sx, 0.0, 0.0, cx)
    qy = (0.0, sy, 0.0, cy)
    qz = (0.0, 0.0, sz, cz)
    return quat_mul(quat_mul(qz, qy), qx)


def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def trs_matrix(t, q, s):
    x, y, z, w = q
    r = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    m = np.eye(4)
    m[:3, :3] = r * np.array(s)[None, :]
    m[:3, 3] = t
    return m


class Bone:
    __slots__ = ('name', 'parent', 't', 'q', 's', 'joint', 'skinning', 'parent_name')


class Skeleton:
    def __init__(self, path):
        root = Table.root(read_file(path))
        self.bones = []
        for n in root.tables(1):
            b = Bone()
            b.name = n.string(0)
            srt = n.table(1)
            if srt is not None:
                b.s = srt.vec3(0, (1.0, 1.0, 1.0))
                e = srt.vec3(1)
                b.t = srt.vec3(2)
            else:
                b.s, e, b.t = (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
            b.q = quat_from_euler_xyz(*e)
            b.parent = n.scalar(4, 'i', -1)
            b.joint = n.scalar(5, 'i', -1)
            b.parent_name = n.string(6)
            b.skinning = False
            self.bones.append(b)
        joints = root.tables(2)
        self.joint_to_node = [-1] * len(joints)
        for i, b in enumerate(self.bones):
            if 0 <= b.joint < len(joints):
                self.joint_to_node[b.joint] = i
                b.skinning = bool(joints[b.joint].scalar(1, 'B', 0))
        self.palette_offset = root.u32(4)
        self.index = {b.name: i for i, b in enumerate(self.bones)}

    def world_matrices(self):
        out = [None] * len(self.bones)

        def world(i):
            if out[i] is None:
                b = self.bones[i]
                local = trs_matrix(b.t, b.q, b.s)
                p = b.parent
                out[i] = world(p) @ local if 0 <= p < len(self.bones) and p != i else local
            return out[i]
        for i in range(len(self.bones)):
            world(i)
        return out


# ---------------------------------------------------------------------------
# materials
# ---------------------------------------------------------------------------

class Material:
    # TRMTR UVWrapMode -> glTF sampler wrap
    WRAP_GL = {0: 33071, 1: 10497, 2: 33648, 3: 33648, 4: 33648, 5: 33071, 6: 33648, 7: 33071}

    def __init__(self, t, base_dir):
        self.name = t.string(0)
        self.textures = {}
        self.wrap = {}           # texture name -> (wrapS, wrapT) as glTF constants
        samplers = t.tables(3)
        for tex in t.tables(2):
            f = tex.string(1)
            if f:
                self.textures[tex.string(0)] = os.path.normpath(os.path.join(base_dir, f))
                slot = tex.u32(2)
                if slot < len(samplers):
                    s = samplers[slot]
                    self.wrap[tex.string(0)] = (self.WRAP_GL.get(s.u32(9), 10497), self.WRAP_GL.get(s.u32(10), 10497))
        self.shader = ''
        self.options = {}
        for sh in t.tables(1):
            self.shader = sh.string(0) or self.shader
            self.options.update({v.string(0): v.string(1) for v in sh.tables(1)})
        self.floats = {p.string(0): p.scalar(1, 'f', 0.0) for p in t.tables(4)}
        self.vec4 = {}
        for p in t.tables(7):
            pos = p.struct_pos(1)
            if pos is not None:
                self.vec4[p.string(0)] = struct.unpack_from('<4f', p.b, pos)
        self.ints = {p.string(0): p.scalar(1, 'i', -1) for p in t.tables(9)}
        self.alpha = t.string(15)

    def texture(self, *names, suffixes=()):
        k = self.texture_key(*names, suffixes=suffixes)
        return self.textures[k] if k else None

    def texture_key(self, *names, suffixes=()):
        for n in names:
            for k in self.textures:
                if k.lower() == n.lower():
                    return k
        for k, v in self.textures.items():
            if any(v.lower().endswith(s) for s in suffixes):
                return k
        return None

    def uv_transform(self, normal=False):
        """KHR_texture_transform (offset, scale) equal to the shader's get_uv() for our flipped UVs.

        Shader: s = (uv - c - o) * k + c, sampled at (s.x, 1 - s.y). Our TEXCOORD is (u, 1 - v)."""
        if normal and self.options.get('EnableUVScaleOffsetNormal') == 'True':
            k = self.vec4.get('UVScaleOffsetNormal', (1, 1, 0, 0))
            c = (0.0, 0.0)
        else:
            k = self.vec4.get('UVScaleOffset', (1, 1, 0, 0))
            c = self.vec4.get('UVCenter0', (0, 0, 0, 0))[:2]
        sx, sy, ox, oy = k
        off = (c[0] - sx * (c[0] + ox), 1 - c[1] - sy * (1 - c[1] - oy))
        if abs(sx - 1) < 1e-6 and abs(sy - 1) < 1e-6 and abs(off[0]) < 1e-6 and abs(off[1]) < 1e-6:
            return None
        return [float(off[0]), float(off[1])], [float(sx), float(sy)]


def resize_channels(img, size, resample=None):
    """Resize every channel on its own (PIL premultiplies RGBA by alpha, which wipes data masks
    whose alpha channel is 0)."""
    from PIL import Image
    if img.size == tuple(size):
        return img
    rs = Image.BILINEAR if resample is None else resample
    bands = [b.resize(size, rs) for b in img.split()]
    return Image.merge(img.mode, bands)


def _srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


_PALETTES = {}


def _palette_tints(mat):
    """Layer colours picked from a colour-table texture (clothing / hair / skin): ColorTableMap is a
    strip of ColorTableDivideNumber cells, BaseColorIndex1-4 (1-based) choose a cell per layer.
    Returns 4 linear RGB colours (None where the layer has no index), or None."""
    path = mat.texture('ColorTableMap')
    n = mat.ints.get('ColorTableDivideNumber', 0)
    if not path or not n or n < 1 or not os.path.isfile(path):
        return None
    real = resolve(path)                      # the mod's copy (custom colours) when there is one
    try:
        key = (real, os.path.getmtime(real))
    except OSError:
        key = (real, 0)
    if key not in _PALETTES:
        try:
            img = zf.Bntx(read_file(path)).textures[0].decode().convert('RGB')
            _PALETTES[key] = np.asarray(img, np.float32) / 255.0
        except Exception:  # noqa: BLE001
            _PALETTES[key] = None
    a = _PALETTES[key]
    if a is None:
        return None
    h, w = a.shape[:2]
    out = []
    for i in range(1, 5):
        idx = mat.ints.get('BaseColorIndex%d' % i)
        if idx is None or idx < 1:
            out.append(None)
            continue
        x = int(min(w - 1, (min(idx, n) - 0.5) * w / n))
        out.append(_srgb_to_linear(a[0:max(1, h // 2), x].mean(0)))      # top half = colour, bottom = shade
    return out


def read_trmmt(path):
    """Material sets of a model (.trmmt): [{'name', 'trmtr': [files], 'groups': {group: {material:
    {param: [value per choice]}}}}]. Clothing uses it for patterns (tex_00…) and colours (color_*)."""
    root = Table.root(read_file(path))
    base = os.path.dirname(path)
    out = []
    for st in root.tables(2):
        sets = {'name': st.string(0), 'trmtr': [os.path.normpath(os.path.join(base, f)) for f in st.strings(1)],
                'groups': {}}
        for g in st.tables(3):
            data = g.table(10)
            mats = {}
            if data and data.has(1):
                try:
                    for me in data.tables(1):
                        params = {}
                        if me.has(4):
                            for pa in me.tables(4):
                                s0, cnt = pa.vector(1)
                                params[pa.string(0)] = [struct.unpack_from('<i', pa.b, s0 + 4 * k)[0] for k in range(cnt)]
                        if params:
                            mats[me.string(0)] = params
                except (struct.error, IndexError):
                    mats = {}
            sets['groups'][g.string(0)] = mats
        out.append(sets)
    return out


COLOUR_GROUP_LABELS = {'color_skin': 'Skin tone'}


def clothing_sets(trmdl_path):
    """Pattern sets and colour choices from the model's .trmmt, for previewing clothing like the game's
    dress-up screen: {label: {'set': name, 'trmtr': [files], 'groups': [(group, label, count)],
    'params': {group: {material: {param: [value per choice]}}}}}. Empty without colour choices."""
    tp = os.path.splitext(trmdl_path)[0] + '.trmmt'
    if not os.path.isfile(resolve(tp)):
        return {}
    try:
        sets = read_trmmt(tp)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    patterns = [st for st in sets if st['name'] != 'normal'] or sets
    if len(patterns) < len(sets):
        patterns = sets          # 'normal' is a pattern of its own (dress-up items "…_normal_r00_c00")
    for st in patterns:
        groups, params = [], {}
        for g, mats in st['groups'].items():
            n = max([len(v) for pr in mats.values() for v in pr.values()] or [0])
            if not g.startswith('color_') or n < 1:
                continue
            k = len([x for x in groups if x[0] != 'color_skin']) + 1
            label = COLOUR_GROUP_LABELS.get(g, 'Colour %d' % k)
            groups.append((g, label, n))
            params[g] = mats
        if not any(n > 1 for g, _, n in groups if g != 'color_skin'):
            continue
        name = st['name']
        label = 'Pattern %s' % name.replace('tex_', '') if name.startswith('tex_') else name.title()
        out[label] = {'set': name, 'trmtr': st['trmtr'], 'groups': groups, 'params': params}
    return out


def clothing_overrides(info, choices):
    """(material files, overrides) for a clothing set with {group: choice index}."""
    ov = {}
    for g, mats in info['params'].items():
        k = choices.get(g, 0)
        for mat, params in mats.items():
            for pn, vals in params.items():
                if vals:
                    ov.setdefault(mat, {})[pn] = vals[max(0, min(k, len(vals) - 1))]
    return info['trmtr'], ov


def baked_base_color(mat, max_size=2048):
    """Base colour texture with the material's layer colours applied (IkCharacter shader logic
    from GFTool's IkCharacter.glsl). Returns a PIL RGBA image, or None if there is no base map."""
    from PIL import Image
    bc = mat.texture('BaseColorMap', 'BaseColorMap0', 'AlbedoMap', suffixes=('_alb.bntx',))
    texel = None
    if bc and os.path.isfile(bc):
        try:
            texel = zf.Bntx(read_file(bc)).textures[0].decode()
        except Exception:  # noqa: BLE001
            texel = None
    layers = int(float(mat.options.get('NumMaterialLayer', '1') or 1))
    lym = mat.texture('LayerMaskMap')
    use_layers = (layers > 1 and lym and os.path.isfile(lym)
                  and mat.options.get('EnableLayerMaskMap', 'True') != 'False'
                  and mat.ints.get('UVIndexLayerMask', 0) in (0, -1))
    if texel is None and not use_layers:
        return None
    mask_img = None
    if use_layers:
        try:
            mask_img = zf.Bntx(read_file(lym)).textures[0].decode()
        except Exception:  # noqa: BLE001
            mask_img = None
    if texel is None:
        texel = Image.new('RGBA', mask_img.size if mask_img else (256, 256), (255, 255, 255, 255))
    # work at the larger of the two resolutions so neither loses detail
    size = texel.size
    if mask_img is not None and mask_img.size[0] * mask_img.size[1] > size[0] * size[1]:
        size = mask_img.size
    if max(size) > max_size:
        s = max_size / max(size)
        size = (max(1, int(size[0] * s)), max(1, int(size[1] * s)))
    texel = resize_channels(texel, size)
    t = np.asarray(texel, np.float32) / 255.0
    rgb = _srgb_to_linear(t[..., :3])
    alpha = t[..., 3:4]
    base_col = np.array(mat.vec4.get('BaseColor', (1, 1, 1, 1)), np.float32)
    base = rgb * base_col[:3]
    if mask_img is not None:
        mask = np.asarray(resize_channels(mask_img, size), np.float32) / 255.0
        if True:
            scale = np.array([mat.floats.get('LayerMaskScale%d' % i, 1.0) for i in range(1, 5)], np.float32)
            w = np.clip(mask * scale, 0, 1)
            for i in range(4):
                if layers < i + 2:
                    w[..., i] = 0
            total = np.clip(1.0 - w.sum(-1, keepdims=True), 0, None)
            base = base * total
            multiply = mat.options.get('BaseColorMultiply', 'False') == 'True'
            pal = _palette_tints(mat)
            for i in range(4):
                tint = pal[i] if pal and pal[i] is not None else                     np.array(mat.vec4.get('BaseColorLayer%d' % (i + 1), (1, 1, 1, 1))[:3], np.float32)
                tint = rgb * tint if multiply else np.broadcast_to(tint, rgb.shape)
                wi = w[..., i:i + 1]
                base = base * (1 - wi) + tint * wi
    sat = mat.floats.get('SaturationPower', 1.0)
    dark = mat.floats.get('BaseColorDarkness', 0.0)
    lum = (base * np.array([0.2126, 0.7152, 0.0722], np.float32)).sum(-1, keepdims=True)
    base = np.maximum((lum + (base - lum) * sat) * (1 - dark), 0)
    out = np.concatenate([_linear_to_srgb(base), alpha], -1)
    return Image.fromarray((out * 255 + 0.5).astype(np.uint8), 'RGBA')


def read_materials(path):
    root = Table.root(read_file(path))
    base = os.path.dirname(path)
    return {m.name: m for m in (Material(t, base) for t in root.tables(1))}


# ---------------------------------------------------------------------------
# meshes
# ---------------------------------------------------------------------------

POSITION, NORMAL, TANGENT, BINORMAL, COLOR, TEX_COORD, BLEND_INDEX, BLEND_WEIGHTS = 1, 2, 3, 4, 5, 6, 7, 8
FMT_SIZE = {20: 4, 22: 4, 36: 4, 37: 4, 39: 8, 43: 8, 48: 8, 51: 12, 52: 16, 54: 16}


def _column(buf, stride, offset, size, count):
    a = np.frombuffer(buf, dtype=np.uint8, count=stride * count).reshape(count, stride)
    return np.ascontiguousarray(a[:, offset:offset + size])


def _decode(col, fmt, usage):
    """Decode one vertex attribute column (n, size bytes) the way GFTool does."""
    n = col.shape[0]
    if fmt == 51:
        v = col.view('<f4').reshape(n, 3)
        return np.concatenate([v, np.ones((n, 1), np.float32)], 1) if usage == TANGENT else v
    if fmt == 54:
        v = col.view('<f4').reshape(n, 4)
        if usage in (POSITION, NORMAL, BINORMAL):
            return v[:, 1:4]
        return v[:, [1, 2, 3, 0]]                  # W stored first
    if fmt == 48:
        return col.view('<f4').reshape(n, 2)
    if fmt == 52:
        v = col.view('<u4').reshape(n, 4)
        return v[:, [1, 2, 3, 0]].astype(np.float32)
    if fmt == 43:
        v = col.view('<f2').reshape(n, 4).astype(np.float32)
        if usage in (POSITION, NORMAL, BINORMAL):
            return v[:, :3]
        if usage == TEX_COORD:
            return v[:, :2]
        return v
    if fmt == 39:
        u = col.view('<u2').reshape(n, 4).astype(np.float32)
        if usage == BLEND_INDEX:
            return u[:, [1, 2, 3, 0]]
        f = u / 65535.0
        if usage == BLEND_WEIGHTS:
            return f[:, [1, 2, 3, 0]]
        if usage in (NORMAL, BINORMAL):
            return f[:, :3] * 2 - 1
        if usage == TANGENT:
            return f * 2 - 1
        if usage == TEX_COORD:
            return f[:, :2]
        if usage == POSITION:
            return f[:, :3]
        return f
    if fmt in (20, 22):
        u = col.astype(np.float32)
        if usage == BLEND_INDEX:
            return u[:, [1, 2, 3, 0]]
        f = u / 255.0
        if usage == BLEND_WEIGHTS:
            return f[:, [1, 2, 3, 0]]
        if usage in (NORMAL, BINORMAL):
            return f[:, :3] * 2 - 1
        if usage == TANGENT:
            return f * 2 - 1
        if usage == TEX_COORD:
            return f[:, :2]
        if usage == POSITION:
            return f[:, :3]
        return f
    return None


class Submesh:
    def __init__(self):
        self.name = ''
        self.material = ''
        self.attrs = {}          # 'POSITION', 'NORMAL', 'TEXCOORD_0', 'COLOR_0', 'TANGENT', 'JOINTS', 'WEIGHTS'
        self.indices = None
        self.bone_weights = []   # RigIndex list from TRMesh.boneWeight
        self.mesh_key = None     # parts of the same TRMesh share their vertex data


def read_meshes(trmsh_path):
    root = Table.root(read_file(trmsh_path))
    buf_path = os.path.join(os.path.dirname(trmsh_path), root.string(2))
    broot = Table.root(read_file(buf_path))
    buffers = broot.tables(1)
    out = []
    for i, mesh in enumerate(root.tables(1)):
        if i >= len(buffers):
            break
        mb = buffers[i]
        vbufs = [t.bytes(0) for t in mb.tables(1)]
        ibufs = [t.bytes(0) for t in mb.tables(0)]
        if not vbufs or not ibufs:
            continue
        index_type = mesh.u32(2)
        decls = mesh.tables(3)
        rig = [bw.u32(0) for bw in mesh.tables(10)]
        decoded = {}
        for part in mesh.tables(4):
            sm = Submesh()
            sm.name = mesh.string(0)
            sm.material = part.string(3)
            sm.bone_weights = rig
            di = part.scalar(4, 'i', -1)
            if di < 0 or di >= len(decls):
                di = 0
            if not decls:
                continue
            sm.mesh_key = (trmsh_path, i, di)
            if di in decoded:
                sm.attrs, nverts = decoded[di]
                _read_indices(sm, part, ibufs[0], index_type, nverts)
                out.append(sm)
                continue
            decl = decls[di]
            sizes = [s.u32(0) for s in decl.tables(1)]
            elements = []
            for el in decl.tables(0):
                elements.append((el.scalar(0, 'i', -1), el.u32(1), el.u32(2), el.u32(3), el.u32(4)))
            pos_el = next((e for e in elements if e[1] == POSITION), None)
            if pos_el is None:
                continue

            def stride_of(si):
                si = 0 if si == -1 else si
                return sizes[si] if 0 <= si < len(sizes) else 0
            pstride = stride_of(pos_el[0])
            if pstride <= 0 or pos_el[2] >= len(vbufs):
                continue
            nverts = len(vbufs[pos_el[2]]) // pstride
            idx_streams, wgt_streams, uv_sets = [], [], []
            for si, usage, layer, fmt, offset in elements:
                stride = stride_of(si)
                size = FMT_SIZE.get(fmt)
                if not size or stride <= 0 or layer >= len(vbufs) or offset + size > stride:
                    continue
                count = min(nverts, len(vbufs[layer]) // stride)
                if count < nverts:
                    continue
                v = _decode(_column(vbufs[layer], stride, offset, size, nverts), fmt, usage)
                if v is None:
                    continue
                if usage == POSITION:
                    sm.attrs['POSITION'] = v.astype(np.float32)
                elif usage == NORMAL:
                    sm.attrs['NORMAL'] = v.astype(np.float32)
                elif usage == TANGENT:
                    sm.attrs['TANGENT'] = v.astype(np.float32)
                elif usage == TEX_COORD:
                    uv_sets.append(v.astype(np.float32))
                elif usage == COLOR and 'COLOR_0' not in sm.attrs:
                    sm.attrs['COLOR_0'] = v.astype(np.float32) if v.shape[1] == 4 else None
                elif usage == BLEND_INDEX:
                    idx_streams.append(v)
                elif usage == BLEND_WEIGHTS:
                    wgt_streams.append(v)
            for k, uv in enumerate(uv_sets[:2]):
                uv = uv.copy()
                uv[:, 1] = 1.0 - uv[:, 1]
                sm.attrs['TEXCOORD_%d' % k] = uv
            if idx_streams and wgt_streams:
                n = min(len(idx_streams), len(wgt_streams))
                sm.attrs['JOINTS'] = np.concatenate(idx_streams[:n], 1)
                sm.attrs['WEIGHTS'] = np.concatenate(wgt_streams[:n], 1)
            decoded[di] = (sm.attrs, nverts)
            _read_indices(sm, part, ibufs[0], index_type, nverts)
            out.append(sm)
    return out


def _read_indices(sm, part, ib, index_type, nverts):
    isz = 1 << index_type
    dt = {1: '<u1', 2: '<u2', 4: '<u4'}.get(isz, '<u2')
    start, cnt = part.u32(1), part.u32(0)
    cnt = min(cnt, max(0, (len(ib) - start * isz) // isz))
    idx = np.frombuffer(ib, dtype=dt, count=cnt, offset=start * isz).astype(np.uint32)
    tri = idx[:len(idx) // 3 * 3].reshape(-1, 3)
    tri = tri[(tri < nverts).all(1)]                 # drop whole triangles that point outside the buffer
    sm.indices = tri.reshape(-1)


def remap_joints(sm, skel):
    """Map raw blend indices to skeleton node indices (GFTool's auto heuristic, simplified).

    Parts of one mesh share their attribute dict, so this runs once per shared vertex set."""
    if sm.attrs.get('_remapped'):
        sm.joint_mode = sm.attrs['_remapped']
        return
    if 'JOINTS' not in sm.attrs or skel is None:
        sm.attrs.pop('JOINTS', None)
        sm.attrs.pop('WEIGHTS', None)
        sm.attrs['_remapped'] = sm.joint_mode = 'static'
        return
    raw = np.rint(sm.attrs['JOINTS']).astype(np.int64)
    w = sm.attrs['WEIGHTS']
    nb = len(skel.bones)
    j2n = np.array(skel.joint_to_node or [0], dtype=np.int64)
    skinning = np.array([b.skinning for b in skel.bones] + [False])
    rig = np.array(sm.bone_weights or [0], dtype=np.int64)
    cands = []
    if sm.bone_weights and raw.max() < len(rig):
        cands.append(('boneweights', rig[raw]))
        mapped = rig[raw]
        if mapped.max() < len(j2n):
            cands.append(('boneweights+joint', j2n[mapped]))
    if len(skel.joint_to_node) and raw.max() < len(j2n):
        cands.append(('joint', j2n[raw]))
    cands.append(('none', raw))
    used = w > 0
    best, best_score = None, None
    for name, m in cands:
        oor = int(((m < 0) | (m >= nb))[used].sum())
        mm = np.clip(m, 0, nb)
        non = int((~skinning[mm])[used].sum())
        score = (oor, non)
        if best_score is None or score < best_score:
            best, best_score = (name, m), score
    if best_score[0] > 0 or nb < 2:
        # indices point outside this skeleton (e.g. clothes bound to the player's body skeleton):
        # export the mesh unskinned in its rest pose instead of binding it wrongly
        sm.attrs.pop('JOINTS', None)
        sm.attrs.pop('WEIGHTS', None)
        sm.attrs['_remapped'] = sm.joint_mode = 'static (external skeleton)'
        return
    sm.attrs['JOINTS'] = np.clip(best[1], 0, max(nb - 1, 0))
    sm.attrs['_remapped'] = sm.joint_mode = best[0]


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------

class Outfit:
    """Player clothing pieces assembled on the player body skeleton (p0_base) for previewing.

    Clothing meshes use palette indices: below the piece's SkinningPaletteOffset they are joints of
    the body skeleton, at/above it they are the piece's own extra bones (sleeves, skirts, hair...)."""

    def __init__(self, base_skeleton_path, piece_paths, variant_piece=None, variant=None, skin=None):
        """variant = (material files, overrides) from clothing_overrides() for the piece `variant_piece`;
        skin = skin-tone choice (0-3) applied to every piece's skin materials."""
        self.variant_piece, self.variant, self.skin = variant_piece, variant, skin
        self.path = piece_paths[0] if piece_paths else base_skeleton_path
        self.lod_count = 1
        base = Skeleton(base_skeleton_path)
        sk = Skeleton.__new__(Skeleton)
        sk.bones = list(base.bones)
        sk.joint_to_node = list(base.joint_to_node)
        sk.palette_offset = 0
        sk.index = {b.name: i for i, b in enumerate(sk.bones)}
        self.skeleton = sk
        self.materials = {}
        self.submeshes = []
        self.pieces = []
        base_j2n = np.array(base.joint_to_node or [0], dtype=np.int64)
        for piece in piece_paths:
            try:
                self._add_piece(piece, base_j2n)
            except Exception:  # noqa: BLE001 - a broken piece shouldn't stop the preview
                continue

    def _add_piece(self, trmdl, base_j2n):
        m = Model.__new__(Model)
        root = Table.root(read_file(trmdl))
        base_dir = os.path.dirname(trmdl)
        mesh_paths = [mm.string(0) for mm in root.tables(1)]
        lods = root.tables(4)
        levels = [e.u32(0) for e in lods[0].tables(0)] if lods else []
        first = levels[0] if levels and levels[0] < len(mesh_paths) else 0
        if not mesh_paths:
            return
        stem = os.path.splitext(os.path.basename(trmdl))[0]
        sk_t = root.table(2)
        local = None
        if sk_t and sk_t.string(0):
            sp = os.path.normpath(os.path.join(base_dir, sk_t.string(0)))
            if os.path.isfile(sp):
                local = Skeleton(sp)
        # add the piece's own bones, parented by name to the body (or to their local parent)
        local_map = {}
        sk = self.skeleton
        if local:
            for li, b in enumerate(local.bones):
                if b.name in sk.index:
                    local_map[li] = sk.index[b.name]
                    continue
                nb = Bone()
                nb.name, nb.t, nb.q, nb.s, nb.joint, nb.skinning = b.name, b.t, b.q, b.s, -1, True
                nb.parent_name = b.parent_name
                if b.parent_name and b.parent_name in sk.index:
                    nb.parent = sk.index[b.parent_name]
                elif 0 <= b.parent < len(local.bones) and local.bones[b.parent].name in sk.index:
                    nb.parent = sk.index[local.bones[b.parent].name]
                else:
                    nb.parent = sk.index.get('waist', 0)
                sk.bones.append(nb)
                sk.index[nb.name] = len(sk.bones) - 1
                local_map[li] = len(sk.bones) - 1
        offset = local.palette_offset if local else 0
        local_j2n = {}
        if local:
            for li, b in enumerate(local.bones):
                if b.joint >= 0:
                    local_j2n[b.joint] = local_map.get(li, 0)
        mats = {}
        files = [os.path.normpath(os.path.join(base_dir, p)) for p in root.strings(3)]
        ov = None
        if self.variant and self.variant_piece and os.path.normcase(trmdl) == os.path.normcase(self.variant_piece):
            files, ov = (self.variant[0] or files), self.variant[1]
        for mp in files:
            if os.path.isfile(mp):
                mats.update(read_materials(mp))
        if self.skin is not None:
            try:
                tp = os.path.splitext(trmdl)[0] + '.trmmt'
                for st in (read_trmmt(tp) if os.path.isfile(resolve(tp)) else []):
                    sk_mats = st['groups'].get('color_skin')
                    if sk_mats:
                        apply_overrides(mats, {m: {p: v[min(self.skin, len(v) - 1)] for p, v in pr.items() if v}
                                               for m, pr in sk_mats.items()})
                        break
            except Exception:  # noqa: BLE001
                pass
        apply_overrides(mats, ov)
        for name, mat in mats.items():
            self.materials['%s:%s' % (stem, name)] = mat
        subs = read_meshes(os.path.normpath(os.path.join(base_dir, mesh_paths[first])))
        done = set()
        for s in subs:
            s.material = '%s:%s' % (stem, s.material)
            a = s.attrs
            if id(a) not in done and a.get('JOINTS') is not None:
                done.add(id(a))
                raw = np.rint(a['JOINTS']).astype(np.int64)
                out = np.zeros_like(raw)
                body = raw < offset if offset else np.ones_like(raw, dtype=bool)
                rb = np.clip(raw, 0, len(base_j2n) - 1)
                out[body] = base_j2n[rb[body]]
                if offset:
                    for j, node in local_j2n.items():
                        out[raw == offset + j] = node
                a['JOINTS'] = out
                a['_remapped'] = 'outfit'
            s.joint_mode = 'outfit'
            self.submeshes.append(s)
        self.pieces.append(trmdl)

    def summary(self):
        tris = sum(len(s.indices) // 3 for s in self.submeshes)
        return 'Outfit: %s\n  %d parts, %d triangles, %d bones' % (
            ', '.join(os.path.basename(p) for p in self.pieces), len(self.submeshes), tris, len(self.skeleton.bones))


def default_outfit(piece_trmdl, model_pc_dir):
    """Pieces to show a clothing item on: the item plus a default face, hair, top, bottoms and shoes."""
    name = os.path.basename(os.path.dirname(piece_trmdl))
    sex = 'p2' if name.startswith('p2_') else 'p1'
    cat = name[3:6] if len(name) > 6 else ''
    slots = {'face': 'p0_face0000_00_round', 'hrs': 'p0_hrs0002_00_medium', 'upb': sex + '_upb0020_00_tshirt',
             'lwb': sex + '_lwb0000_00_skinny', 'ftw': 'p0_ftw0000_00_sneakers'}
    if cat == 'drs':
        slots.pop('upb')
        slots.pop('lwb')
    slots[cat or 'item'] = name
    out = []
    for folder in slots.values():
        d = os.path.join(model_pc_dir, folder)
        if folder == name:
            out.append(piece_trmdl)
            continue
        if os.path.isdir(d):
            cands = sorted(f for f in os.listdir(d) if f.lower().endswith('.trmdl'))
            if cands:
                out.append(os.path.join(d, cands[0]))
    return out


def material_variants(trmdl_path):
    """{label: material file or None (= the model's own)} - e.g. Normal, Shiny (_rare.trmtr)."""
    d = os.path.dirname(trmdl_path)
    stem = os.path.splitext(os.path.basename(trmdl_path))[0]
    out = {'Normal': None}
    try:
        files = sorted(f for f in os.listdir(d) if f.lower().endswith('.trmtr'))
    except OSError:
        return out
    for f in files:
        s = os.path.splitext(f)[0]
        if s == stem:
            continue
        tag = s[len(stem) + 1:] if s.startswith(stem + '_') else s
        label = {'rare': 'Shiny'}.get(tag, tag.replace('_', ' ').title())
        out[label] = os.path.join(d, f)
    return out


def apply_overrides(materials, overrides, prefix=''):
    """Set int params (e.g. BaseColorIndex1) per material name: {material: {param: value}}."""
    for mat, params in (overrides or {}).items():
        m = materials.get(prefix + mat)
        if m is not None:
            m.ints.update(params)


class Model:
    def __init__(self, trmdl_path, material_paths=None, overrides=None):
        self.path = trmdl_path
        base = os.path.dirname(trmdl_path)
        root = Table.root(read_file(trmdl_path))
        mesh_paths = [m.string(0) for m in root.tables(1)]
        # LODs[0].Entries lists the mesh file of each detail level; the first one is full detail
        lods = root.tables(4)
        levels = [e.u32(0) for e in lods[0].tables(0)] if lods else []
        first = levels[0] if levels and levels[0] < len(mesh_paths) else 0
        self.mesh_paths = [os.path.normpath(os.path.join(base, mesh_paths[first]))] if mesh_paths else []
        self.lod_count = max(len(levels), 1)
        sk = root.table(2)
        skp = sk.string(0) if sk else ''
        self.skeleton_path = os.path.normpath(os.path.join(base, skp)) if skp else None
        self.material_paths = [os.path.normpath(os.path.join(base, p)) for p in root.strings(3)]
        if material_paths:
            self.material_paths = list(material_paths)
        self.skeleton = None
        if self.skeleton_path and os.path.isfile(self.skeleton_path):
            try:
                self.skeleton = Skeleton(self.skeleton_path)
            except Exception:  # noqa: BLE001
                self.skeleton = None
        self.materials = {}
        for p in self.material_paths:
            if os.path.isfile(p):
                try:
                    self.materials.update(read_materials(p))
                except Exception:  # noqa: BLE001
                    pass
        apply_overrides(self.materials, overrides)
        self.submeshes = []
        for p in self.mesh_paths:
            if os.path.isfile(p):
                self.submeshes.extend(read_meshes(p))
        for sm in self.submeshes:
            remap_joints(sm, self.skeleton)

    def summary(self):
        tris = sum(len(s.indices) // 3 for s in self.submeshes)
        verts = sum(len(a['POSITION']) for a in {id(s.attrs): s.attrs for s in self.submeshes}.values())
        lines = ['Model: %s' % os.path.basename(self.path),
                 '  %d parts, %d vertices, %d triangles  (full-detail LOD of %d)'
                 % (len(self.submeshes), verts, tris, self.lod_count),
                 '  skeleton: %s (%d bones)' % (os.path.basename(self.skeleton_path or '-'),
                                               len(self.skeleton.bones) if self.skeleton else 0),
                 '', 'Materials:']
        for name, m in self.materials.items():
            lines.append('  %s   [%s]' % (name, m.shader))
            for k, v in m.textures.items():
                lines.append('      %-22s %s%s' % (k, os.path.basename(v), '' if os.path.isfile(v) else '   (missing)'))
        lines += ['', 'Parts (mesh / material / triangles / data / skinning):']
        for s in self.submeshes:
            lines.append('  %-40s %-22s %6d tris  %s  [%s]' % (
                s.name, s.material, len(s.indices) // 3,
                ', '.join(k for k in s.attrs if not k.startswith('_') and s.attrs[k] is not None),
                getattr(s, 'joint_mode', '-')))
        return '\n'.join(lines)


# ---------------------------------------------------------------------------
# animation
# ---------------------------------------------------------------------------

def unpack_quats(packed):
    """packed: (n, 3) uint16 -> (n, 4) float xyzw (GFTool QuaternionExtensions.Unpack)."""
    p = packed.astype(np.uint64)
    pack = (p[:, 2] << np.uint64(32)) | (p[:, 1] << np.uint64(16)) | p[:, 0]
    scale = (math.pi / 2.0) / 0x7FFF

    def expand(v):
        return v.astype(np.float64) * scale - math.pi / 4.0
    q1 = expand((pack >> np.uint64(3)) & np.uint64(0x7FFF))
    q2 = expand((pack >> np.uint64(18)) & np.uint64(0x7FFF))
    q3 = expand((pack >> np.uint64(33)) & np.uint64(0x7FFF))
    mx = np.sqrt(np.clip(1.0 - (q1 * q1 + q2 * q2 + q3 * q3), 0.0, None))
    missing = (pack & np.uint64(3)).astype(np.int64)
    neg = (pack & np.uint64(4)) != 0
    vals = np.stack([q1, q2, q3], 1)
    out = np.zeros((len(p), 4))
    for m in range(4):
        sel = missing == m
        if not sel.any():
            continue
        v = vals[sel]
        cols = [v[:, 0], v[:, 1], v[:, 2]]
        cols.insert(m, mx[sel])
        out[sel] = np.stack(cols, 1)         # order: x, y, z, w
    out[neg] *= -1
    n = np.linalg.norm(out, axis=1, keepdims=True)
    out = np.where(n > 0, out / np.where(n > 0, n, 1), np.array([0, 0, 0, 1.0]))
    # keep successive keys in the same hemisphere so interpolation takes the short path
    for i in range(1, len(out)):
        if np.dot(out[i], out[i - 1]) < 0:
            out[i] = -out[i]
    return out.astype(np.float32)


def _track(tbl, slot, kind):
    """Union channel at FlatSharp item `slot`: type at slot, value at slot+1.

    Returns (frames or None, values) where values are (n,3) vectors or (n,4) quaternions."""
    ttype = tbl.scalar(slot, 'B', 0)
    t = tbl.table(slot + 1)
    if not ttype or t is None:
        return None
    if ttype == 1:                                          # fixed
        p = t.struct_pos(0)
        if p is None:
            return None
        if kind == 'rot':
            v = unpack_quats(np.frombuffer(tbl.b, '<u2', 3, p).reshape(1, 3))
        else:
            v = np.array([struct.unpack_from('<3f', tbl.b, p)], np.float32)
        return None, v
    if ttype == 2:                                          # one value per frame
        v = t.array(0, '<u2', 3) if kind == 'rot' else t.array(0, '<f4', 3)
        v = unpack_quats(v) if kind == 'rot' else v.astype(np.float32)
        return np.arange(len(v), dtype=np.float32), v
    if ttype in (3, 4):                                     # keyed (16 / 8 bit frame numbers)
        keys = t.array(0, '<u2' if ttype == 3 else '<u1').astype(np.float32)
        v = t.array(1, '<u2', 3) if kind == 'rot' else t.array(1, '<f4', 3)
        v = unpack_quats(v) if kind == 'rot' else v.astype(np.float32)
        n = min(len(keys), len(v))
        return keys[:n], v[:n]
    return None


class Animation:
    def __init__(self, path):
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        root = Table.root(read_file(path))
        info = root.table(0)
        self.frames = info.u32(1) if info else 0
        self.fps = (info.u32(2) if info else 0) or 30
        self.loop = bool(info.u32(0)) if info else False
        self.tracks = {}
        skel = root.table(1)
        if skel is None:
            return
        for tr in skel.tables(0):
            bone = tr.string(0)
            ch = {}
            for slot, key, kind in ((1, 's', 'vec'), (3, 'r', 'rot'), (5, 't', 'vec')):
                try:
                    r = _track(tr, slot, kind)
                except Exception:  # noqa: BLE001
                    r = None
                if r is not None:
                    ch[key] = r
            self.tracks[bone] = ch

    @property
    def duration(self):
        return max(self.frames - 1, 1) / self.fps

    def channels_for(self, skeleton):
        """[(node index, path, times(s), values)] for glTF / three.js."""
        out = []
        last = max(self.frames - 1, 0)
        for bone, ch in self.tracks.items():
            ni = skeleton.index.get(bone) if skeleton else None
            if ni is None:
                continue
            for key, path in (('t', 'translation'), ('r', 'rotation'), ('s', 'scale')):
                if key not in ch:
                    continue
                frames, vals = ch[key]
                if frames is None:                       # fixed value: hold for the whole clip
                    times = np.array([0.0, last / self.fps], np.float32) if last else np.array([0.0], np.float32)
                    vals = np.repeat(vals, len(times), 0)
                else:
                    times = frames / self.fps
                out.append((ni, path, times.astype(np.float32), vals.astype(np.float32)))
        return out


# ---------------------------------------------------------------------------
# glTF writer
# ---------------------------------------------------------------------------

class _Glb:
    def __init__(self):
        self.j = {'asset': {'version': '2.0', 'generator': 'Lumiose Toolkit'}, 'scene': 0,
                  'scenes': [{'nodes': []}], 'nodes': [], 'meshes': [], 'materials': [],
                  'accessors': [], 'bufferViews': [], 'buffers': [], 'textures': [],
                  'images': [], 'samplers': [{'magFilter': 9729, 'minFilter': 9987,
                                             'wrapS': 10497, 'wrapT': 10497}],
                  'skins': [], 'animations': []}
        self.bin = io.BytesIO()

    def view(self, data, target=None):
        while self.bin.tell() % 4:
            self.bin.write(b'\0')
        off = self.bin.tell()
        self.bin.write(data)
        v = {'buffer': 0, 'byteOffset': off, 'byteLength': len(data)}
        if target:
            v['target'] = target
        self.j['bufferViews'].append(v)
        return len(self.j['bufferViews']) - 1

    def accessor(self, arr, atype, ctype=5126, target=None, minmax=False, normalized=False):
        arr = np.ascontiguousarray(arr)
        bv = self.view(arr.tobytes(), target)
        a = {'bufferView': bv, 'componentType': ctype, 'count': int(arr.shape[0]), 'type': atype}
        if normalized:
            a['normalized'] = True
        if minmax:
            flat = arr.reshape(arr.shape[0], -1).astype(np.float64)
            a['min'] = [float(x) for x in flat.min(0)]
            a['max'] = [float(x) for x in flat.max(0)]
        self.j['accessors'].append(a)
        return len(self.j['accessors']) - 1

    def image(self, png_bytes, name, wrap=(10497, 10497)):
        bv = self.view(png_bytes)
        self.j['images'].append({'bufferView': bv, 'mimeType': 'image/png', 'name': name})
        return self.texture(len(self.j['images']) - 1, name, wrap)

    def texture(self, image_index, name, wrap=(10497, 10497)):
        sampler = {'magFilter': 9729, 'minFilter': 9987, 'wrapS': wrap[0], 'wrapT': wrap[1]}
        if sampler in self.j['samplers']:
            si = self.j['samplers'].index(sampler)
        else:
            self.j['samplers'].append(sampler)
            si = len(self.j['samplers']) - 1
        self.j['textures'].append({'sampler': si, 'source': image_index, 'name': name})
        return len(self.j['textures']) - 1

    def tex_info(self, index, transform=None, **extra):
        info = {'index': index}
        info.update(extra)
        if transform:
            info['extensions'] = {'KHR_texture_transform': {'offset': transform[0], 'scale': transform[1]}}
            used = self.j.setdefault('extensionsUsed', [])
            if 'KHR_texture_transform' not in used:
                used.append('KHR_texture_transform')
        return info

    def finish(self):
        for k in [k for k, v in self.j.items() if isinstance(v, list) and not v and k != 'scenes']:
            del self.j[k]
        while self.bin.tell() % 4:
            self.bin.write(b'\0')
        data = self.bin.getvalue()
        self.j['buffers'] = [{'byteLength': len(data)}]
        js = json.dumps(self.j, separators=(',', ':')).encode()
        js += b' ' * ((4 - len(js) % 4) % 4)
        out = io.BytesIO()
        out.write(struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(data)))
        out.write(struct.pack('<II', len(js), 0x4E4F534A))
        out.write(js)
        out.write(struct.pack('<II', len(data), 0x004E4942))
        out.write(data)
        return out.getvalue()


def _texture_png(path, rebuild_normal=False, max_size=2048):
    try:
        b = zf.Bntx(read_file(path))
        img = b.textures[0].decode(0, 0, rebuild_normal_z=rebuild_normal)
    except Exception:  # noqa: BLE001
        return None
    if max(img.size) > max_size:
        s = max_size / max(img.size)
        img = resize_channels(img, (max(1, int(img.size[0] * s)), max(1, int(img.size[1] * s))))
    bio = io.BytesIO()
    img.save(bio, 'PNG')
    return bio.getvalue()


def export_glb(model, animations=(), out_path=None, texture_size=2048, with_textures=True, bake_colors=True):
    """Build a .glb from a Model (+ optional Animation list). Returns bytes (and writes out_path)."""
    g = _Glb()
    skel = model.skeleton
    root_node = {'name': os.path.splitext(os.path.basename(model.path))[0], 'children': []}
    g.j['nodes'].append(root_node)
    g.j['scenes'][0]['nodes'].append(0)

    joint_nodes = []
    skin = None
    if skel and skel.bones:
        base = len(g.j['nodes'])
        for b in skel.bones:
            g.j['nodes'].append({'name': b.name, 'translation': [float(x) for x in b.t],
                                 'rotation': [float(x) for x in b.q], 'scale': [float(x) for x in b.s]})
        for i, b in enumerate(skel.bones):
            if 0 <= b.parent < len(skel.bones) and b.parent != i:
                g.j['nodes'][base + b.parent].setdefault('children', []).append(base + i)
            else:
                root_node['children'].append(base + i)
        joint_nodes = list(range(base, base + len(skel.bones)))
        inv = np.array([np.linalg.inv(m).T for m in skel.world_matrices()], np.float32)  # column-major
        acc = g.accessor(inv.reshape(-1, 16), 'MAT4')
        g.j['skins'].append({'joints': joint_nodes, 'inverseBindMatrices': acc, 'skeleton': joint_nodes[0]})
        skin = 0

    img_cache, mat_index = {}, {}

    def tex(m, key, normal=False):
        """glTF texture index for material m's texture `key` (image shared, sampler per wrap mode)."""
        path = m.textures.get(key) if key else None
        if not with_textures or not path or not os.path.isfile(path):
            return None
        if (path, normal) not in img_cache:
            png = _texture_png(path, rebuild_normal=normal, max_size=texture_size)
            if not png:
                img_cache[(path, normal)] = None
            else:
                bv = g.view(png)
                g.j['images'].append({'bufferView': bv, 'mimeType': 'image/png', 'name': os.path.basename(path)})
                img_cache[(path, normal)] = len(g.j['images']) - 1
        ii = img_cache[(path, normal)]
        return None if ii is None else g.texture(ii, os.path.basename(path), m.wrap.get(key, (10497, 10497)))

    def material(name):
        if name in mat_index:
            return mat_index[name]
        m = model.materials.get(name)
        mat = {'name': name, 'pbrMetallicRoughness': {'baseColorFactor': [1, 1, 1, 1],
                                                     'metallicFactor': 0.0, 'roughnessFactor': 0.8},
               'doubleSided': True}
        if m:
            uvx = m.uv_transform()
            bc_key = m.texture_key('BaseColorMap', 'BaseColorMap0', 'AlbedoMap', suffixes=('_alb.bntx',))
            t = None
            if with_textures and bake_colors:
                img = baked_base_color(m, texture_size)
                if img is not None:
                    bio = io.BytesIO()
                    img.save(bio, 'PNG')
                    t = g.image(bio.getvalue(), name + '_basecolor', m.wrap.get(bc_key or 'LayerMaskMap', (10497, 10497)))
            if t is None:
                t = tex(m, bc_key)
            if t is not None:
                mat['pbrMetallicRoughness']['baseColorTexture'] = g.tex_info(t, uvx)
            nk = m.texture_key('NormalMap', 'NormalMap0', suffixes=('_nrm.bntx',))
            t = tex(m, nk, normal=True)
            if t is not None:
                mat['normalTexture'] = g.tex_info(t, m.uv_transform(normal=True))
            ak = m.texture_key('AOMap', 'OcclusionMap', suffixes=('_ao.bntx',))
            t = tex(m, ak)
            if t is not None:
                mat['occlusionTexture'] = g.tex_info(t, uvx)
            if m.alpha and m.alpha.lower() not in ('', 'opaque'):
                mat['alphaMode'] = 'MASK' if 'test' in m.alpha.lower() or 'mask' in m.alpha.lower() else 'BLEND'
        g.j['materials'].append(mat)
        mat_index[name] = len(g.j['materials']) - 1
        return mat_index[name]

    shared = {}            # id(attrs) -> (accessor dict, gltf mesh index)
    for sm in model.submeshes:
        a = sm.attrs
        n = len(a['POSITION'])
        if n == 0 or sm.indices is None or len(sm.indices) < 3:
            continue
        if id(a) in shared:
            attrs, mi = shared[id(a)]
            idx = g.accessor(sm.indices.astype(np.uint32), 'SCALAR', ctype=5125, target=34963)
            g.j['meshes'][mi]['primitives'].append({'attributes': attrs, 'indices': idx,
                                                    'material': material(sm.material)})
            continue
        attrs = {'POSITION': g.accessor(a['POSITION'], 'VEC3', target=34962, minmax=True)}
        if a.get('NORMAL') is not None:
            nrm = a['NORMAL']
            ln = np.linalg.norm(nrm, axis=1, keepdims=True)
            nrm = np.where(ln > 1e-6, nrm / np.where(ln > 1e-6, ln, 1), np.array([0, 1, 0], np.float32))
            attrs['NORMAL'] = g.accessor(nrm.astype(np.float32), 'VEC3', target=34962)
        for k in ('TEXCOORD_0', 'TEXCOORD_1'):
            if a.get(k) is not None:
                attrs[k] = g.accessor(a[k], 'VEC2', target=34962)
        if a.get('COLOR_0') is not None:
            attrs['COLOR_0'] = g.accessor(np.clip(a['COLOR_0'], 0, 1).astype(np.float32), 'VEC4', target=34962)
        if skin is not None and a.get('JOINTS') is not None:
            j = a['JOINTS'].astype(np.uint16)
            w = a['WEIGHTS'].astype(np.float32).copy()
            w[w < 0] = 0
            s = w.sum(1, keepdims=True)
            w = np.where(s > 0, w / np.where(s > 0, s, 1), np.eye(1, w.shape[1], dtype=np.float32))
            for k in range(j.shape[1] // 4):
                attrs['JOINTS_%d' % k] = g.accessor(j[:, 4 * k:4 * k + 4], 'VEC4', ctype=5123, target=34962)
                attrs['WEIGHTS_%d' % k] = g.accessor(w[:, 4 * k:4 * k + 4], 'VEC4', target=34962)
        idx = g.accessor(sm.indices.astype(np.uint32), 'SCALAR', ctype=5125, target=34963)
        g.j['meshes'].append({'name': sm.name, 'primitives': [{'attributes': attrs, 'indices': idx,
                                                               'material': material(sm.material)}]})
        shared[id(a)] = (attrs, len(g.j['meshes']) - 1)
        node = {'name': 'mesh:' + sm.name, 'mesh': len(g.j['meshes']) - 1}   # never clash with bone names
        if skin is not None and 'JOINTS_0' in attrs:
            node['skin'] = skin
        g.j['nodes'].append(node)
        root_node['children'].append(len(g.j['nodes']) - 1)

    for anim in animations:
        chans = anim.channels_for(skel)
        if not chans:
            continue
        samplers, channels = [], []
        for ni, path, times, vals in chans:
            ti = g.accessor(times.reshape(-1, 1), 'SCALAR', minmax=True)
            vi = g.accessor(vals, 'VEC4' if path == 'rotation' else 'VEC3')
            samplers.append({'input': ti, 'output': vi, 'interpolation': 'LINEAR'})
            channels.append({'sampler': len(samplers) - 1, 'target': {'node': joint_nodes[ni], 'path': path}})
        g.j['animations'].append({'name': anim.name, 'samplers': samplers, 'channels': channels})

    data = g.finish()
    if out_path:
        with open(out_path, 'wb') as f:
            f.write(data)
    return data


def find_model_for(path):
    """Best .trmdl for a file (an animation, mesh, material...) - same folder, else parent folders."""
    d = os.path.dirname(path)
    for _ in range(3):
        cands = sorted(f for f in os.listdir(d) if f.lower().endswith('.trmdl')) if os.path.isdir(d) else []
        if cands:
            stem = os.path.basename(d)
            best = next((c for c in cands if os.path.splitext(c)[0] == stem), cands[0])
            return os.path.join(d, best)
        d = os.path.dirname(d)
    return None


def related_animations(model_path):
    d = os.path.dirname(model_path)
    try:
        return sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith('.tranm'))
    except OSError:
        return []
