"""Play as another character: the player's model becomes one of the game's human characters (a rival, a boss,
the hotel manager...), and the player's own face, hair and clothes become invisible.

How the player is put together: ik_chara/model_pc_base/model/p0_base.trmdl is the base - it has the skeleton
but no meshes. Everything visible is a part on top of it: a face (p0_face*), hair (p0_hrs*) and every
clothing item (p1_/p2_ upb, lwb, drs, p0_ ftw, lgw, glv, hdw, bag, acc, eyw), each in its own model_pc folder
with its own skeleton, matched to the base by bone names.

The mod: the base model gets the character's meshes, materials and skeleton (every character's skeleton has the
player's bones, so the player's animations move it), and every part is drawn as nothing: its vertex positions and
bone weights are set to zero, so every vertex lands on one point. Its files, mesh and material names stay as they
are - the game's own "none" items show a part needs a real mesh and material (an empty model crashes the game).
"""
import json
import os
import re

import za_modding as zm

BASE_DIR = 'ik_chara/model_pc_base/model'
BASE_MDL = BASE_DIR + '/p0_base.trmdl'
PARTS_DIR = 'ik_chara/model_pc'
CHAR_DIRS = ('ik_chara/model_uq', 'ik_chara/model_cc_ir')
# names from the game's trainer names where the model is certain; the rest say what the model is
NICE = {'hotel_manager': 'Hotel owner', 'president': 'President', 'secretary': 'Vinnie (secretary)',
        'friend_f': 'Lida', 'friend_m': 'Naveen', 'rival_f': 'Taunie', 'rival_m': 'Urbain',
        'detective': 'Emma', 'researcher': 'Researcher', 'co_member_m': 'Member of group CO (male)',
        'co_member_f': 'Member of group CO (female)', 'f': 'Woman', 'co': 'Member of group CO',
        'ca': 'Member of group CA'}


class PlayAsError(Exception):
    pass


def _bones(path):
    d = json.loads(zm.to_json(path, zm.schema_for(path)))
    return {n.get('Name') for n in d.get('TransformNodes') or [] if n.get('Name')}


# names the game's text doesn't give (or only as a code)
NAMES = {'tr0003_00_hotel_manager': 'AZ', 'tr0004_00_f': 'L / Lysandre', 'tr0005_00_president': 'Jett',
         'tr0023_00_researcher': 'Mable', 'tr0030_00_co_member_m': 'Rust Syndicate Grunt (male)',
         'tr0031_00_co_member_f': 'Rust Syndicate Grunt (female)', 'tr1000_01_co': 'Korrina (coat)'}
MAX_JOINT_GAP = 0.085          # m: how far a character's joints may sit from the player's (at the player's height)
ACCESSORY_BONES = ('bag', 'bangle', 'left_attach', 'right_attach')     # attachment points, not the body


def size_ratio(char_trskl, player_trskl):
    """The character's height compared with the player's (1.0 = the same)."""
    return head_height(char_trskl) / max(head_height(player_trskl), 1e-6)


def built_like_player(char_trskl, player_trskl):
    """True if the character, brought to the player's height, has its joints close to the player's - then the
    player's animations fit it. (Characters built differently get splayed fingers and floating accessories.)"""
    import numpy as np
    import za_trinity as zt
    ck, pk = zt.Skeleton(char_trskl), zt.Skeleton(player_trskl)
    cw, pw = ck.world_matrices(), pk.world_matrices()
    r = size_ratio(char_trskl, player_trskl)
    if not 0.5 <= r <= 2.5:
        return False
    gaps = [np.linalg.norm(cw[ck.index[b.name]][:3, 3] / r - pw[pk.index[b.name]][:3, 3])
            for b in pk.bones if b.name in ck.index and b.joint >= 0 and b.name not in ACCESSORY_BONES]
    return bool(gaps) and max(gaps) <= MAX_JOINT_GAP


def trainer_names(root):
    """{model folder: the character's name in the game's text}: model -> NPC template -> NPC -> trainer name."""
    import collections
    import za_formats as zf
    try:
        def rows(rel):
            path = os.path.join(root, *rel.split('/'))
            d = json.loads(zm.to_json(path, zm.schema_for(path)))
            return d[next(k for k in d if isinstance(d[k], list))]
        tpl = rows('world/ik_data/field/npc/npc_object_template_data/npc_object_template_data/'
                   'npc_object_template_data.bin')
        assets = rows('world/ik_data/field/npc/npc_asset_data/npc_asset_data/npc_asset_data.bin')
        t = os.path.join(root, 'ik_message', 'dat', 'English', 'common', 'trname')
        with open(t + '.tbl', 'rb') as f:
            labels = zf.parse_tbl(f.read())
        with open(t + '.dat', 'rb') as f:
            texts = zf.decode_msg(f.read())
    except Exception:  # noqa: BLE001
        return {}
    text = dict(zip(labels, texts))
    by_tpl = collections.defaultdict(set)
    for a in assets:
        for n in a.get('npcInfoList') or []:
            if n.get('name') in text and not text[n['name']].startswith(('[', '?')):
                by_tpl[n.get('objectTemplateId')].add(text[n['name']])
    out = {}
    for r in tpl:
        m = re.search(r'/(tr\d{4}_\d\d_[^/]+?)_?/', r.get('path', ''))
        if m and by_tpl.get(r.get('id')):
            out.setdefault(m.group(1).rstrip('_'), set()).update(by_tpl[r['id']])
    return {k: ' / '.join(sorted(v)) for k, v in out.items()}


def characters(root):
    """[(label, game folder)] of every human character model that can stand in for the player."""
    out = []
    player = _bones(os.path.join(root, *BASE_DIR.split('/'), 'p0_base.trskl'))
    known = trainer_names(root)
    for d in CHAR_DIRS:
        base = os.path.join(root, *d.split('/'))
        if not os.path.isdir(base):
            continue
        for f in sorted(os.listdir(base)):
            m = re.match(r'^tr(\d{4})_(\d\d)(?:_(.+))?$', f)
            folder = os.path.join(base, f)
            stem = 'tr%s_%s' % (m.group(1), m.group(2)) if m else f
            if not m or m.group(1) == '9999' or not os.path.isfile(os.path.join(folder, stem + '.trmdl')):
                continue
            sk = os.path.join(folder, stem + '.trskl')
            try:
                missing = player - _bones(sk)
            except Exception:  # noqa: BLE001
                continue
            if len(missing) > 4:                 # not a human body the player's animations can move
                continue
            # only characters built like the player: the player's animations fit them. Taller or differently
            # proportioned ones (Vinnie, the hotel manager...) get stretched fingers and a floating mega band
            if not built_like_player(sk, os.path.join(root, *BASE_DIR.split('/'), 'p0_base.trskl')):
                continue
            what = m.group(3) or ''
            label = NAMES.get(f) or known.get(f) or NICE.get(what)
            if label is None:
                b = re.match(r'^([a-z]+)_(boss|leader)(?:_([mf]))?$', what)
                label = ('%s of group %s%s' % (b.group(2).capitalize(), b.group(1).upper(),
                                                {'m': ' (male)', 'f': ' (female)'}.get(b.group(3), ''))
                         if b else what.replace('_', ' ').capitalize() if what else 'Character')
            if m.group(2) != '00' and f not in NAMES:
                label += ' (variant %d)' % int(m.group(2))
            r = size_ratio(sk, os.path.join(root, *BASE_DIR.split('/'), 'p0_base.trskl'))
            if abs(r - 1) > 0.02:
                label += '  (resized)'
            out.append(('%s  -  tr%s' % (label, m.group(1)), '%s/%s' % (d, f)))
    # trainers and townsfolk the game builds from parts (body types that fit the player)
    npcs = []
    for f, body in npc_list(root):
        if body in NPC_BODIES_THAT_FIT:
            try:
                npcs.append((npc_label(root, f), os.path.relpath(f, root).replace(os.sep, '/')))
            except Exception:  # noqa: BLE001
                continue
    return out + sorted(npcs)


def npc_folder(root, char_rel, tmp):
    """For an NPC (a .ccdata): its model assembled in a temporary folder (laid out like the game's romfs, so
    shared game textures are found); for a named character: its game folder."""
    if not char_rel.endswith('.ccdata'):
        return char_rel
    name = os.path.splitext(os.path.basename(char_rel))[0]
    out = os.path.join(tmp, 'romfs', 'ik_chara', 'model_pc_base', 'model', name)
    return assemble_npc(root, os.path.join(root, *char_rel.split('/')), out)


def player_order_skeleton(player, char):
    """The character's skeleton (trskl JSON) reordered for the player's animations: the player's bones first, in
    the player's order and with the player's joint numbers, then the character's own extra bones (face, hair...).
    Bones the character lacks (e.g. bag, bangle) are taken from the player. Returns (skeleton, old joint -> new
    joint) for renumbering the meshes' RigIndex."""
    import copy
    pn = player['TransformNodes']
    cn = {n['Name']: n for n in char['TransformNodes']}
    cj = char.get('JointInfos') or []
    pj = player.get('JointInfos') or []
    order = [n['Name'] for n in pn] + [n['Name'] for n in char['TransformNodes'] if n['Name'] not in
                                       {x['Name'] for x in pn}]
    index = {name: i for i, name in enumerate(order)}
    nodes, joints, jmap = [], [], {}
    used = set(n.get('JointInfoIndex') for n in pn if n.get('JointInfoIndex') is not None)
    next_joint = max(used) + 1 if used else 0
    joints = [None] * next_joint
    for i, name in enumerate(order):
        src_p = next((n for n in pn if n['Name'] == name), None)
        src = cn.get(name)
        n = copy.deepcopy(src if src is not None else src_p)
        parent = (src or src_p).get('ParentNodeName') or ''
        if not parent and (src or src_p).get('ParentNodeIndex') is not None:
            owner = char['TransformNodes'] if src is not None else pn
            parent = owner[(src or src_p)['ParentNodeIndex']]['Name']
        n.pop('JointInfoIndex', None)
        if i == 0:
            n.pop('ParentNodeIndex', None)
        else:
            n['ParentNodeIndex'] = index.get(parent, 0)
        old_j = src.get('JointInfoIndex') if src is not None else None
        if src_p is not None and src_p.get('JointInfoIndex') is not None:
            j = src_p['JointInfoIndex']                       # the player's joint number
        elif old_j is not None:
            j = next_joint                                    # a joint only the character has
            next_joint += 1
            joints.append(None)
        else:
            j = None
        if j is not None:
            n['JointInfoIndex'] = j
            if old_j is not None and old_j < len(cj):
                joints[j] = copy.deepcopy(cj[old_j])
                jmap[old_j] = j
            else:                                             # the player's own bone (bag, bangle...)
                joints[j] = copy.deepcopy(pj[src_p['JointInfoIndex']])
        nodes.append(n)
    for k, jt in enumerate(joints):
        if jt is None:
            joints[k] = copy.deepcopy(pj[k]) if k < len(pj) else {}
    out = dict(char)
    out['TransformNodes'] = nodes
    out['JointInfos'] = joints
    return out, jmap


def player_joint_map(player, char):
    """old character joint -> the player's joint of the same bone; bones the player doesn't have (face, hair...)
    go to their nearest parent the player has (e.g. head)."""
    pj = {n['Name']: n['JointInfoIndex'] for n in player['TransformNodes'] if n.get('JointInfoIndex') is not None}
    nodes = char['TransformNodes']
    by_name = {n['Name']: n for n in nodes}
    out = {}
    for n in nodes:
        j = n.get('JointInfoIndex')
        if j is None:
            continue
        cur, hops = n, 0
        while cur is not None and cur['Name'] not in pj and hops < 64:
            pname = cur.get('ParentNodeName') or (nodes[cur['ParentNodeIndex']]['Name']
                                                  if cur.get('ParentNodeIndex') is not None else '')
            cur = by_name.get(pname)
            hops += 1
        out[j] = pj[cur['Name']] if cur is not None and cur['Name'] in pj else pj.get('waist', 0)
    return out


SKIP_NEAREST = ('chara_root', 'origin', 'bag', 'bangle', 'look', 'left_attach', 'right_attach')


def rebound_weights(trmsh_path, buf, char_trskl, player_trskl, jmap, free):
    """The mesh buffer skinned to the player's joints. jmap: character joint -> player joint. Weight on the
    character's own extra bones that aren't under the head (jacket flaps, straps, cloth: `free`, a set of
    character joints) goes to the two player bones nearest each vertex instead, so those pieces bend with the
    hips and legs rather than swinging stiffly with one bone. Returns (bytes, {mesh index: {joint: weight sum}})."""
    import numpy as np
    import za_trinity as zt
    pk = zt.Skeleton(player_trskl)
    pw = pk.world_matrices()
    ck = zt.Skeleton(char_trskl)
    cw = ck.world_matrices()
    cand = [b for b in pk.bones if b.joint >= 0 and b.name not in SKIP_NEAREST and '_index_' not in b.name
            and not any(k in b.name for k in ('thumb', 'middle', 'ring', 'pinky')) and b.name in ck.index]
    cpos = np.array([cw[ck.index[b.name]][:3, 3] for b in cand])
    cj = np.array([b.joint for b in cand])
    with open(trmsh_path, 'rb') as f:
        mroot = zt.Table.root(f.read())
    buf = bytearray(buf)
    bt = zt.Table.root(buf).tables(1)
    sums = {}
    for i, mesh in enumerate(mroot.tables(1)):
        if i >= len(bt):
            break
        vbufs = bt[i].tables(1)
        use = sums.setdefault(i, {})
        for decl in mesh.tables(3):
            sizes = [t.u32(0) for t in decl.tables(1)]
            els = [(e.scalar(0, 'i', -1), e.u32(1), e.u32(2), e.u32(3), e.u32(4)) for e in decl.tables(0)]

            def column(el):
                si, usage, layer, fmt, off = el
                stride = sizes[0 if si == -1 else si] if 0 <= (0 if si == -1 else si) < len(sizes) else 0
                size = zt.FMT_SIZE.get(fmt)
                if not stride or not size or layer >= len(vbufs):
                    return None
                start, count = vbufs[layer].vector(0)
                n_ = count // stride
                arr = np.frombuffer(bytes(buf[start:start + n_ * stride]), np.uint8).reshape(n_, stride).copy()
                return start, stride, off, size, n_, arr
            pos = next((e for e in els if e[1] == zt.POSITION and e[3] in (51, 54)), None)
            idx = [e for e in els if e[1] == zt.BLEND_INDEX]
            wgt = [e for e in els if e[1] == zt.BLEND_WEIGHTS]
            if pos is None or not idx or len(idx) != len(wgt) or any(e[3] not in (20, 22, 39) for e in idx + wgt):
                continue
            cp = column(pos)
            n_ = cp[4]
            width = 12 if pos[3] == 51 else 16
            raw = np.ascontiguousarray(cp[5][:, pos[4]:pos[4] + width]).view('<f4').reshape(n_, width // 4)
            P = raw[:, :3] if pos[3] == 51 else raw[:, 1:4]
            cols_i = [column(e) for e in idx]
            cols_w = [column(e) for e in wgt]
            J = np.concatenate([zt._decode(np.ascontiguousarray(c[5][:, e[4]:e[4] + zt.FMT_SIZE[e[3]]]), e[3],
                                           zt.BLEND_INDEX) for c, e in zip(cols_i, idx)], 1).astype(np.int64)
            W = np.concatenate([zt._decode(np.ascontiguousarray(c[5][:, e[4]:e[4] + zt.FMT_SIZE[e[3]]]), e[3],
                                           zt.BLEND_WEIGHTS) for c, e in zip(cols_w, wgt)], 1)
            slots = J.shape[1]
            d = np.linalg.norm(P[:, None, :] - cpos[None, :, :], axis=2)          # vertex -> candidate bones
            near = np.argsort(d, axis=1)[:, :2]
            nd = np.take_along_axis(d, near, 1)
            share = (1.0 / np.maximum(nd, 1e-4))
            share /= share.sum(1, keepdims=True)
            newJ = np.zeros_like(J)
            newW = np.zeros_like(W)
            for v in range(n_):
                acc = {}
                for k in range(slots):
                    w = float(W[v, k])
                    if w <= 0:
                        continue
                    j = int(J[v, k])
                    if j in free:
                        for t in range(2):
                            jj = int(cj[near[v, t]])
                            acc[jj] = acc.get(jj, 0.0) + w * float(share[v, t])
                    else:
                        jj = jmap.get(j, 0)
                        acc[jj] = acc.get(jj, 0.0) + w
                top = sorted(acc.items(), key=lambda x: -x[1])[:slots]
                tot = sum(w for _, w in top) or 1.0
                for k, (jj, w) in enumerate(top):
                    newJ[v, k], newW[v, k] = jj, w / tot
                    use[jj] = use.get(jj, 0.0) + w / tot
            # back into the file, in each column's own layout (stored order W,X,Y,Z = logical 3,0,1,2)
            for g, (ci, e) in enumerate(zip(cols_i, idx)):
                part = newJ[:, 4 * g:4 * g + 4][:, [3, 0, 1, 2]]
                data = np.ascontiguousarray(part.astype('<u2' if e[3] == 39 else 'u1')).view(np.uint8).reshape(n_, -1)
                start, stride, off, size, _, arr = ci
                arr[:, off:off + size] = data
                buf[start:start + n_ * stride] = arr.tobytes()
                cols_w[g] = column(wgt[g])                    # the same rows may hold both columns
            for g, (cw_, e) in enumerate(zip(cols_w, wgt)):
                part = newW[:, 4 * g:4 * g + 4][:, [3, 0, 1, 2]]
                if e[3] == 39:
                    q = np.rint(part * 65535).astype('<u2')
                else:
                    q = np.rint(part * 255).astype('u1')
                data = np.ascontiguousarray(q).view(np.uint8).reshape(n_, -1)
                start, stride, off, size, _, arr = column(e)
                arr[:, off:off + size] = data
                buf[start:start + n_ * stride] = arr.tobytes()
    return bytes(buf), sums


def renumber_joints(trmsh_path, buf, jmap):
    """The mesh buffer with every vertex's bone indices (joint numbers) renumbered old -> new."""
    import numpy as np
    import za_trinity as zt
    with open(trmsh_path, 'rb') as f:
        mroot = zt.Table.root(f.read())
    buf = bytearray(buf)
    bt = zt.Table.root(buf).tables(1)
    lut = np.arange(65536, dtype=np.int64)
    for o, n in jmap.items():
        lut[o] = n
    for i, mesh in enumerate(mroot.tables(1)):
        if i >= len(bt):
            break
        vbufs = bt[i].tables(1)
        for decl in mesh.tables(3):
            sizes = [t.u32(0) for t in decl.tables(1)]
            for e in decl.tables(0):
                si, usage, layer, fmt, off = e.scalar(0, 'i', -1), e.u32(1), e.u32(2), e.u32(3), e.u32(4)
                if usage != zt.BLEND_INDEX or layer >= len(vbufs) or fmt not in (20, 22, 39):
                    continue
                stride = sizes[0 if si == -1 else si] if 0 <= (0 if si == -1 else si) < len(sizes) else 0
                if not stride:
                    continue
                start, count = vbufs[layer].vector(0)
                n_ = count // stride
                arr = np.frombuffer(bytes(buf[start:start + n_ * stride]), np.uint8).reshape(n_, stride).copy()
                dt = np.uint16 if fmt == 39 else np.uint8
                width = 8 if fmt == 39 else 4
                col = np.ascontiguousarray(arr[:, off:off + width]).view('<u2' if fmt == 39 else 'u1')
                new = lut[col.astype(np.int64)]
                if new.max() > np.iinfo(dt).max:
                    raise PlayAsError('Too many bones for this mesh\'s bone index format.')
                arr[:, off:off + width] = new.astype('<u2' if fmt == 39 else 'u1').view(np.uint8).reshape(n_, width)
                buf[start:start + n_ * stride] = arr.tobytes()
    return bytes(buf)


def collapsed_buffer(trmsh_path):
    """(buffer file name, bytes) of a mesh's .trmbf with every vertex's position and bone weights set to zero."""
    import za_trinity as zt
    with open(trmsh_path, 'rb') as f:
        mroot = zt.Table.root(f.read())
    name = mroot.string(2)
    with open(os.path.join(os.path.dirname(trmsh_path), name), 'rb') as f:
        buf = bytearray(f.read())
    broot = zt.Table.root(buf)
    buffers = broot.tables(1)
    for i, mesh in enumerate(mroot.tables(1)):
        if i >= len(buffers):
            break
        vbufs = buffers[i].tables(1)
        for decl in mesh.tables(3):
            sizes = [t.u32(0) for t in decl.tables(1)]
            for el in decl.tables(0):
                si, usage, layer, fmt, offset = el.scalar(0, 'i', -1), el.u32(1), el.u32(2), el.u32(3), el.u32(4)
                if usage not in (zt.POSITION, zt.BLEND_WEIGHTS) or layer >= len(vbufs):
                    continue
                stride = sizes[0 if si == -1 else si] if 0 <= (0 if si == -1 else si) < len(sizes) else 0
                size = zt.FMT_SIZE.get(fmt)
                start, count = vbufs[layer].vector(0)
                if not stride or not size or offset + size > stride:
                    continue
                for k in range(count // stride):
                    q = start + k * stride + offset
                    buf[q:q + size] = bytes(size)
    return name, bytes(buf)


def head_height(trskl_path):
    """Height of the 'head' bone in the rest pose (m) - how tall a character's skeleton is."""
    import za_trinity as zt
    sk = zt.Skeleton(trskl_path)
    w = sk.world_matrices()
    i = sk.index.get('head')
    return float(w[i][1, 3]) if i is not None else 0.0


def scaled_positions(trmsh_path, buf, factor):
    """The mesh buffer with every vertex position multiplied by factor (about the feet: the model's origin)."""
    import numpy as np
    import za_trinity as zt
    with open(trmsh_path, 'rb') as f:
        mroot = zt.Table.root(f.read())
    buf = bytearray(buf)
    bt = zt.Table.root(buf).tables(1)
    for i, mesh in enumerate(mroot.tables(1)):
        if i >= len(bt):
            break
        vbufs = bt[i].tables(1)
        for decl in mesh.tables(3):
            sizes = [t.u32(0) for t in decl.tables(1)]
            for e in decl.tables(0):
                si, usage, layer, fmt, off = e.scalar(0, 'i', -1), e.u32(1), e.u32(2), e.u32(3), e.u32(4)
                if usage != zt.POSITION or layer >= len(vbufs) or fmt not in (51, 54):
                    continue
                stride = sizes[0 if si == -1 else si] if 0 <= (0 if si == -1 else si) < len(sizes) else 0
                if not stride:
                    continue
                start, count = vbufs[layer].vector(0)
                n_ = count // stride
                arr = np.frombuffer(bytes(buf[start:start + n_ * stride]), np.uint8).reshape(n_, stride).copy()
                width = 12 if fmt == 51 else 16
                v = np.ascontiguousarray(arr[:, off:off + width]).view('<f4').reshape(n_, width // 4).copy()
                if fmt == 51:
                    v *= factor
                else:
                    v[:, 1:4] *= factor                      # W is stored first
                arr[:, off:off + width] = v.astype('<f4').view(np.uint8).reshape(n_, width)
                buf[start:start + n_ * stride] = arr.tobytes()
    return bytes(buf)


def fitted_to_player(trmsh_path, buf, char_trskl, player_trskl, ratio, target_name):
    """The mesh buffer with every vertex moved from where its bones are in the character to where the same bones
    are in the player's skeleton (scaled to the character's height first), with normals and tangents turned
    along. target_name: character bone name -> the player bone it follows. Old joint numbers are kept."""
    import numpy as np
    import za_trinity as zt
    ck, pk = zt.Skeleton(char_trskl), zt.Skeleton(player_trskl)
    cw, pw = ck.world_matrices(), pk.world_matrices()
    shrink = np.diag([1.0 / ratio, 1.0 / ratio, 1.0 / ratio, 1.0])

    move = {}
    for j, node in enumerate(ck.joint_to_node):
        if node < 0:
            continue
        name = ck.bones[node].name
        tgt = target_name.get(name)
        if tgt is None or tgt not in pk.index:
            continue
        a = tgt if tgt in ck.index else name             # a bone the player lacks moves with the one it follows
        frame = cw[ck.index[a]].copy()
        frame[:3, 3] /= ratio                            # the character at the player's height: positions only
        move[j] = pw[pk.index[tgt]] @ np.linalg.inv(frame) @ shrink
    with open(trmsh_path, 'rb') as f:
        mroot = zt.Table.root(f.read())
    buf = bytearray(buf)
    bt = zt.Table.root(buf).tables(1)
    for i, mesh in enumerate(mroot.tables(1)):
        if i >= len(bt):
            break
        vbufs = bt[i].tables(1)
        for decl in mesh.tables(3):
            sizes = [t.u32(0) for t in decl.tables(1)]
            els = [(e.scalar(0, 'i', -1), e.u32(1), e.u32(2), e.u32(3), e.u32(4)) for e in decl.tables(0)]

            def column(el):
                si, usage, layer, fmt, off = el
                stride = sizes[0 if si == -1 else si] if 0 <= (0 if si == -1 else si) < len(sizes) else 0
                size = zt.FMT_SIZE.get(fmt)
                if not stride or not size or layer >= len(vbufs):
                    return None
                start, count = vbufs[layer].vector(0)
                n_ = count // stride
                arr = np.frombuffer(bytes(buf[start:start + n_ * stride]), np.uint8).reshape(n_, stride).copy()
                return start, stride, off, size, n_, arr

            def put(c, data):
                start, stride, off, size, n_, arr = c
                arr[:, off:off + size] = data
                buf[start:start + n_ * stride] = arr.tobytes()
            pos = next((e for e in els if e[1] == zt.POSITION and e[3] in (51, 54)), None)
            idx = [e for e in els if e[1] == zt.BLEND_INDEX]
            wgt = [e for e in els if e[1] == zt.BLEND_WEIGHTS]
            if pos is None or not idx or len(idx) != len(wgt):
                continue
            cp = column(pos)
            if cp is None:
                continue
            n_ = cp[4]
            J = np.concatenate([zt._decode(np.ascontiguousarray(column(e)[5][:, e[4]:e[4] + zt.FMT_SIZE[e[3]]]),
                                           e[3], zt.BLEND_INDEX) for e in idx], 1).astype(np.int64)
            W = np.concatenate([zt._decode(np.ascontiguousarray(column(e)[5][:, e[4]:e[4] + zt.FMT_SIZE[e[3]]]),
                                           e[3], zt.BLEND_WEIGHTS) for e in wgt], 1)
            W = W / np.maximum(W.sum(1, keepdims=True), 1e-8)
            top = int(J.max()) + 1 if J.size else 1
            mats = np.stack([move.get(j, shrink) for j in range(top)])
            M = np.einsum('vk,vkij->vij', W, mats[J])
            width = 12 if pos[3] == 51 else 16
            raw = np.ascontiguousarray(cp[5][:, pos[4]:pos[4] + width]).view('<f4').reshape(n_, width // 4).copy()
            P = raw[:, :3] if pos[3] == 51 else raw[:, 1:4]
            P2 = np.einsum('vij,vj->vi', M[:, :3, :3], P) + M[:, :3, 3]
            if pos[3] == 51:
                raw[:, :3] = P2
            else:
                raw[:, 1:4] = P2
            put(cp, raw.astype('<f4').view(np.uint8).reshape(n_, width))
            for e in els:                                     # directions turn with their vertex
                if e[1] in (zt.NORMAL, zt.TANGENT, zt.BINORMAL) and e[3] == 43:
                    c = column(e)
                    if c is None or c[4] != n_:
                        continue
                    v = np.ascontiguousarray(c[5][:, e[4]:e[4] + 8]).view('<f2').reshape(n_, 4).astype(np.float32)
                    d = np.einsum('vij,vj->vi', M[:, :3, :3], v[:, :3])
                    v[:, :3] = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-8)
                    put(c, v.astype('<f2').view(np.uint8).reshape(n_, 8))
    return bytes(buf)


FACE_BONES = ('eye', 'brow', 'lip', 'teeth', 'jaw', 'cheek', 'mouth', 'tongue', 'nose', 'look')


def idle_animation(root, char_rel):
    """The character's own standing-idle animation (.tranm), or None."""
    import glob
    folder = os.path.join(root, 'ik_chara', 'motion_uq', os.path.basename(char_rel))
    found = sorted(glob.glob(os.path.join(folder, '*defaultwait01_loop.tranm'))) or \
        sorted(glob.glob(os.path.join(folder, '*wait*loop*.tranm')))
    return found[0] if found else None


def hanging_bones(char_trskl, player_trskl, anim):
    """Bones on the character's head the player doesn't have (hair strands, bandanas, ribbons) that its idle
    animation moves - not the face. On the player they can't move, so they're baked hanging the way the idle
    holds them instead of sticking out in the model's default pose."""
    import za_trinity as zt
    ck, pk = zt.Skeleton(char_trskl), zt.Skeleton(player_trskl)
    out = set()
    for i, b in enumerate(ck.bones):
        if b.name in pk.index or b.name not in anim.tracks or any(k in b.name for k in FACE_BONES):
            continue
        p_, hops = b.parent, 0
        while 0 <= p_ < len(ck.bones) and ck.bones[p_].name != 'head' and hops < 64:
            p_, hops = ck.bones[p_].parent, hops + 1
        if 0 <= p_ < len(ck.bones):
            out.add(b.name)
    return out


def baked_pose(trmsh_path, buf, char_trskl, anim_path, bones):
    """The mesh buffer with `bones` (names) posed as in the first frame of the animation - their vertices moved
    (normals and tangents turned) - and every other bone left in the model's own pose."""
    import numpy as np
    import za_trinity as zt
    ck = zt.Skeleton(char_trskl)
    anim = zt.Animation(anim_path)
    rest = ck.world_matrices()
    posed = [None] * len(ck.bones)

    def first(ch, key, default):
        v = ch.get(key)
        if v is None:
            return default
        vals = np.asarray(v[1])
        return tuple(float(x) for x in vals[0]) if len(vals) else default

    def world(i):
        if posed[i] is None:
            b = ck.bones[i]
            t, q, sc = b.t, b.q, b.s
            if b.name in bones:
                ch = anim.tracks.get(b.name, {})
                t, q, sc = first(ch, 't', t), first(ch, 'r', q), first(ch, 's', sc)
            local = zt.trs_matrix(t, q, sc)
            posed[i] = world(b.parent) @ local if 0 <= b.parent < len(ck.bones) and b.parent != i else local
        return posed[i]
    move = {}
    for j, node in enumerate(ck.joint_to_node):
        if node >= 0 and ck.bones[node].name in bones:
            move[j] = world(node) @ np.linalg.inv(rest[node])
    if not move:
        return buf
    with open(trmsh_path, 'rb') as f:
        mroot = zt.Table.root(f.read())
    buf = bytearray(buf)
    bt = zt.Table.root(buf).tables(1)
    for i, mesh in enumerate(mroot.tables(1)):
        if i >= len(bt):
            break
        vbufs = bt[i].tables(1)
        for decl in mesh.tables(3):
            sizes = [t_.u32(0) for t_ in decl.tables(1)]
            els = [(e.scalar(0, 'i', -1), e.u32(1), e.u32(2), e.u32(3), e.u32(4)) for e in decl.tables(0)]

            def column(el):
                si, usage, layer, fmt, off = el
                stride = sizes[0 if si == -1 else si] if 0 <= (0 if si == -1 else si) < len(sizes) else 0
                size = zt.FMT_SIZE.get(fmt)
                if not stride or not size or layer >= len(vbufs):
                    return None
                start, count = vbufs[layer].vector(0)
                n_ = count // stride
                arr = np.frombuffer(bytes(buf[start:start + n_ * stride]), np.uint8).reshape(n_, stride).copy()
                return start, stride, off, size, n_, arr

            def put(c, data):
                start, stride, off, size, n_, arr = c
                arr[:, off:off + size] = data
                buf[start:start + n_ * stride] = arr.tobytes()
            pos = next((e for e in els if e[1] == zt.POSITION and e[3] in (51, 54)), None)
            idx = [e for e in els if e[1] == zt.BLEND_INDEX]
            wgt = [e for e in els if e[1] == zt.BLEND_WEIGHTS]
            if pos is None or not idx or len(idx) != len(wgt):
                continue
            J = np.concatenate([zt._decode(np.ascontiguousarray(column(e)[5][:, e[4]:e[4] + zt.FMT_SIZE[e[3]]]),
                                           e[3], zt.BLEND_INDEX) for e in idx], 1).astype(np.int64)
            W = np.concatenate([zt._decode(np.ascontiguousarray(column(e)[5][:, e[4]:e[4] + zt.FMT_SIZE[e[3]]]),
                                           e[3], zt.BLEND_WEIGHTS) for e in wgt], 1)
            W = W / np.maximum(W.sum(1, keepdims=True), 1e-8)
            if not np.isin(J[W > 0], list(move)).any():
                continue
            top = int(J.max()) + 1
            mats = np.stack([move.get(j, np.eye(4)) for j in range(top)])
            M = np.einsum('vk,vkij->vij', W, mats[J])
            cp = column(pos)
            n_ = cp[4]
            width = 12 if pos[3] == 51 else 16
            raw = np.ascontiguousarray(cp[5][:, pos[4]:pos[4] + width]).view('<f4').reshape(n_, width // 4).copy()
            P = raw[:, :3] if pos[3] == 51 else raw[:, 1:4]
            P2 = np.einsum('vij,vj->vi', M[:, :3, :3], P) + M[:, :3, 3]
            if pos[3] == 51:
                raw[:, :3] = P2
            else:
                raw[:, 1:4] = P2
            put(cp, raw.astype('<f4').view(np.uint8).reshape(n_, width))
            for e in els:
                if e[1] in (zt.NORMAL, zt.TANGENT, zt.BINORMAL) and e[3] == 43:
                    c = column(e)
                    if c is None or c[4] != n_:
                        continue
                    v = np.ascontiguousarray(c[5][:, e[4]:e[4] + 8]).view('<f2').reshape(n_, 4).astype(np.float32)
                    d = np.einsum('vij,vj->vi', M[:, :3, :3], v[:, :3])
                    v[:, :3] = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-8)
                    put(c, v.astype('<f2').view(np.uint8).reshape(n_, 8))
    return bytes(buf)


def held_meshes(trmsh_path, buf, char_trskl, player_trskl):
    """Indices of the meshes the character holds in a hand (briefcase, cane...): pieces that follow only bones the
    player doesn't have, hung under a hand. Hands and gloves follow the player's own hand bones, so they stay."""
    import numpy as np
    import za_trinity as zt
    ck, pk = zt.Skeleton(char_trskl), zt.Skeleton(player_trskl)

    def held_bone(i):
        if not 0 <= i < len(ck.bones) or ck.bones[i].name in pk.index:
            return False
        hops = 0
        while 0 <= i < len(ck.bones) and hops < 64:
            n = ck.bones[i].name
            if 'hand' in n or 'attach' in n or any(k in n for k in ('thumb', 'index', 'middle', 'ring', 'pinky')):
                return True
            if n in ('spine_01', 'spine_02', 'spine_03', 'neck', 'head', 'hips', 'waist', 'origin', 'chara_root'):
                return False
            i, hops = ck.bones[i].parent, hops + 1
        return False
    held_joint = {j for j, node in enumerate(ck.joint_to_node) if node >= 0 and held_bone(node)}
    if not held_joint:
        return set()
    with open(trmsh_path, 'rb') as f:
        mroot = zt.Table.root(f.read())
    bt = zt.Table.root(bytearray(buf)).tables(1)
    out = set()
    for i, mesh in enumerate(mroot.tables(1)):
        if i >= len(bt):
            break
        vbufs = bt[i].tables(1)
        for decl in mesh.tables(3):
            sizes = [t.u32(0) for t in decl.tables(1)]
            els = [(e.scalar(0, 'i', -1), e.u32(1), e.u32(2), e.u32(3), e.u32(4)) for e in decl.tables(0)]
            idx = [e for e in els if e[1] == zt.BLEND_INDEX]
            wgt = [e for e in els if e[1] == zt.BLEND_WEIGHTS]
            if not idx or len(idx) != len(wgt):
                continue

            def col(e):
                si, usage, layer, fmt, off = e
                stride = sizes[0 if si == -1 else si] if 0 <= (0 if si == -1 else si) < len(sizes) else 0
                size = zt.FMT_SIZE.get(fmt)
                start, count = vbufs[layer].vector(0)
                n_ = count // stride
                arr = np.frombuffer(bytes(buf[start:start + n_ * stride]), np.uint8).reshape(n_, stride)
                return zt._decode(np.ascontiguousarray(arr[:, off:off + size]), fmt, usage)
            J = np.concatenate([col(e) for e in idx], 1).astype(np.int64)
            W = np.concatenate([col(e) for e in wgt], 1)
            total = float(W.sum())
            on_held = float(W[np.isin(J, list(held_joint))].sum())
            if total > 0 and on_held / total >= 0.9:
                out.add(i)
    return out


def collapse_meshes(trmsh_path, buf, indices):
    """The mesh buffer with the meshes at `indices` drawn as nothing (positions and bone weights zeroed)."""
    import za_trinity as zt
    with open(trmsh_path, 'rb') as f:
        mroot = zt.Table.root(f.read())
    buf = bytearray(buf)
    bt = zt.Table.root(buf).tables(1)
    for i, mesh in enumerate(mroot.tables(1)):
        if i not in indices or i >= len(bt):
            continue
        vbufs = bt[i].tables(1)
        for decl in mesh.tables(3):
            sizes = [t.u32(0) for t in decl.tables(1)]
            for el in decl.tables(0):
                si, usage, layer, fmt, offset = el.scalar(0, 'i', -1), el.u32(1), el.u32(2), el.u32(3), el.u32(4)
                if usage not in (zt.POSITION, zt.BLEND_WEIGHTS) or layer >= len(vbufs):
                    continue
                stride = sizes[0 if si == -1 else si] if 0 <= (0 if si == -1 else si) < len(sizes) else 0
                size = zt.FMT_SIZE.get(fmt)
                start, count = vbufs[layer].vector(0)
                if not stride or not size:
                    continue
                for k in range(count // stride):
                    q = start + k * stride + offset
                    buf[q:q + size] = bytes(size)
    return bytes(buf)


def player_skeleton_bytes(root, char_skel, ratio, char_trskl=None):
    """p0_base.trskl with, patched into its own bytes (a round trip through the schema loses part of this file,
    and a skeleton missing it crashes the game): bind matrices that move each bone's part of the character's
    mesh, left as it is, to the player's joint (without turning it), and the root bone's scale for the
    character's height (no animation moves the root). Returns (bytes, number of bones changed)."""
    import struct
    import za_trinity as zt
    sp = os.path.join(root, *BASE_DIR.split('/'), 'p0_base.trskl')
    with open(sp, 'rb') as f:
        raw = bytearray(f.read())
    t = zt.Table.root(raw)
    nodes, joints = t.tables(1), t.tables(2)
    cj = char_skel.get('JointInfos') or []
    cbind = {n['Name']: cj[n['JointInfoIndex']] for n in char_skel['TransformNodes']
             if n.get('JointInfoIndex') is not None and n['JointInfoIndex'] < len(cj)}
    # the character's bind pose, without its turn: each bone's part of the mesh is only moved to where the
    # player's joint is (at the character's height), never turned - where the character's bones point another way
    # than the player's (fingers), turning them would splay the hand
    import numpy as np
    ck, pk = zt.Skeleton(char_trskl or sp), zt.Skeleton(sp)
    cw, pw = ck.world_matrices(), pk.world_matrices()
    scale = np.diag([ratio, ratio, ratio, 1.0])
    swapped = 0
    for n in nodes:
        name, j = n.string(0), n.scalar(5, 'i', -1)
        if name not in cbind or name not in ck.index or name not in pk.index or not 0 <= j < len(joints):
            continue
        mt = joints[j].table(2)
        if mt is None or not all(mt.off(k) for k in range(4)):
            raise PlayAsError("The player skeleton has an unexpected layout - can't fit the character to it.")
        wp = scale @ pw[pk.index[name]]                  # the player's joint, at the character's height
        move = np.eye(4)
        move[:3, 3] = wp[:3, 3] - cw[ck.index[name]][:3, 3]
        ibm = np.linalg.inv(wp) @ move
        old = np.array([struct.unpack_from('<3f', raw, mt.pos + mt.off(k)) for k in range(4)]).T   # 3x4 columns
        if np.abs(ibm[:3, :4] - old).max() <= 0.05 and ratio == 1.0:
            continue                                     # built like the player here: keep the game's own
        for k in range(4):
            struct.pack_into('<3f', raw, mt.pos + mt.off(k), *[float(x) for x in ibm[:3, k]])
        swapped += 1
    if ratio != 1.0:
        rn = nodes[0]
        srt = rn.table(1)
        if rn.string(0) != 'chara_root' or srt is None or not srt.off(0):
            raise PlayAsError("The player skeleton has an unexpected layout - can't set the height.")
        struct.pack_into('<3f', raw, srt.pos + srt.off(0), ratio, ratio, ratio)
    return bytes(raw), swapped


def make(root, mod, char_rel, hide_parts=True, log=None, reorder=False, bind_player=True, own_height=False,
         fit=True, nearest_weights=False, keep_held=False):
    """Make `mod` turn the player into the character in game folder `char_rel` (from characters())."""
    say = log or (lambda s: None)
    if char_rel.endswith('.ccdata'):                     # a part-built NPC: assembled first, then like the others
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp(prefix='playas_')
        try:
            return make(root, mod, npc_folder(root, char_rel, tmp), hide_parts, log, reorder, bind_player,
                        own_height, fit, nearest_weights, keep_held)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    # a game folder, or a folder assemble_npc() made (a part-built NPC)
    src = os.path.normpath(char_rel) if os.path.isabs(char_rel) else os.path.join(root, *char_rel.split('/'))
    stem = re.match(r'^(tr\d{4}_\d\d)', os.path.basename(char_rel))
    if not stem or not os.path.isfile(os.path.join(src, stem.group(1) + '.trmdl')):
        raise PlayAsError('Not a character model folder: ' + char_rel)
    stem = stem.group(1)
    sch = zm.schema_for(os.path.join(src, stem + '.trmdl'))
    mdl = json.loads(zm.to_json(os.path.join(src, stem + '.trmdl'), sch))
    # 1. the character's files next to the base model (names kept: the character's model points at them)
    n = 0
    for f in sorted(os.listdir(src)):
        p = os.path.join(src, f)
        if os.path.isfile(p) and not f.endswith('.trmdl'):
            mod.add_file('%s/%s' % (BASE_DIR, f), p)
            n += 1
    # 2. the base model is now the character's model, its skeleton in the player's bone order (the player's
    #    animations address bones by their place in the player's skeleton) and its meshes renumbered to match
    out = mod.path_for(BASE_MDL)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    zm.from_json(json.dumps(mdl), sch, out)
    skl_name = (mdl.get('Skeleton') or {}).get('PathName') or stem + '.trskl'
    skl_sch = zm.schema_for(os.path.join(src, skl_name))
    player = json.loads(zm.to_json(os.path.join(root, *BASE_DIR.split('/'), 'p0_base.trskl'), skl_sch))
    char = json.loads(zm.to_json(os.path.join(src, skl_name), skl_sch))
    if reorder:     # experimental: the player's bone order (crashed the game in testing - off for now)
        skel, jmap = player_order_skeleton(player, char)
        import za_trinity as zt
        for m_ in mdl.get('Meshes') or []:          # vertices name their bones by joint number: renumber them too
            mp = os.path.join(src, m_['PathName'])
            with open(mp, 'rb') as f:
                bname = zt.Table.root(f.read()).string(2)
            with open(os.path.join(src, bname), 'rb') as f:
                data = f.read()
            mod.add_bytes('%s/%s' % (BASE_DIR, bname), renumber_joints(mp, data, jmap))
        # only the character's own skeleton file: the player's p0_base.trskl stays as it is (the game's player
        # systems are made for its bones - replacing it crashes the game)
        zm.from_json(json.dumps(skel), skl_sch, mod.path_for('%s/%s' % (BASE_DIR, skl_name)))
        for m_ in mdl.get('Meshes') or []:
            mp = os.path.join(src, m_['PathName'])
            msch = zm.schema_for(mp)
            md = json.loads(zm.to_json(mp, msch))
            for mesh in md.get('Meshes') or []:
                for bw in mesh.get('boneWeight') or []:
                    bw['RigIndex'] = jmap.get(bw.get('RigIndex', 0), bw.get('RigIndex', 0))
            zm.from_json(json.dumps(md), msch, mod.path_for('%s/%s' % (BASE_DIR, m_['PathName'])))
        say('skeleton in the player\'s bone order (%d bones, %d joints)' % (len(skel['TransformNodes']),
                                                                           len(skel['JointInfos'])))
    if bind_player:     # the character's meshes skinned to the player's own skeleton (p0_base.trskl)
        import za_trinity as zt
        jmap = player_joint_map(player, char)
        pjn = {x['JointInfoIndex']: x['Name'] for x in player['TransformNodes'] if x.get('JointInfoIndex') is not None}
        # the character's extra bones outside the head (jacket flaps, straps, cloth): skinned by nearness
        pnames = {x['Name'] for x in player['TransformNodes']}
        cn = char['TransformNodes']

        def under_head(node):
            hops = 0
            while node is not None and hops < 64:
                if node['Name'] == 'head':
                    return True
                pi = node.get('ParentNodeIndex')
                node = cn[pi] if pi is not None and 0 <= pi < len(cn) else None
                hops += 1
            return False
        free = {x['JointInfoIndex'] for x in cn if x.get('JointInfoIndex') is not None and x['Name'] not in pnames
                and not under_head(x)} if nearest_weights else set()
        names = {x['Name']: pjn.get(jmap.get(x['JointInfoIndex'])) for x in char['TransformNodes']
                 if x.get('JointInfoIndex') is not None}
        if fit is None:
            # built like the player (same height and bind pose, e.g. Urbain, Taunie): the method proven in game;
            # built differently (e.g. Vinnie): the mesh stays as it is and its bind pose goes into the skeleton
            fit = player_skeleton_bytes(root, char, 1.0, os.path.join(src, skl_name))[1] == 0
        # a character taller or shorter than the player: its mesh is brought to the player's height so its joints
        # meet the player's, and the player's skeleton is scaled back up (or down) from its root bone, which no
        # animation moves - so it stands at its own height
        hang, anim_path = set(), idle_animation(root, char_rel)
        if anim_path:
            import za_trinity as zt
            hang = hanging_bones(os.path.join(src, skl_name), os.path.join(root, *BASE_DIR.split('/'), 'p0_base.trskl'),
                                 zt.Animation(anim_path))
            if hang:
                say('hanging as in its idle: %d hair / band bones' % len(hang))
        ratio = 1.0                          # the character brought to the player's height (its mesh scaled)
        ph = head_height(os.path.join(root, *BASE_DIR.split('/'), 'p0_base.trskl'))
        ch = head_height(os.path.join(src, skl_name))
        if ph > 0 and ch > 0 and abs(ch / ph - 1) > 0.02:
            ratio = ch / ph
        for m_ in mdl.get('Meshes') or []:
            mp = os.path.join(src, m_['PathName'])
            with open(mp, 'rb') as f:
                bname = zt.Table.root(f.read()).string(2)
            with open(os.path.join(src, bname), 'rb') as f:
                data = f.read()
            held = set() if keep_held else held_meshes(mp, data, os.path.join(src, skl_name),
                                                       os.path.join(root, *BASE_DIR.split('/'), 'p0_base.trskl'))
            if held:                            # briefcase, cane...: the player needs both hands free
                data = collapse_meshes(mp, data, held)
                say('left out what it holds in its hands: %d piece(s)' % len(held))
            if hang:                            # hair strands, bandanas: hanging as in the character's idle
                data = baked_pose(mp, data, os.path.join(src, skl_name), anim_path, hang)
            if fit:                             # bones of other lengths or angles: vertices follow the player's
                data = fitted_to_player(mp, data, os.path.join(src, skl_name),
                                        os.path.join(root, *BASE_DIR.split('/'), 'p0_base.trskl'), ratio, names)
            if free:                            # experimental: jacket / strap bones by nearness (off)
                data, sums = rebound_weights(mp, data, os.path.join(src, skl_name),
                                             os.path.join(root, *BASE_DIR.split('/'), 'p0_base.trskl'), jmap, free)
            else:                               # each bone to the player's bone of the same name (or its parent's)
                data, sums = renumber_joints(mp, data, jmap), {}
            mod.add_bytes('%s/%s' % (BASE_DIR, bname), data)
            msch = zm.schema_for(mp)
            md = json.loads(zm.to_json(mp, msch))
            for k, mesh in enumerate(md.get('Meshes') or []):
                # the joints a mesh uses, each once, like the game's own meshes
                used = sums.get(k) or {}
                if used:
                    mesh['boneWeight'] = [{'RigIndex': j, 'RigWeight': float(w)}
                                          for j, w in sorted(used.items(), key=lambda x: -x[1])]
                else:
                    merged = {}
                    for bw in mesh.get('boneWeight') or []:
                        j = jmap.get(bw.get('RigIndex', 0), 0)
                        merged[j] = merged.get(j, 0.0) + float(bw.get('RigWeight', 0.0))
                    mesh['boneWeight'] = [{'RigIndex': j, 'RigWeight': w} for j, w in merged.items()]
            zm.from_json(json.dumps(md), msch, mod.path_for('%s/%s' % (BASE_DIR, m_['PathName'])))
        mdl2 = dict(mdl, Skeleton={'PathName': 'p0_base.trskl'})
        zm.from_json(json.dumps(mdl2), sch, mod.path_for(BASE_MDL))
        if not fit:                             # the character's mesh as it is, with its own bind pose
            sk_bytes, swapped = player_skeleton_bytes(root, char, ratio, os.path.join(src, skl_name))
            if swapped or ratio != 1.0:         # built like the player already: the game's skeleton stays as it is
                mod.add_bytes('%s/p0_base.trskl' % BASE_DIR, sk_bytes)
            say('player skeleton: the character\'s bind pose for %d bones%s' % (
                swapped, ', own height %.0f%% of the player' % (ratio * 100) if ratio != 1.0 else ''))
        moved = sum(1 for o, n_ in jmap.items() if next((x['Name'] for x in char['TransformNodes']
                                                         if x.get('JointInfoIndex') == o), '') not in
                    {x['Name'] for x in player['TransformNodes']})
        say('skinned to the player\'s skeleton (%d of its bones follow their nearest player bone)' % moved)
    say('base model: %s (%d files)' % (os.path.basename(char_rel), n))
    # 3. the player's own parts drawn as nothing (every mesh collapsed to a point)
    hidden = 0
    if hide_parts:
        parts = os.path.join(root, *PARTS_DIR.split('/'))
        for f in sorted(os.listdir(parts)):
            d = os.path.join(parts, f)
            if not os.path.isdir(d):
                continue
            n_mesh = 0
            for x in sorted(os.listdir(d)):
                if x.endswith('.trmsh'):
                    name, data = collapsed_buffer(os.path.join(d, x))
                    mod.add_bytes('%s/%s/%s' % (PARTS_DIR, f, name), data)
                    n_mesh += 1
            hidden += 1 if n_mesh else 0
        say('player face, hair and clothes hidden: %d models' % hidden)
    return {'character': char_rel, 'files': n, 'hidden': hidden}


NPC_CLASSES = {
    'ol': 'Office Worker', 'businessman': 'Office Worker', 'occult': 'Hex Maniac', 'collector': 'Collector',
    'driver': 'Driver', 'jogger': 'Jogger', 'waitress': 'Waitress', 'waiter': 'Waiter', 'backpacker': 'Backpacker',
    'elite': 'Ace Trainer', 'artist': 'Artist', 'tourist': 'Sightseer', 'police': 'Police Officer',
    'delivery': 'Courier', 'veteran': 'Veteran', 'hope': 'Rising Star', 'nu': 'Team Flare Nouveau',
    'nu_member': 'Team Flare Nouveau', 'jua': 'Fist of Justice', 'battlegirl': 'Battle Girl',
    'schoolkids': 'Student', 'girl': 'Girl', 'boy': 'Boy', 'pclady': 'Pokémon Center Lady', 'operator': 'Operator',
    'adult_a': 'Townsperson', 'adult_b': 'Townsperson', 'middle_a': 'Townsperson', 'old_a': 'Older townsperson',
    'child_a': 'Child', 'hunk': 'Townsperson (strong)', 'tall': 'Townsperson (tall)', 'fat': 'Townsperson (large)',
    'sleeves': 'Townsperson'}
# full names for outfit codes (gender included)
NPC_NAMES = {
    'operator_m': 'Construction Worker (Male)', 'jua_f': 'Fists of Justice Trainee (Female)',
    'girl_f': 'SBC Trainer (Female)', 'boy_m': 'SBC Trainer (Male)', 'nu_f': 'Team Flare Cafe Worker (Female)',
    'pclady_f': 'Pokémon Center Lady', 'schoolkids_m': 'Schoolkid (Male)', 'schoolkids_f': 'Schoolkid (Female)',
    'sleeves': 'Boutique Lady', 'battlegirl_f': 'Battle Girl', 'adult_a_m': 'Adult Male NPC',
    'adult_b_m': 'Adult Male NPC', 'middle_a_m': 'Middle-aged Male NPC', 'old_a_m': 'Old Male NPC',
    'adult_a_f': 'Adult Female NPC', 'adult_b_f': 'Adult Female NPC', 'middle_a_f': 'Middle-aged Female NPC',
    'old_a_f': 'Old Female NPC', 'child_a_f': 'Child Female NPC', 'child_a_m': 'Child Male NPC',
    'hunk_f': 'Hunk Female NPC', 'hunk_m': 'Hunk Male NPC', 'tall_m': 'Tall Male NPC', 'tall_f': 'Tall Female NPC'}
NPC_DIR = 'ik_chara/sav_cc_vr'
NPC_BODIES_THAT_FIT = ('bu', 'dm', 'fm', 'gm', 'rv')   # 'em' doesn't fit the player even resized


def npc_list(root):
    """[(ccdata path, body type)] of the trainers and townsfolk the game builds from parts."""
    import glob
    out = []
    for f in sorted(glob.glob(os.path.join(root, *NPC_DIR.split('/'), '*', '*.ccdata'))):
        try:
            d = json.loads(zm.to_json(f, zm.schema_for(f)))
            body = d['parts'][0]['model'].replace('\\', '/').split('/')[-2]
        except Exception:  # noqa: BLE001
            continue
        out.append((f, body))
    return out


def npc_label(root, ccdata):
    """'Hex Maniac (female)  -  tr0036 outfit 2' for a part-built NPC."""
    d = json.loads(zm.to_json(ccdata, zm.schema_for(ccdata)))
    drs = next((p['model'] for p in d['parts'] if p['name'] == 'drs'), '')
    code = drs.replace('\\', '/').split('/')[-2].split('_', 3)[-1] if drs else ''
    gender = {'m': 'male', 'f': 'female'}.get(code.rsplit('_', 1)[-1]) if '_' in code else None
    key = code.rsplit('_', 1)[0] if gender else code
    cls = NPC_CLASSES.get(key) or key.replace('_', ' ').capitalize() or 'Townsperson'
    name = os.path.splitext(os.path.basename(ccdata))[0]                 # tr0036_00_fa02
    m = re.match(r'^tr(\d{4})_\d\d_[a-z]+(\d\d)$', name)
    where = 'tr%s outfit %d' % (m.group(1), int(m.group(2)) + 1) if m else name
    if code in NPC_NAMES:
        return '%s  -  %s' % (NPC_NAMES[code], where)
    return '%s%s  -  %s' % (cls, ' (%s)' % gender if gender else '', where)


def assemble_npc(root, ccdata, out_dir):
    """One model of a part-built NPC (base body + face, outfit, hair) in out_dir, laid out like the named
    characters' folders: <stem>.trmdl / .trmsh (+_lod1, _lod2) / .trmbf / .trmtr / .trskl and its textures.
    Bones are numbered as the game numbers them for the NPC: the body's joints, then each part's own joints
    from its palette offset. Returns the folder."""
    import copy
    import shutil
    import za_trinity as zt
    d = json.loads(zm.to_json(ccdata, zm.schema_for(ccdata)))
    here = os.path.dirname(ccdata)
    name = os.path.splitext(os.path.basename(ccdata))[0]                 # e.g. tr0043_00_fa02
    stem = name[:9]
    os.makedirs(out_dir, exist_ok=True)
    target_dir = os.path.join(root, *BASE_DIR.split('/'))              # where the game will read the files
    base_mdl = os.path.normpath(os.path.join(here, d['parts'][0]['model']))
    base_dir = os.path.dirname(base_mdl)
    bm = json.loads(zm.to_json(base_mdl, zm.schema_for(base_mdl)))
    skl_path = os.path.join(base_dir, (bm.get('Skeleton') or {}).get('PathName') or
                            os.path.basename(base_mdl).replace('.trmdl', '.trskl'))
    skl_sch = zm.schema_for(skl_path)
    skel = json.loads(zm.to_json(skl_path, skl_sch))
    nodes, joints = skel['TransformNodes'], skel.setdefault('JointInfos', [])
    by_name = {n['Name']: i for i, n in enumerate(nodes)}
    lods = [dict(Meshes=[]) for _ in range(3)]
    buffers = [[] for _ in range(3)]
    materials, bounds, mesh_names = [], [], set()
    lod_info = None
    # the NPC's look: its first variant picks each part's pattern set and a colour for each colour group
    variant = (d.get('variants') or [{}])[0]
    look = {vp.get('part'): vp for vp in variant.get('parts') or []}
    for part in d['parts'][1:]:
        mdl = os.path.normpath(os.path.join(here, part['model']))
        folder = os.path.dirname(mdl)
        pm = json.loads(zm.to_json(mdl, zm.schema_for(mdl)))
        lod_info = lod_info or pm.get('LODs')
        tag = part['name']
        # skeleton: the part's own bones, after the body's, at the part's palette offset
        psk_path = os.path.join(folder, (pm.get('Skeleton') or {}).get('PathName', ''))
        if os.path.isfile(psk_path):
            off = zt.Skeleton(psk_path).palette_offset
            psk = json.loads(zm.to_json(psk_path, skl_sch))
            pn = psk['TransformNodes']
            for n in pn:
                if n['Name'] in by_name:
                    continue
                parent = n.get('ParentNodeName') or (pn[n['ParentNodeIndex']]['Name']
                                                     if n.get('ParentNodeIndex') is not None else '')
                nn = copy.deepcopy(n)
                nn['ParentNodeIndex'] = by_name.get(parent, by_name.get('head', 0))
                nn['ParentNodeName'] = ''
                j = n.get('JointInfoIndex')
                if j is not None and j < len(psk.get('JointInfos') or []):
                    g = off + j
                    while len(joints) <= g:
                        joints.append({'InfluenceSkinning': False, 'InverseBindPoseMatrix': {
                            'AxisX': {'X': 1.0, 'Y': 0.0, 'Z': 0.0}, 'AxisY': {'X': 0.0, 'Y': 1.0, 'Z': 0.0},
                            'AxisZ': {'X': 0.0, 'Y': 0.0, 'Z': 1.0}, 'AxisW': {'X': 0.0, 'Y': 0.0, 'Z': 0.0}}})
                    joints[g] = copy.deepcopy(psk['JointInfos'][j])
                    nn['JointInfoIndex'] = g
                else:
                    nn.pop('JointInfoIndex', None)
                by_name[n['Name']] = len(nodes)
                nodes.append(nn)
        # materials: names made unique per part, texture paths made to work from the player's base folder
        pmt_path = os.path.join(folder, (pm.get('Materials') or [''])[0])
        vp = look.get(tag) or {}
        sets, chosen = [], None
        if part.get('material_set'):
            mmt = os.path.normpath(os.path.join(here, part['material_set']))
            if os.path.isfile(mmt):
                try:
                    sets = zt.read_trmmt(mmt)
                except Exception:  # noqa: BLE001
                    sets = []
        want = vp.get('set') or part.get('texture_set') or ''
        chosen = next((st for st in sets if st['name'] == want), None)
        if chosen and chosen['trmtr'] and os.path.isfile(zt.resolve(chosen['trmtr'][0])):
            pmt_path = zt.resolve(chosen['trmtr'][0])            # the pattern set's own materials
        pmt = json.loads(zm.to_json(pmt_path, zm.schema_for(pmt_path)))
        if chosen:                                                # the variant's colours: palette cells
            for layer in vp.get('layers') or []:
                group = chosen['groups'].get(layer.get('color') or '') or {}
                k = int(layer.get('index', 0) or 0)
                for mname, params in group.items():
                    m = next((x for x in pmt.get('Materials') or [] if x.get('Name') == mname), None)
                    if m is None:
                        continue
                    ints = m.setdefault('IntParams', [])
                    for pname, values in params.items():
                        if 0 <= k < len(values):
                            e = next((x for x in ints if x.get('Name') == pname), None)
                            if e is None:
                                ints.append({'Name': pname, 'Value': values[k]})
                            else:
                                e['Value'] = values[k]
        for m in pmt.get('Materials') or []:
            m['Name'] = '%s_%s' % (tag, m['Name'])
            for tx in m.get('Textures') or []:
                f = tx.get('File') or ''
                if not f:
                    continue
                full = os.path.normpath(os.path.join(folder, f))
                if os.path.dirname(full) == os.path.normpath(folder):
                    if os.path.isfile(full):
                        shutil.copyfile(full, os.path.join(out_dir, os.path.basename(full)))
                    tx['File'] = os.path.basename(full)
                else:
                    tx['File'] = os.path.relpath(full, target_dir).replace(os.sep, '/')
            materials.append(m)
        if pm.get('Bounds'):
            bounds.append(pm['Bounds'])
        # meshes: each LOD of every part into one mesh file per LOD
        for k in range(3):
            ref = (pm.get('Meshes') or [])[min(k, len(pm.get('Meshes') or []) - 1)]['PathName']
            msh_path = os.path.join(folder, ref)
            msh = json.loads(zm.to_json(msh_path, zm.schema_for(msh_path)))
            buf_path = os.path.join(folder, msh.get('bufferFilePath') or ref.replace('.trmsh', '.trmbf'))
            mbf = json.loads(zm.to_json(buf_path, zm.schema_for(buf_path)))
            for mesh in msh.get('Meshes') or []:
                if mesh.get('Name') in mesh_names:
                    mesh['Name'] = '%s_%s' % (tag, mesh['Name'])
                mesh_names.add(mesh.get('Name'))
                for mp_ in mesh.get('meshParts') or []:
                    mp_['MaterialName'] = '%s_%s' % (tag, mp_.get('MaterialName', ''))
                lods[k]['Meshes'].append(mesh)
            buffers[k] += mbf.get('TRMeshBuffers') or []
    lod_names = [stem + ('.trmsh' if k == 0 else '_lod%d.trmsh' % k) for k in range(3)]
    for k in range(3):
        bname = lod_names[k].replace('.trmsh', '.trmbf')
        lods[k]['bufferFilePath'] = bname
        zm.from_json(json.dumps(lods[k]), zm.schema_for(os.path.join(target_dir, 'x.trmsh')),
                     os.path.join(out_dir, lod_names[k]))
        zm.from_json(json.dumps({'TRMeshBuffers': buffers[k]}), zm.schema_for(os.path.join(target_dir, 'x.trmbf')),
                     os.path.join(out_dir, bname))
    zm.from_json(json.dumps({'Materials': materials}), zm.schema_for(os.path.join(target_dir, 'x.trmtr')),
                 os.path.join(out_dir, stem + '.trmtr'))
    zm.from_json(json.dumps(skel), skl_sch, os.path.join(out_dir, stem + '.trskl'))
    lo = {a: min(b['MinBound'].get(a, 0.0) for b in bounds) for a in 'XYZ'} if bounds else {}
    hi = {a: max(b['MaxBound'].get(a, 0.0) for b in bounds) for a in 'XYZ'} if bounds else {}
    mdl = {'Meshes': [{'PathName': n} for n in lod_names], 'Skeleton': {'PathName': stem + '.trskl'},
           'Materials': [stem + '.trmtr'], 'LODs': lod_info or [{'Entries': [{}, {'Index': 1}, {'Index': 2}],
                                                                  'Type': 'Custom'}],
           'Bounds': {'MinBound': lo, 'MaxBound': hi}}
    zm.from_json(json.dumps(mdl), zm.schema_for(base_mdl), os.path.join(out_dir, stem + '.trmdl'))
    return out_dir


def open_window(app):
    """Mods tab > Play as a character...: pick a character, see it in 3D, put it into the current mod."""
    import threading
    import tkinter as tk
    from tkinter import messagebox, ttk
    root = os.path.normpath(app.root_var.get())
    w = tk.Toplevel(app)
    w.title('Play as a character')
    w.geometry('560x560')
    bg = ttk.Style(w).lookup('TFrame', 'background') or None
    if bg:
        w.configure(background=bg)
    f = ttk.Frame(w, padding=12)
    f.pack(fill='both', expand=True)
    ttk.Label(f, text=zm.EXPERIMENTAL, style='Warn.TLabel', wraplength=530, justify='left').pack(fill='x', pady=(0, 6))
    ttk.Label(f, wraplength=530, justify='left', text=(
        'Your player becomes one of the game\'s characters. Its own face, hair and clothes are hidden, '
        'whatever the save wears, so only the character is drawn. Pick one, "Show in 3D" to see it, then '
        '"Put into current mod".')).pack(fill='x')
    lf = ttk.Frame(f)
    lf.pack(fill='both', expand=True, pady=8)
    lst = tk.Listbox(lf, height=16, exportselection=False, activestyle='none', borderwidth=0, highlightthickness=0)
    sbar = ttk.Scrollbar(lf, orient='vertical', command=lst.yview)
    lst.configure(yscrollcommand=sbar.set)
    sbar.pack(side='right', fill='y')
    lst.pack(side='left', fill='both', expand=True)
    try:
        c = app.theme_colours()              # the program's light / dark theme
        lst.configure(background=c['field'], foreground=c['fg'], selectbackground=c['sel'], selectforeground=c['selfg'])
    except Exception:  # noqa: BLE001
        pass
    status = tk.StringVar(value='Reading the characters…')
    hide = tk.BooleanVar(value=True)
    ttk.Checkbutton(f, text="Hide the player's own face, hair and clothes", variable=hide).pack(anchor='w')
    keep = tk.BooleanVar(value=False)
    ttk.Checkbutton(f, text='Keep what the character holds in its hands (briefcase, cane…) - off: both hands free',
                    variable=keep).pack(anchor='w')
    bar = ttk.Frame(f)
    bar.pack(fill='x', pady=(8, 0))
    ttk.Label(f, textvariable=status, style='Dim.TLabel', wraplength=530, justify='left').pack(fill='x', pady=(6, 0))
    chars = []

    def cur():
        s = lst.curselection()
        return chars[s[0]] if s else None

    def show():
        c = cur()
        if not c:
            return
        if c[1].endswith('.ccdata'):                     # an NPC: put its parts together first
            import tempfile
            tmp = tempfile.mkdtemp(prefix='playas_view_')
            status.set('Putting %s together…' % c[0].split('  -  ')[0])

            def work():
                try:
                    folder = npc_folder(root, c[1], tmp)
                    stem = os.path.basename(folder)[:9]
                    path = os.path.join(folder, stem + '.trmdl')
                    app.q.put(lambda: (status.set(''), app.open_viewer(path, with_mod=False, extra=['--root', root])))
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    app.q.put(lambda: status.set('Could not show it: ' + msg))
            threading.Thread(target=work, daemon=True).start()
            return
        stem = re.match(r'^(tr\d{4}_\d\d)', os.path.basename(c[1])).group(1)
        app.open_viewer(os.path.join(root, *c[1].split('/'), stem + '.trmdl'), with_mod=False)

    def put():
        c = cur()
        if not c:
            messagebox.showinfo('Play as a character', 'Pick a character first.', parent=w)
            return
        m = app.current_mod()
        if not m:
            return
        if any(r.startswith(BASE_DIR + '/') for r in m.files()) and not messagebox.askyesno(
                'Play as a character', 'Mod "%s" already changes the player model. Replace that?' % m.name,
                parent=w):
            return
        h, kh = hide.get(), keep.get()

        def job():
            m.snapshot()
            remove(root, m)
            r = make(root, m, c[1], hide_parts=h, keep_held=kh)
            m.snapshot('play as %s' % c[0])
            return r

        def done(r):
            app.mod_changed(m, BASE_MDL, 'player is now %s' % c[0].split('  -  ')[0])
            try:
                status.set('Done: in "%s" the player is %s%s. Install the mod and load a copy of your save.' % (
                    m.name, c[0].split('  -  ')[0], ' (own face, hair and clothes hidden: %d models)' % r['hidden']
                    if r['hidden'] else ''))
            except tk.TclError:
                pass
        app.run_bg('Making the player model', job, done)

    def undo():
        m = app.current_mod()
        if not m:
            return

        def job():
            m.snapshot()
            n = remove(root, m)
            m.snapshot('player model change removed')
            return n

        def done(n):
            app.mod_changed(m, BASE_MDL, 'player model change removed')
            try:
                status.set('Removed %d file(s) from "%s": the player looks as normal again.' % (n, m.name))
            except tk.TclError:
                pass
        app.run_bg('Removing the player model change', job, done)

    ttk.Button(bar, text='Show in 3D', command=show).pack(side='left')
    ttk.Button(bar, text='Remove from mod', command=undo).pack(side='left', padx=6)
    ttk.Button(bar, text='Put into current mod', command=put).pack(side='right')
    lst.bind('<Double-1>', lambda e: show())

    def load():
        try:
            r = characters(root)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            app.q.put(lambda: status.set('Could not read the characters: ' + msg))
            return

        def fill():
            chars[:] = r
            for label, _ in r:
                lst.insert('end', label)
            status.set('%d characters.' % len(r))
        app.q.put(fill)
    threading.Thread(target=load, daemon=True).start()
    return w


def remove(root, mod):
    """Take the player model change out of `mod` again."""
    gone = 0
    for rel in list(mod.files()):
        if rel.startswith(BASE_DIR + '/') or (rel.startswith(PARTS_DIR + '/') and rel.endswith(('.trmdl', '.trmbf'))):
            mod.remove(rel)
            gone += 1
    return gone
