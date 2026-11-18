"""Map tab: create a new spawn point where you clicked - a Pokémon spawner, an item ball, random items, a move
gimmick or an NPC - through a short step-by-step menu.

Every kind of point is four pieces: its position (a *_spawner_transform row), its spawner row ("id_" + name, or
the NPC's own id), what it spawns (an encounter, an item table, a gimmick type or an NPC look) and its entry in
the area's scene file, which is what makes the game place it. A new point copies the nearest point of the same
kind (so it gets that area's scene file and conditions), takes your spot on the map and its own name, and gets
its own data, set from the menu. The height (Y) comes from that nearest point - check it in the Y field.
"""
import copy
import glob
import json
import os
import random
import re
import struct
import tkinter as tk
from tkinter import messagebox, ttk

import za_modding as zm

FORCE = [{'element': [{'param': [{'condition': 'force_condition'}]}]}]

# kind -> (button label, transform layer stem, spawner table key, scene file pattern, description)
KINDS = {
    'pokemon': ('Pokémon Spawner', 'pokemon_spawner_transform_array', 'spawner', '*pokemon*spawner*.trscn',
                'A wild Pokémon appears here: species, form, levels, size, alpha.'),
    'itemball': ('Item Ball', 'itemball_spawner_transform_array', 'ib_spawner', '*item_ball_spawner*.trscn',
                 'A Poké Ball item on the ground with the item and count you pick.'),
    'random': ('Random Items', 'random_pop_item_spawner_transform_array', 'rp_spawner', '*random_pop_item_spawner*.trscn',
               'Sparkling items that pop up again and again, picked from a list by weight.'),
    'gimmick': ('Move Gimmick', 'wazagimmick_spawner_transform_array', 'wg_spawner', '*waza_gimmick*.trscn',
                'An object Pokémon moves interact with (rocks, boxes, crates...).'),
    'npc': ('NPC', 'placement_npc_spawner_transform_array', 'npc_spawner', '*placement_npc_spawner*.trscn',
            'A person standing here: their look, when they appear, and how they behave.'),
}


# ---- table helpers ------------------------------------------------------------------------------------------

def rows(table):
    for g in table.data.get('values') or []:
        for r in g.get('root') or []:
            yield r


def object_names(row):
    """Every objectName inside a spawner row (any kind)."""
    out = []

    def walk(x):
        if isinstance(x, dict):
            if isinstance(x.get('objectName'), str):
                out.append(x['objectName'])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(row)
    return out


def spawner_of(table, name):
    for g in table.data.get('values') or []:
        rr = g.get('root') or []
        for i, r in enumerate(rr):
            if name in object_names(r):
                return rr, i, r
    return None, None, None


def renamed(x, old, new):
    """A copy with every string equal to old (or id_old) renamed."""
    if isinstance(x, dict):
        return {k: renamed(v, old, new) for k, v in x.items()}
    if isinstance(x, list):
        return [renamed(v, old, new) for v in x]
    if x == old:
        return new
    if x == 'id_' + old:
        return 'id_' + new
    return x


def new_name(old, taken):
    """old with its trailing number replaced by an unused one of the same width (counting down from 99..9), so
    the name keeps its length (scene files hold it in fixed-size data)."""
    m = re.search(r'(\d+)$', old)
    if not m or len(m.group(1)) < 2:
        return None
    w = len(m.group(1))
    for n in range(10 ** w - 1, 0, -1):
        cand = old[:m.start()] + '%0*d' % (w, n)
        if cand not in taken:
            return cand
    return None


# ---- creating ------------------------------------------------------------------------------------------------

def layer_for(mt, kind):
    stem = KINDS[kind][1]
    return next((L for L in mt.layers if L.stem == stem), None)


def candidates(mt, kind, x, z):
    """Points of this kind nearest to (x, z) that have a spawner row: [(dist, layer, gi, si, point)]."""
    L = layer_for(mt, kind)
    table = mt.tables.get(KINDS[kind][2])
    if L is None or table is None:
        return []
    with_spawner = {n for r in rows(table) for n in object_names(r)}
    out = []
    for gi, si, p in L.points():
        if p.get('Name') in with_spawner:
            px, py, pz = _pos(p)
            out.append(((px - x) ** 2 + (pz - z) ** 2, L, gi, si, p))
    out.sort(key=lambda t: t[0])
    return out


def _pos(p):
    q = p.get('Position') or {}
    return q.get('X', 0.0), q.get('Y', 0.0), q.get('Z', 0.0)


def taken_names(mt):
    taken = {p.get('Name') for L in mt.layers for _, _, p in L.points()}
    for t in mt.tables.values():
        for r in rows(t):
            taken |= set(object_names(r))
    for other in zm.list_mods():                     # points other mods add: two mods never make the same one
        for L in mt.layers:
            path = other.path_for(L.rel)
            if os.path.isfile(path) and os.path.normcase(other.romfs) != os.path.normcase(mt.romfs or ''):
                try:
                    d = json.loads(zm.to_json(path, zm.schema_for(L.path)))
                    taken |= {r.get('Name') for g in d.get('values') or [] for r in g.get('root') or []}
                except Exception:  # noqa: BLE001
                    pass
    return taken


def create(mt, kind, x, z, spec):
    """Make the new point. Returns (new name, template name). Raises ValueError with a message for the user."""
    table = mt.tables.get(KINDS[kind][2])
    if table is None:
        raise ValueError('The %s table could not be read.' % KINDS[kind][0])
    template = None
    for _, L, gi, si, p in candidates(mt, kind, x, z)[:40]:
        old = p.get('Name', '')
        if not re.search(r'\d\d$', old):
            continue
        rel = mt.find_scene(old, KINDS[kind][3])
        if rel:
            template = (L, gi, si, p, rel)
            break
    if template is None:
        raise ValueError('No %s near here can be copied (none is placed by a scene file the toolkit can find).'
                         % KINDS[kind][0].lower())
    L, gi, si, p, rel = template
    old = p['Name']
    new = new_name(old, taken_names(mt))
    if not new:
        raise ValueError('No free name left for a copy of %s.' % old)
    sc = mt.scene(rel)
    chunks = sc[0]['Chunks']
    ci = next((i for i, c in enumerate(chunks) if old.encode() in bytes(c.get('Data', []))), None)
    px, py, pz = _pos(p)
    off = mt._pos_offset(chunks[ci]['Data'], px, py, pz) if ci is not None else None
    if off is None:
        raise ValueError("Could not find where %s's position is stored in its scene file." % old)
    # 1. position
    q = copy.deepcopy(p)
    q['Name'] = new
    q['Position'] = {'X': float(x), 'Y': float(py), 'Z': float(z)}
    L.data['values'][gi]['root'].insert(si + 1, q)
    mt.mark_dirty(L)
    # 2. spawner (+ 3. what it spawns)
    rr, i, srow = spawner_of(table, old)
    s = renamed(copy.deepcopy(srow), old, new)
    if kind == 'npc':                                   # only this NPC, not the group it stood in
        s['id'] = 'npc_' + new
        for cond in s.get('appearingConditionList') or []:
            cond['spawnerObjectInfoList'] = [o for o in cond.get('spawnerObjectInfoList') or []
                                             if o.get('objectName') == new]
        s['appearingConditionList'] = [c for c in s.get('appearingConditionList') or []
                                       if c.get('spawnerObjectInfoList')][:1]
    APPLY[kind](mt, s, new, spec)
    rr.insert(i + 1, s)
    table.dirty = True
    # 4. scene: the template's chunk tree, renamed, with fresh ids for chunks that belong only to it, moved
    ids = []

    def all_ids(cs):
        for c in cs:
            ids.append(c.get('Id'))
            all_ids(c.get('Children') or [])
    all_ids(chunks)
    unique = {k for k in ids if ids.count(k) == 1}
    used = set(ids)
    rng = random.Random(hash(new) & 0xFFFFFFFF)

    def rename(c):
        b = bytes(c.get('Data', []))
        if old.encode() in b:
            c['Data'] = list(b.replace(old.encode(), new.encode()))
        if c.get('Id') in unique:
            nid = rng.getrandbits(64)
            while nid in used:
                nid = rng.getrandbits(64)
            used.add(nid)
            c['Id'] = nid
        for ch in c.get('Children') or []:
            rename(ch)
    nc = copy.deepcopy(chunks[ci])
    rename(nc)
    b = bytearray(nc['Data'])
    struct.pack_into('<3f', b, off, x, py, z)
    nc['Data'] = list(b)
    chunks.insert(ci + 1, nc)
    mt.__dict__.setdefault('scene_dirty', set()).add(rel)
    mt.__dict__.setdefault('new_points', {})[new] = (rel, off)
    mt.index_spawn_tables()
    mt.spawn_info = {n: mt.encounter_lines(n) for n in mt.spawner_by_obj}
    mt.sel = (L, gi, si + 1)
    return new, old


def _apply_pokemon(mt, s, new, spec):
    info = (s.get('encountDataInfoList') or [{}])[0]
    e0 = mt.enc_by_id.get(info.get('encountDataId')) or {}
    if not e0:
        raise ValueError('The nearest Pokémon spawner has no encounter to copy.')
    enc = copy.deepcopy(e0)
    enc['id'] = 'ect_' + new
    enc['devNo'] = spec['species']
    if spec.get('form'):
        enc['formNo'] = spec['form']
    else:
        enc.pop('formNo', None)
    enc['minLevel'], enc['maxLevel'] = spec['min'], max(spec['min'], spec['max'])
    enc['talentScale'] = spec['size']                 # -1 = random size, 0-255 = this size
    if spec.get('alpha'):
        enc['oyabunProbability'] = 100.0
        enc.setdefault('oyabunAdditionalLevel', 10)
    groups = mt.tables['enc'].data['values']
    eg = next((g for g in groups if e0 in (g.get('root') or [])), groups[0])
    eg.setdefault('root', []).append(enc)
    mt.tables['enc'].dirty = True
    info = copy.deepcopy(info)
    info['encountDataId'] = enc['id']
    info['weight'] = info.get('weight') or 100
    s['encountDataInfoList'] = [info]


def _item_table(mt, key, tid, items):
    t = mt.tables[key]
    groups = t.data['values']
    groups[0].setdefault('root', []).append({
        'id': tid, 'itemLotteryDataList': [{'itemId': str(i), 'weight': w, 'maxCount': n,
                                            'activationCondition': copy.deepcopy(FORCE)} for i, n, w in items]})
    t.dirty = True


def _apply_itemball(mt, s, new, spec):
    tid = 'lt_' + new
    _item_table(mt, 'ib_items', tid, [(spec['item'], spec['count'], 100)])
    s['tableInfoList'] = [{'tableId': tid, 'activationCondition': copy.deepcopy(FORCE)}]
    s.pop('activationCondition', None)


def _apply_random(mt, s, new, spec):
    tid = 'lt_' + new
    _item_table(mt, 'rp_items', tid, [(i, 1, w) for i, w in spec['items']])
    s['tableInfoList'] = [{'tableId': tid, 'activationCondition': copy.deepcopy(FORCE)}]
    s['activationCondition'] = copy.deepcopy(FORCE)
    for o in s.get('appearanceSpawnerObjectInfoList') or []:
        o['appearanceInfo'] = {'minCount': spec['min'], 'maxCount': max(spec['min'], spec['max'])}


def _apply_gimmick(mt, s, new, spec):
    s['activationCondition'] = copy.deepcopy(FORCE)
    s['spawnWazagimmickInfoList'] = [{'privateId': spec['gimmick'], 'weight': 100, 'maxCount': 1,
                                      'activationCondition': copy.deepcopy(FORCE)}]


def _apply_npc(mt, s, new, spec):
    s['assetId'] = spec['asset']
    for cond in s.get('appearingConditionList') or []:
        cond['element'] = copy.deepcopy(FORCE)[0]['element']
        cond['dayNightFlag'] = {} if spec['when'] == 'always' else {'dayNightFlag': 1 if spec['when'] == 'day' else 2}
        for o in cond.get('spawnerObjectInfoList') or []:
            if spec.get('talk') and spec['talk'] != 'keep':
                o['tagInfoList'] = [{'tag': spec['talk']}] if spec['talk'] else []


APPLY = {'pokemon': _apply_pokemon, 'itemball': _apply_itemball, 'random': _apply_random,
         'gimmick': _apply_gimmick, 'npc': _apply_npc}


# ---- choices for the menu ------------------------------------------------------------------------------------

def gimmick_types(mt):
    t = mt.tables.get('wg_spawner')
    seen = {}
    for r in rows(t) if t else []:
        for g in r.get('spawnWazagimmickInfoList') or []:
            pid = g.get('privateId')
            if pid:
                seen[pid] = seen.get(pid, 0) + 1
    return sorted(seen)


def npc_assets(root):
    p = glob.glob(os.path.join(root, 'world', 'ik_data', 'field', 'npc', 'npc_asset_data', '**', 'npc_asset_data.bin'),
                  recursive=True)
    if not p:
        return []
    d = json.loads(zm.to_json(p[0], zm.schema_for(p[0])))
    k = next(k for k in d if isinstance(d[k], list))
    return sorted({r.get('assetId') for r in d[k] if r.get('assetId')})


def npc_talk_tags(mt):
    t = mt.tables.get('npc_spawner')
    seen = {}
    for r in rows(t) if t else []:
        for c in r.get('appearingConditionList') or []:
            for o in c.get('spawnerObjectInfoList') or []:
                for tg in o.get('tagInfoList') or []:
                    seen[tg.get('tag')] = seen.get(tg.get('tag'), 0) + 1
    return sorted((k for k in seen if k), key=lambda k: -seen[k])


# ---- the menu ------------------------------------------------------------------------------------------------

def open_menu(mt, x, z):
    """Step 1: what to create. Step 2: its options. Then it is created and selected."""
    app = mt.app
    w = tk.Toplevel(app)
    w.title('Create spawn point')
    w.transient(app)
    w.geometry('520x560')
    bg = ttk.Style(w).lookup('TFrame', 'background') or None
    if bg:
        w.configure(background=bg)
    body = ttk.Frame(w, padding=14)
    body.pack(fill='both', expand=True)

    def clear():
        for c in body.winfo_children():
            c.destroy()

    def step1():
        clear()
        ttk.Label(body, text=zm.EXPERIMENTAL, style='Warn.TLabel', wraplength=480, justify='left').pack(anchor='w',
                                                                                                   pady=(0, 6))
        ttk.Label(body, text='Create a spawn point at X %.1f, Z %.1f' % (x, z), style='Head.TLabel').pack(anchor='w')
        ttk.Label(body, style='Dim.TLabel', wraplength=480, justify='left', text=(
            'What should be here? It copies the nearest point of that kind (its area, scene and height), so '
            'check the height (Y) afterwards.')).pack(anchor='w', pady=(2, 10))
        for kind, (label, _, key, _, desc) in KINDS.items():
            row = ttk.Frame(body)
            row.pack(fill='x', pady=3)
            b = ttk.Button(row, text=label, width=18, command=lambda k=kind: step2(k))
            b.pack(side='left')
            if layer_for(mt, kind) is None or mt.tables.get(key) is None:
                b.state(['disabled'])
            ttk.Label(row, text=desc, wraplength=320, justify='left').pack(side='left', padx=8)
        ttk.Button(body, text='Cancel', command=w.destroy).pack(side='bottom', anchor='e')

    def finish(kind, spec):
        try:
            new, old = create(mt, kind, x, z, spec)
        except ValueError as e:
            messagebox.showerror('Create spawn point', str(e), parent=w)
            return
        w.destroy()
        mt.show_sel()
        mt.redraw()
        n = sum(1 for x_ in mt.layers if x_.dirty) + sum(1 for t in mt.tables.values() if t.dirty)
        mt.dirty_var.set('%d table(s) and %d scene(s) changed - not saved yet' % (n, len(mt.scene_dirty)))
        app.status_var.set('Created %s %s (a copy of %s, with its own data). Check its height (Y), tick Edit mode to '
                           'move it, then Save changes to current mod.' % (KINDS[kind][0].lower(), new, old))

    def nav(kind, get_spec):
        bar = ttk.Frame(body)
        bar.pack(side='bottom', fill='x', pady=(10, 0))

        def ok():
            try:
                spec = get_spec()
            except (ValueError, tk.TclError) as e:
                messagebox.showerror('Create spawn point', str(e) or 'Check the numbers.', parent=w)
                return
            finish(kind, spec)
        ttk.Button(bar, text='Create', command=ok).pack(side='right')
        ttk.Button(bar, text='Back', command=step1).pack(side='right', padx=6)

    def filtered(box, values):
        def f(_e=None):
            q = box.get().lower()
            box.configure(values=[v for v in values if q in v.lower()][:500] if q else values)
        box.bind('<KeyRelease>', f)

    def step2(kind):
        clear()
        ttk.Label(body, text=KINDS[kind][0], style='Head.TLabel').pack(anchor='w', pady=(0, 8))
        g = ttk.Frame(body)
        g.pack(fill='x')
        if kind == 'pokemon':
            names = list(mt.species_names)
            ttk.Label(g, text='Pokémon:').grid(row=0, column=0, sticky='w')
            sp = ttk.Combobox(g, values=names, width=30)
            sp.grid(row=0, column=1, columnspan=3, sticky='w', pady=2)
            filtered(sp, names)
            form = tk.IntVar(value=0)
            ttk.Label(g, text='Form:').grid(row=1, column=0, sticky='w')
            ttk.Spinbox(g, from_=0, to=30, textvariable=form, width=5).grid(row=1, column=1, sticky='w', pady=2)
            ttk.Label(g, text='0 = the normal form', style='Dim.TLabel').grid(row=1, column=2, columnspan=2, sticky='w')
            lo, hi = tk.IntVar(value=5), tk.IntVar(value=10)
            ttk.Label(g, text='Levels:').grid(row=2, column=0, sticky='w')
            ttk.Spinbox(g, from_=1, to=100, textvariable=lo, width=5).grid(row=2, column=1, sticky='w', pady=2)
            ttk.Label(g, text='to').grid(row=2, column=2, sticky='w')
            ttk.Spinbox(g, from_=1, to=100, textvariable=hi, width=5).grid(row=2, column=3, sticky='w')
            rnd = tk.BooleanVar(value=True)
            size = tk.IntVar(value=128)
            ttk.Label(g, text='Size:').grid(row=3, column=0, sticky='nw', pady=(6, 0))
            sf = ttk.Frame(g)
            sf.grid(row=3, column=1, columnspan=3, sticky='w', pady=(6, 0))
            ttk.Checkbutton(sf, text='Random (like wild Pokémon)', variable=rnd).pack(anchor='w')
            sl = ttk.Frame(sf)
            sl.pack(anchor='w')
            ttk.Label(sl, text='tiny').pack(side='left')
            ttk.Scale(sl, from_=0, to=255, variable=size, length=200).pack(side='left', padx=4)
            ttk.Label(sl, text='huge').pack(side='left')
            alpha = tk.BooleanVar(value=False)
            ttk.Checkbutton(g, text='Always an alpha', variable=alpha).grid(row=4, column=1, columnspan=3, sticky='w',
                                                                          pady=(8, 0))

            def spec():
                m = re.search(r'#(\d+)\)?\s*$', sp.get())
                if not m:
                    raise ValueError('Pick a Pokémon from the list.')
                return {'species': int(m.group(1)), 'form': int(form.get()), 'min': int(lo.get()),
                        'max': int(hi.get()), 'size': -1 if rnd.get() else int(size.get()), 'alpha': alpha.get()}
            nav(kind, spec)
        elif kind == 'itemball':
            items = ['%s (#%d)' % (n, i) for i, n in sorted(mt.item_names.items()) if n and i]
            ttk.Label(g, text='Item:').grid(row=0, column=0, sticky='w')
            it = ttk.Combobox(g, values=items, width=34)
            it.grid(row=0, column=1, sticky='w', pady=2)
            filtered(it, items)
            cnt = tk.IntVar(value=1)
            ttk.Label(g, text='How many:').grid(row=1, column=0, sticky='w')
            ttk.Spinbox(g, from_=1, to=99, textvariable=cnt, width=5).grid(row=1, column=1, sticky='w', pady=2)

            def spec():
                m = re.search(r'#(\d+)\)\s*$', it.get())
                if not m:
                    raise ValueError('Pick an item from the list.')
                return {'item': int(m.group(1)), 'count': int(cnt.get())}
            nav(kind, spec)
        elif kind == 'random':
            items = ['%s (#%d)' % (n, i) for i, n in sorted(mt.item_names.items()) if n and i]
            ttk.Label(g, text='Items that can pop up (the weight decides how often each one is picked):',
                      wraplength=480).grid(row=0, column=0, columnspan=3, sticky='w')
            lst = ttk.Treeview(g, columns=('w',), height=6, selectmode='browse')
            lst.heading('#0', text='Item')
            lst.heading('w', text='Weight')
            lst.column('#0', width=300)
            lst.column('w', width=70)
            lst.grid(row=1, column=0, columnspan=3, sticky='ew', pady=4)
            it = ttk.Combobox(g, values=items, width=28)
            it.grid(row=2, column=0, sticky='w')
            filtered(it, items)
            wv = tk.IntVar(value=100)
            ttk.Spinbox(g, from_=1, to=1000, textvariable=wv, width=6).grid(row=2, column=1, sticky='w', padx=4)

            def add():
                if re.search(r'#(\d+)\)\s*$', it.get()):
                    lst.insert('', 'end', text=it.get(), values=(int(wv.get()),))
            ttk.Button(g, text='Add', command=add).grid(row=2, column=2, sticky='w')
            ttk.Button(g, text='Remove selected', command=lambda: [lst.delete(i) for i in lst.selection()]).grid(
                row=3, column=0, sticky='w', pady=2)
            lo, hi = tk.IntVar(value=2), tk.IntVar(value=4)
            cf = ttk.Frame(g)
            cf.grid(row=4, column=0, columnspan=3, sticky='w', pady=(8, 0))
            ttk.Label(cf, text='Items shown at once:').pack(side='left')
            ttk.Spinbox(cf, from_=1, to=10, textvariable=lo, width=4).pack(side='left', padx=4)
            ttk.Label(cf, text='to').pack(side='left')
            ttk.Spinbox(cf, from_=1, to=10, textvariable=hi, width=4).pack(side='left', padx=4)

            def spec():
                chosen = [(int(re.search(r'#(\d+)\)\s*$', lst.item(i, 'text')).group(1)), int(lst.item(i, 'values')[0]))
                          for i in lst.get_children()]
                if not chosen:
                    raise ValueError('Add at least one item.')
                return {'items': chosen, 'min': int(lo.get()), 'max': int(hi.get())}
            nav(kind, spec)
        elif kind == 'gimmick':
            types = gimmick_types(mt)
            ttk.Label(g, text='Gimmick:').grid(row=0, column=0, sticky='w')
            gb = ttk.Combobox(g, values=types, width=34)
            gb.grid(row=0, column=1, sticky='w', pady=2)
            filtered(gb, types)
            ttk.Label(g, style='Dim.TLabel', wraplength=460, justify='left', text=(
                'The game\'s own gimmick names (box, rock, crate...). Look at an existing move gimmick on the map to '
                'see which one is which.')).grid(row=1, column=0, columnspan=2, sticky='w', pady=4)

            def spec():
                if gb.get() not in types:
                    raise ValueError('Pick a gimmick from the list.')
                return {'gimmick': gb.get()}
            nav(kind, spec)
        elif kind == 'npc':
            assets = npc_assets(os.path.normpath(app.root_var.get()))
            ttk.Label(g, text='Look:').grid(row=0, column=0, sticky='w')
            ab = ttk.Combobox(g, values=assets, width=34)
            ab.grid(row=0, column=1, sticky='w', pady=2)
            filtered(ab, assets)
            when = tk.StringVar(value='always')
            ttk.Label(g, text='Appears:').grid(row=1, column=0, sticky='w')
            wf = ttk.Frame(g)
            wf.grid(row=1, column=1, sticky='w', pady=2)
            for v, t in (('always', 'always'), ('day', 'by day'), ('night', 'at night')):
                ttk.Radiobutton(wf, text=t, value=v, variable=when).pack(side='left', padx=(0, 8))
            tags = ['keep'] + npc_talk_tags(mt)
            ttk.Label(g, text='Behaviour:').grid(row=2, column=0, sticky='w')
            tb = ttk.Combobox(g, values=tags, width=34, state='readonly')
            tb.set('keep')
            tb.grid(row=2, column=1, sticky='w', pady=2)
            ttk.Label(g, style='Dim.TLabel', wraplength=460, justify='left', text=(
                '"keep" behaves like the nearest NPC (what it says and does comes from the game\'s scripts). Other '
                'entries are the game\'s talk tags, e.g. TalkNpcEmpty = can be talked to. Custom lines and item '
                'gifts need the game\'s event scripts and can\'t be set here yet.')).grid(
                row=3, column=0, columnspan=2, sticky='w', pady=4)

            def spec():
                if ab.get() not in assets:
                    raise ValueError('Pick a look from the list.')
                return {'asset': ab.get(), 'when': when.get(), 'talk': tb.get()}
            nav(kind, spec)
    step1()
    w.bind('<Escape>', lambda e: w.destroy())
    return w
