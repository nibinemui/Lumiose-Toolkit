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
NICE = {'hotel_manager': 'Hotel manager', 'president': 'President', 'secretary': 'Vinnie (secretary)',
        'friend_f': 'Lida', 'friend_m': 'Naveen', 'rival_f': 'Taunie', 'rival_m': 'Urbain',
        'detective': 'Emma', 'researcher': 'Researcher', 'co_member_m': 'Member of group CO (male)',
        'co_member_f': 'Member of group CO (female)', 'f': 'Woman', 'co': 'Member of group CO',
        'ca': 'Member of group CA'}


class PlayAsError(Exception):
    pass


def _bones(path):
    d = json.loads(zm.to_json(path, zm.schema_for(path)))
    return {n.get('Name') for n in d.get('TransformNodes') or [] if n.get('Name')}


def characters(root):
    """[(label, game folder)] of every human character model that can stand in for the player."""
    out = []
    player = _bones(os.path.join(root, *BASE_DIR.split('/'), 'p0_base.trskl'))
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
            what = m.group(3) or ''
            label = NICE.get(what)
            if label is None:
                b = re.match(r'^([a-z]+)_(boss|leader)(?:_([mf]))?$', what)
                label = ('%s of group %s%s' % (b.group(2).capitalize(), b.group(1).upper(),
                                                {'m': ' (male)', 'f': ' (female)'}.get(b.group(3), ''))
                         if b else what.replace('_', ' ').capitalize() if what else 'Character')
            if m.group(2) != '00':
                label += ' (variant %d)' % int(m.group(2))
            out.append(('%s  -  tr%s' % (label, m.group(1)), '%s/%s' % (d, f)))
    return out


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


def make(root, mod, char_rel, hide_parts=True, log=None, reorder=False, bind_player=True):
    """Make `mod` turn the player into the character in game folder `char_rel` (from characters())."""
    say = log or (lambda s: None)
    src = os.path.join(root, *char_rel.split('/'))
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
        for m_ in mdl.get('Meshes') or []:
            mp = os.path.join(src, m_['PathName'])
            with open(mp, 'rb') as f:
                bname = zt.Table.root(f.read()).string(2)
            with open(os.path.join(src, bname), 'rb') as f:
                data = f.read()
            mod.add_bytes('%s/%s' % (BASE_DIR, bname), renumber_joints(mp, data, jmap))
            msch = zm.schema_for(mp)
            md = json.loads(zm.to_json(mp, msch))
            for mesh in md.get('Meshes') or []:
                for bw in mesh.get('boneWeight') or []:
                    bw['RigIndex'] = jmap.get(bw.get('RigIndex', 0), 0)
            zm.from_json(json.dumps(md), msch, mod.path_for('%s/%s' % (BASE_DIR, m_['PathName'])))
        mdl2 = dict(mdl, Skeleton={'PathName': 'p0_base.trskl'})
        zm.from_json(json.dumps(mdl2), sch, mod.path_for(BASE_MDL))
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
        h = hide.get()

        def job():
            m.snapshot()
            remove(root, m)
            r = make(root, m, c[1], hide_parts=h)
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
