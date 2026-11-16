"""EXPERIMENTAL: replace a Trinity model's geometry with meshes from a glTF/GLB file.

Workflow: export a model with Lumiose Toolkit (.glb), edit it in Blender (keep the mesh names and the
armature), export .glb again, then import it here. Each game mesh is replaced by the glTF mesh with
the same name ("pm0025_00_00_body_mesh_shape" or "mesh:<name>"); vertices are re-encoded in the
mesh's original vertex format, bone weights are mapped back by bone name, and material parts are
matched by material name. Writes .trmsh/.trmbf (and .trmdl with LODs pointing at the new mesh)
into a mod folder. Shape keys (morphs) of replaced meshes are dropped. Tangents are recomputed from
the game's UVs; each vertex keeps its strongest bone influences (as many as the format holds).
A file whose mesh names match none of the model's meshes is refused instead of written.
"""
import base64
import json
import os
import struct

import numpy as np

import za_modding as zm
import za_trinity as zt

COMP = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NCOMP = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}
FORMAT_ID = {'R8_G8_B8_A8_UNSIGNED_NORMALIZED': 20, 'W8_X8_Y8_Z8_UNSIGNED': 22, 'X32_UNSIGNED': 36,
             'X32_SIGNED': 37, 'W16_X16_Y16_Z16_UNSIGNED_NORMALIZED': 39, 'W16_X16_Y16_Z16_FLOAT': 43,
             'X32_Y32_FLOAT': 48, 'X32_Y32_Z32_FLOAT': 51, 'W32_X32_Y32_Z32_UNSIGNED': 52,
             'W32_X32_Y32_Z32_FLOAT': 54}


class ImportError_(Exception):
    pass


class Gltf:
    def __init__(self, path):
        with open(path, 'rb') as f:
            data = f.read()
        self.bin = b''
        if data[:4] == b'glTF':
            jl = struct.unpack_from('<I', data, 12)[0]
            self.j = json.loads(data[20:20 + jl])
            off = 20 + jl
            if off + 8 <= len(data):
                bl = struct.unpack_from('<I', data, off)[0]
                self.bin = data[off + 8:off + 8 + bl]
            buffers = [self.bin]
        else:
            self.j = json.loads(data.decode('utf-8'))
            buffers = []
            for b in self.j.get('buffers', []):
                uri = b.get('uri', '')
                if uri.startswith('data:'):
                    buffers.append(base64.b64decode(uri.split(',', 1)[1]))
                else:
                    with open(os.path.join(os.path.dirname(path), uri), 'rb') as f:
                        buffers.append(f.read())
        self.buffers = buffers

    def accessor(self, i):
        a = self.j['accessors'][i]
        n, k = a['count'], NCOMP[a['type']]
        dt = np.dtype(COMP[a['componentType']])
        if 'bufferView' not in a:
            return np.zeros((n, k), np.float32)
        bv = self.j['bufferViews'][a['bufferView']]
        buf = self.buffers[bv.get('buffer', 0)]
        start = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
        stride = bv.get('byteStride', 0) or dt.itemsize * k
        raw = np.frombuffer(buf, np.uint8, count=stride * (n - 1) + dt.itemsize * k, offset=start)
        rows = np.lib.stride_tricks.as_strided(raw, shape=(n, dt.itemsize * k), strides=(stride, 1))
        out = np.ascontiguousarray(rows).view(dt).reshape(n, k).astype(np.float64)
        if a.get('normalized'):
            out /= float(np.iinfo(dt).max)
        return out


def _encode(usage, fmt, v, n):
    """Inverse of za_trinity._decode: returns an (n, size) uint8 array."""
    def f32(a):
        return np.ascontiguousarray(a, np.float32).view(np.uint8).reshape(n, -1)

    def f16(a):
        return np.ascontiguousarray(a, np.float16).view(np.uint8).reshape(n, -1)

    def u16(a):
        return np.ascontiguousarray(np.clip(np.rint(a), 0, 65535), np.uint16).view(np.uint8).reshape(n, -1)

    def u8(a):
        return np.clip(np.rint(a), 0, 255).astype(np.uint8)

    def pad(a, k, fill=0.0):
        if a.shape[1] >= k:
            return a[:, :k]
        return np.concatenate([a, np.full((n, k - a.shape[1]), fill)], 1)
    U = usage
    if fmt == 51:
        return f32(pad(v, 3))
    if fmt == 54:
        if U == 'TANGENT':
            t = pad(v, 4, 1.0)
            return f32(t[:, [3, 0, 1, 2]])
        if U == 'BLEND_INDEX':
            raise ImportError_('unsupported float blend indices')
        return f32(np.concatenate([np.full((n, 1), 1.0 if U == 'POSITION' else 0.0), pad(v, 3)], 1))
    if fmt == 48:
        return f32(pad(v, 2))
    if fmt == 52:
        i = pad(v, 4)
        return np.ascontiguousarray(i[:, [3, 0, 1, 2]], np.uint32).view(np.uint8).reshape(n, -1)
    if fmt == 43:
        if U in ('POSITION', 'NORMAL', 'BINORMAL'):
            return f16(pad(pad(v, 3), 4, 0.0))
        if U == 'TEX_COORD':
            return f16(pad(pad(v, 2), 4, 0.0))
        return f16(pad(v, 4, 1.0))
    if fmt == 39:
        if U == 'BLEND_INDEX':
            return u16(pad(v, 4)[:, [3, 0, 1, 2]])
        if U == 'BLEND_WEIGHTS':
            return u16(pad(v, 4)[:, [3, 0, 1, 2]] * 65535)
        if U in ('NORMAL', 'BINORMAL'):
            return u16(pad((pad(v, 3) + 1) / 2 * 65535, 4))
        if U == 'TANGENT':
            return u16((pad(v, 4, 1.0) + 1) / 2 * 65535)
        return u16(pad(pad(v, 3 if U == 'POSITION' else 2), 4) * 65535)
    if fmt in (20, 22):
        if U == 'BLEND_INDEX':
            return u8(pad(v, 4)[:, [3, 0, 1, 2]])
        if U == 'BLEND_WEIGHTS':
            return u8(pad(v, 4)[:, [3, 0, 1, 2]] * 255)
        if U in ('NORMAL', 'BINORMAL'):
            return u8(pad((pad(v, 3) + 1) / 2 * 255, 4))
        if U == 'TANGENT':
            return u8((pad(v, 4, 1.0) + 1) / 2 * 255)
        if U == 'COLOR':
            return u8(pad(v, 4, 1.0) * 255)
        return u8(pad(pad(v, 3 if U == 'POSITION' else 2), 4) * 255)
    raise ImportError_('vertex format %d is not supported for import' % fmt)


def _fmt_size(fmt):
    return zt.FMT_SIZE.get(fmt)


def compute_tangents(P, N, UV, idx):
    """Per-vertex tangents (x, y, z, handedness) from positions, normals and the game's UVs.
    Checked against the game's own tangents: 97-100% agree in direction and handedness. glTF tangents
    are not used: Blender computes them for flipped UVs, so their handedness is wrong for the game."""
    n = len(P)
    t1, t2 = np.zeros((n, 3)), np.zeros((n, 3))
    tri = idx[:len(idx) - len(idx) % 3].reshape(-1, 3)
    if len(tri):
        e1, e2 = P[tri[:, 1]] - P[tri[:, 0]], P[tri[:, 2]] - P[tri[:, 0]]
        d1, d2 = UV[tri[:, 1]] - UV[tri[:, 0]], UV[tri[:, 2]] - UV[tri[:, 0]]
        det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
        r = np.where(np.abs(det) > 1e-12, 1.0 / np.where(det == 0, 1, det), 0.0)[:, None]
        sdir = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) * r
        tdir = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) * r
        for k in range(3):
            np.add.at(t1, tri[:, k], sdir)
            np.add.at(t2, tri[:, k], tdir)
    t = t1 - N * (N * t1).sum(1, keepdims=True)              # Gram-Schmidt against the normal
    ln = np.linalg.norm(t, axis=1, keepdims=True)
    fallback = np.cross(N, [0.0, 0.0, 1.0])                  # vertices without usable UVs
    fallback[np.linalg.norm(fallback, axis=1) < 1e-6] = [1.0, 0.0, 0.0]
    fallback /= np.linalg.norm(fallback, axis=1, keepdims=True)
    t = np.where(ln > 1e-9, t / np.where(ln > 0, ln, 1), fallback)
    w = np.where((np.cross(N, t) * t2).sum(1) < 0, -1.0, 1.0)[:, None]
    return np.concatenate([t, w], 1)


def strongest_influences(J, W, slots):
    """Sort each vertex's bone influences strongest first, keep `slots` of them and make the kept
    weights add up to 1 (the game stores unused slots as 0 at the end)."""
    order = np.argsort(-W, axis=1, kind='stable')
    J = np.take_along_axis(J, order, 1)
    W = np.take_along_axis(W, order, 1)
    if J.shape[1] < slots:
        J = np.pad(J, ((0, 0), (0, slots - J.shape[1])))
        W = np.pad(W, ((0, 0), (0, slots - W.shape[1])))
    J, W = J[:, :slots].copy(), W[:, :slots].copy()
    J[W <= 0] = 0
    s = W.sum(1, keepdims=True)
    W = np.where(s > 0, W / np.where(s > 0, s, 1), np.eye(1, slots))
    return J, W


def import_gltf(glb_path, trmdl_path, mod, log=print):
    """Replace the geometry of trmdl_path's full-detail mesh with glb_path; write into `mod`.
    Returns a list of report lines."""
    root_dir = os.path.dirname(trmdl_path)
    g = Gltf(glb_path)
    orig = zt.Model(trmdl_path)                       # for skeleton + joint modes of the original
    skel = orig.skeleton
    mode_by_mesh = {}
    for s in orig.submeshes:
        mode_by_mesh.setdefault(s.name, getattr(s, 'joint_mode', 'static'))
    trmsh_path = orig.mesh_paths[0]
    msh = json.loads(zm.to_json(trmsh_path))
    trmbf_path = os.path.join(os.path.dirname(trmsh_path), msh['bufferFilePath'])
    mbf = json.loads(zm.to_json(trmbf_path))
    report = []

    # glTF mesh lookup by name (mesh name or node name, with or without our "mesh:" prefix)
    gmesh = {}
    for ni, node in enumerate(g.j.get('nodes', [])):
        if 'mesh' in node:
            for nm in (node.get('name', ''), g.j['meshes'][node['mesh']].get('name', '')):
                nm = nm[5:] if nm.startswith('mesh:') else nm
                if nm:
                    gmesh.setdefault(nm, (node['mesh'], node.get('skin')))
    gmat = [m.get('name', '') for m in g.j.get('materials', [])]
    replaced = 0

    for mi, mesh in enumerate(msh['Meshes']):
        name = mesh.get('Name', '')
        hit = gmesh.get(name) or gmesh.get(name.replace('_shape', ''))
        if not hit:
            report.append('kept original: %s (no glTF mesh with that name)' % name)
            continue
        gi, skin_i = hit
        prims = g.j['meshes'][gi]['primitives']
        decl = mesh['vertexDeclaration'][0]
        elements = decl['vertexElements']
        sizes = [s.get('elementSize', 0) for s in decl['vertexElementSizes']]
        part_names = [p.get('MaterialName', '') for p in mesh['meshParts']]
        # joint node index (glTF skin) -> skeleton bone index
        joint_to_bone = None
        if skin_i is not None and skel:
            joints = g.j['skins'][skin_i]['joints']
            joint_to_bone = np.array([skel.index.get(g.j['nodes'][j].get('name', ''), 0) for j in joints] or [0])
            unknown = sorted({g.j['nodes'][j].get('name', '') for j in joints} - set(skel.index))
            if unknown:
                report.append('%s: bones not in the game skeleton (weights on them move with the root): %s'
                              % (name, ', '.join(unknown[:8]) + (' …' if len(unknown) > 8 else '')))
        # concatenate primitives
        P, N, T, UV0, UV1, C, J, W, idx_by_part = [], [], [], [], [], [], [], [], {p: [] for p in part_names}
        base = 0
        shared = {}                       # primitives that share one vertex set (same accessors)
        for pr in prims:
            at = pr['attributes']
            key = tuple(sorted(at.items()))
            if key in shared:
                ind = g.accessor(pr['indices'])[:, 0].astype(np.int64) if 'indices' in pr else np.arange(shared[key][1])
                mname = gmat[pr['material']] if 'material' in pr and pr['material'] < len(gmat) else ''
                mname = mname.split('.')[0]
                target = mname if mname in idx_by_part else part_names[0]
                idx_by_part[target].append(ind + shared[key][0])
                continue
            pos = g.accessor(at['POSITION'])
            shared[key] = (base, len(pos))
            n = len(pos)
            P.append(pos)
            N.append(g.accessor(at['NORMAL']) if 'NORMAL' in at else np.tile([0, 1, 0], (n, 1)))
            T.append(g.accessor(at['TANGENT']) if 'TANGENT' in at else np.tile([1, 0, 0, 1], (n, 1)))
            uv0 = g.accessor(at['TEXCOORD_0']) if 'TEXCOORD_0' in at else np.zeros((n, 2))
            UV0.append(uv0)
            UV1.append(g.accessor(at['TEXCOORD_1']) if 'TEXCOORD_1' in at else uv0)
            C.append(g.accessor(at['COLOR_0'])[:, :4] if 'COLOR_0' in at else np.ones((n, 4)))
            js, ws = [], []
            for k in range(2):
                if 'JOINTS_%d' % k in at:
                    js.append(g.accessor(at['JOINTS_%d' % k]))
                    ws.append(g.accessor(at['WEIGHTS_%d' % k]))
            if js:
                J.append(np.concatenate(js, 1))
                W.append(np.concatenate(ws, 1))
            else:
                J.append(np.zeros((n, 4)))
                W.append(np.tile([1, 0, 0, 0], (n, 1)))
            ind = g.accessor(pr['indices'])[:, 0].astype(np.int64) if 'indices' in pr else np.arange(n)
            mname = gmat[pr['material']] if 'material' in pr and pr['material'] < len(gmat) else ''
            mname = mname.split('.')[0]                   # Blender adds .001 suffixes
            target = mname if mname in idx_by_part else part_names[0]
            if mname not in idx_by_part:
                report.append('%s: material "%s" not in the original - put into part "%s"' % (name, mname, target))
            idx_by_part[target].append(ind + base)
            base += n
        nv = base
        P, N, T = np.concatenate(P), np.concatenate(N), np.concatenate(T)
        UV0, UV1, C = np.concatenate(UV0), np.concatenate(UV1), np.concatenate(C)
        J, W = np.concatenate([np.pad(j, ((0, 0), (0, 8 - j.shape[1]))) for j in J]), \
            np.concatenate([np.pad(w, ((0, 0), (0, 8 - w.shape[1]))) for w in W])
        # UVs back to the game's orientation
        UV0 = UV0.copy()
        UV0[:, 1] = 1 - UV0[:, 1]
        UV1 = UV1.copy()
        UV1[:, 1] = 1 - UV1[:, 1]
        ln = np.linalg.norm(N, axis=1, keepdims=True)
        N = N / np.where(ln > 0, ln, 1)
        all_tris = np.concatenate([np.concatenate(v) for v in idx_by_part.values() if v] or [np.zeros(0, np.int64)])
        T = compute_tangents(P, N, UV0, all_tris)
        B = np.cross(N, T[:, :3]) * T[:, 3:4]
        # bone influences: strongest first, as many as the vertex format holds (4 per BLEND_INDEX element)
        slots = 4 * max(1, sum(1 for el in elements if el.get('vertexUsage') == 'BLEND_INDEX'))
        dropped = int((np.sort(W, 1)[:, ::-1][:, slots:] > 0.01).any(1).sum()) if W.shape[1] > slots else 0
        if dropped:
            report.append('%s: %d vertices had more than %d bone influences - kept the %d strongest'
                          % (name, dropped, slots, slots))
        J, W = strongest_influences(J, W, slots)
        # bone weights -> raw blend indices in this mesh's index space
        mode = mode_by_mesh.get(name, 'static')
        raw = np.zeros_like(J)
        bw_table = mesh.setdefault('boneWeight', [])
        if joint_to_bone is not None and mode not in ('static', 'static (external skeleton)'):
            bones = joint_to_bone[np.clip(J.astype(np.int64), 0, len(joint_to_bone) - 1)]
            if mode == 'joint':
                lut = np.array([max(b.joint, 0) for b in skel.bones])
                raw = lut[bones]
            elif mode.startswith('boneweights'):
                key = (lambda b: skel.bones[b].joint) if mode == 'boneweights+joint' else (lambda b: b)
                pos_of = {bw.get('RigIndex', 0): k for k, bw in enumerate(bw_table)}
                flat = bones.reshape(-1)
                out = np.zeros_like(flat)
                for k, b in enumerate(flat):
                    r = key(int(b))
                    if r not in pos_of:
                        pos_of[r] = len(bw_table)
                        bw_table.append({'RigIndex': int(r), 'RigWeight': 1.0})
                    out[k] = pos_of[r]
                raw = out.reshape(bones.shape)
            else:
                raw = bones
            W = W / np.where(W.sum(1, keepdims=True) > 0, W.sum(1, keepdims=True), 1)
        # vertex buffers per layer
        layers = {}
        bi = wi = ui = 0
        for el in elements:
            usage = el.get('vertexUsage', 'NONE')
            fmt = FORMAT_ID.get(el.get('vertexFormat'), el.get('vertexFormat'))
            layer = el.get('vertexElementLayer', 0)
            si = el.get('vertexElementSizeIndex', 0)
            stride = sizes[si if si >= 0 else 0]
            off = el.get('vertexElementOffset', 0)
            size = _fmt_size(fmt)
            buf = layers.setdefault(layer, np.zeros((nv, stride), np.uint8))
            if usage == 'POSITION':
                v = P
            elif usage == 'NORMAL':
                v = N
            elif usage == 'TANGENT':
                v = T
            elif usage == 'BINORMAL':
                v = B
            elif usage == 'TEX_COORD':
                v = UV0 if ui == 0 else UV1
                ui += 1
            elif usage == 'COLOR':
                v = C
            elif usage == 'BLEND_INDEX':
                # _encode stores (x, y, z, w) as slots (w, x, y, z); pre-rotate so the strongest
                # influence lands in the first slot, unused ones at the end - like the game's files
                v = raw[:, 4 * bi:4 * bi + 4][:, [1, 2, 3, 0]]
                bi += 1
            elif usage == 'BLEND_WEIGHTS':
                v = W[:, 4 * wi:4 * wi + 4][:, [1, 2, 3, 0]]
                wi += 1
            else:
                continue
            buf[:, off:off + size] = _encode(usage, fmt, np.asarray(v, np.float64), nv)
        # indices + parts
        all_idx, parts = [], []
        for p in mesh['meshParts']:
            chunk = np.concatenate(idx_by_part.get(p.get('MaterialName', ''), []) or [np.zeros(0, np.int64)])
            p['indexOffset'] = int(sum(len(x) for x in all_idx))
            p['indexCount'] = int(len(chunk))
            all_idx.append(chunk)
        idx = np.concatenate(all_idx) if all_idx else np.zeros(0, np.int64)
        big = nv > 65535
        mesh['IndexType'] = 'INT' if big else 'SHORT'
        ib = idx.astype(np.uint32 if big else np.uint16).tobytes()
        mn, mx = P.min(0), P.max(0)
        mesh['boundingBox'] = {'MinBound': dict(zip('XYZ', map(float, mn))), 'MaxBound': dict(zip('XYZ', map(float, mx)))}
        c = (mn + mx) / 2
        mesh['clipSphere'] = {'X': float(c[0]), 'Y': float(c[1]), 'Z': float(c[2]),
                              'Radius': float(np.linalg.norm(P - c, axis=1).max())}
        mesh['MorphShapes'] = []
        tb = mbf['TRMeshBuffers'][mi]
        tb['VertexBuffer'] = [{'Bytes': list(layers[k].tobytes())} for k in sorted(layers)]
        tb['IndexBuffer'] = [{'Bytes': list(ib)}]
        tb['MorphTargets'] = []
        replaced += 1
        empty = [p.get('MaterialName', '') for p in mesh['meshParts'] if not p['indexCount']]
        if empty:
            report.append('%s: no triangles for material %s - those parts will be invisible'
                          % (name, ', '.join('"%s"' % e for e in empty)))
        report.append('replaced %s: %d vertices, %d triangles, skinning "%s"' % (name, nv, len(idx) // 3, mode))

    if not replaced:
        names = ', '.join(sorted(m.get('Name', '') for m in msh['Meshes']))
        raise ImportError_('None of the meshes in this glTF file match %s, so nothing was changed.\n\n'
                           'The file must come from this model (exported with Lumiose Toolkit), with the '
                           'mesh objects still named like: %s' % (os.path.basename(trmdl_path), names))

    # write into the mod
    rels = {}
    for path in (trmsh_path, trmbf_path, trmdl_path):
        rels[path] = os.path.relpath(path, _game_root(trmdl_path)).replace(os.sep, '/')
    zm.from_json(json.dumps(msh), zm.schema_for(trmsh_path), mod.path_for(rels[trmsh_path]))
    zm.from_json(json.dumps(mbf), zm.schema_for(trmbf_path), mod.path_for(rels[trmbf_path]))
    mdl = json.loads(zm.to_json(trmdl_path))
    for lod in mdl.get('LODs', []):
        ents = lod.get('Entries', [])
        if ents:
            first = ents[0].get('Index', 0)
            for e in ents:
                e['Index'] = first          # every distance level uses the new full-detail mesh
    zm.from_json(json.dumps(mdl), zm.schema_for(trmdl_path), mod.path_for(rels[trmdl_path]))
    report.append('written into mod "%s": %s' % (mod.name, ', '.join(os.path.basename(p) for p in rels)))
    return report


def _game_root(path):
    """The export folder that contains ik_pokemon / ik_chara / field ..."""
    p = os.path.abspath(path)
    while True:
        parent = os.path.dirname(p)
        if parent == p:
            raise ImportError_('Could not find the game folder for ' + path)
        if os.path.isdir(os.path.join(parent, 'ik_message')) or os.path.isdir(os.path.join(parent, 'arc')):
            return parent
        p = parent
