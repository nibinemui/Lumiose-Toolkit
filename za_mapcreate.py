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


def ground_height(mt, x, z, fallback):
    """The height to put a new point at: the middle height of the points closest to (x, z) of any kind (within
    20 m) - the nearest point of the same kind may be on a roof or a ledge."""
    near = []
    for L in mt.layers:
        for _, _, p in L.points():
            px, py, pz = _pos(p)
            d = (px - x) ** 2 + (pz - z) ** 2
            if d <= 400:
                near.append((d, py))
    if not near:
        return fallback
    near.sort()
    ys = sorted(y for _, y in near[:7])
    return ys[len(ys) // 2]


def _zones(mt):
    """{Pokémon spawn point name: its Wild Zone id} for the points of a zone."""
    t = mt.tables.get('spawner')
    out = {}
    for r in rows(t) if t else []:
        for o in r.get('appearanceSpawnerObjectInfoList') or []:
            zid = (o.get('zoneInfo') or {}).get('zoneId')
            if zid:
                out[o.get('objectName')] = zid
    return out


def _zone_at(mt, zones, x, z):
    """The Wild Zone (x, z) is in, or None: inside means within the zone's spread of spawn points around their
    middle (a zone's points fill it)."""
    L = layer_for(mt, 'pokemon')
    by_zone = {}
    for _, _, p in L.points() if L else []:
        zid = zones.get(p.get('Name'))
        if zid:
            px, _, pz = _pos(p)
            by_zone.setdefault(zid, []).append((px, pz))
    best = None
    for zid, ps in by_zone.items():
        cx = sum(p[0] for p in ps) / len(ps)
        cz = sum(p[1] for p in ps) / len(ps)
        reach = max(((p[0] - cx) ** 2 + (p[1] - cz) ** 2) ** 0.5 for p in ps) + 5.0
        d = ((x - cx) ** 2 + (z - cz) ** 2) ** 0.5
        if d <= reach and (best is None or d < best[0]):
            best = (d, zid)
    return best[1] if best else None


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
    zones = _zones(mt) if kind == 'pokemon' else {}
    here = _zone_at(mt, zones, x, z) if kind == 'pokemon' else None
    for _, L, gi, si, p in candidates(mt, kind, x, z)[:80]:
        old = p.get('Name', '')
        if not re.search(r'\d\d$', old):
            continue
        if kind == 'npc' and 'npc_talk' not in old:
            continue                                    # people are copied from townsfolk, not story scenes
        if kind == 'pokemon':                           # a Wild Zone spawner only inside its zone, and only
            if zones.get(old) != here:                  # spawners outside zones outside them
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
    # every scene file that places the original (NPCs are often in a day and a night scene)
    px, py, pz = _pos(p)
    places = []
    for r_ in mt.find_scenes(old, KINDS[kind][3]) or [rel]:
        chunks = mt.scene(r_)[0]['Chunks']
        ci = next((i for i, c in enumerate(chunks) if old.encode() in bytes(c.get('Data', []))), None)
        off = mt._pos_offset(chunks[ci]['Data'], px, py, pz) if ci is not None else None
        if off is not None:
            places.append((r_, chunks, ci, off))
    if not places:
        raise ValueError("Could not find where %s's position is stored in its scene file." % old)
    y = ground_height(mt, x, z, py)
    # 1. position
    q = copy.deepcopy(p)
    q['Name'] = new
    q['Position'] = {'X': float(x), 'Y': float(y), 'Z': float(z)}
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
    # 4. scene(s): the template's chunk tree, renamed, with fresh ids for chunks that belong only to it, moved.
    # An id gets the same new id in every scene file, as the original's day and night copies share theirs
    rng = random.Random(hash(new) & 0xFFFFFFFF)
    new_id = {}
    spots = []
    for r_, chunks, ci, off in places:
        ids = []

        def all_ids(cs):
            for c in cs:
                ids.append(c.get('Id'))
                all_ids(c.get('Children') or [])
        all_ids(chunks)
        unique = {k for k in ids if ids.count(k) == 1}
        used = set(ids) | set(new_id.values())

        def rename(c):
            b = bytes(c.get('Data', []))
            if old.encode() in b:
                c['Data'] = list(b.replace(old.encode(), new.encode()))
            if c.get('Id') in unique:
                if c['Id'] not in new_id:
                    nid = rng.getrandbits(64)
                    while nid in used:
                        nid = rng.getrandbits(64)
                    new_id[c['Id']] = nid
                used.add(new_id[c['Id']])
                c['Id'] = new_id[c['Id']]
            for ch in c.get('Children') or []:
                rename(ch)
        nc = copy.deepcopy(chunks[ci])
        rename(nc)
        b = bytearray(nc['Data'])
        struct.pack_into('<3f', b, off, x, y, z)
        nc['Data'] = list(b)
        chunks.insert(ci + 1, nc)
        mt.__dict__.setdefault('scene_dirty', set()).add(r_)
        spots.append((r_, off))
    mt.__dict__.setdefault('new_points', {})[new] = spots
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
        enc['talentScale'] = 255                      # alphas are always the biggest size
        enc['oyabunProbability'] = 100.0
        enc.setdefault('oyabunAdditionalLevel', 10)
    if spec.get('form'):
        _wild_behaviour(mt, spec['species'], spec['form'])
    groups = mt.tables['enc'].data['values']
    eg = next((g for g in groups if e0 in (g.get('root') or [])), groups[0])
    eg.setdefault('root', []).append(enc)
    mt.tables['enc'].dirty = True
    info = copy.deepcopy(info)
    info['encountDataId'] = enc['id']
    info['weight'] = info.get('weight') or 100
    info['activationCondition'] = copy.deepcopy(FORCE)
    info.pop('appearedWeatherCondition', None)
    # day / night as the game marks it: 1 = by day, 2 = at night (night ones also have a night-only spawner)
    info.pop('appearedTimeCondition', None)
    if spec.get('when') == 'day':
        info['appearedTimeCondition'] = 1
    elif spec.get('when') == 'night':
        info['appearedTimeCondition'] = 2
    s['encountDataInfoList'] = [info]
    # the copied spawner may only work in some weather, time or story point: the new one always does (or only at
    # night, if asked)
    s['activationCondition'] = _when(spec) if spec.get('when') == 'night' else copy.deepcopy(FORCE)
    if 'activationConditionForUi' in s:
        s['activationConditionForUi'] = copy.deepcopy(FORCE)


def _item_table(mt, key, tid, items):
    t = mt.tables[key]
    groups = t.data['values']
    groups[0].setdefault('root', []).append({
        'id': tid, 'itemLotteryDataList': [{'itemId': str(i), 'weight': w, 'maxCount': n,
                                            'activationCondition': copy.deepcopy(FORCE)} for i, n, w in items]})
    t.dirty = True


WILD_DIR = 'param_chr/data/character/pokemon/wild_single'


def has_wild_behaviour(root, species, form):
    """False for forms that never appear in the wild (Mega forms...): their wild behaviour file is empty, so a
    wild one would just stand still."""
    p = os.path.join(root, *WILD_DIR.split('/'), '%04d_%02d_00.bin' % (species, form))
    try:
        with open(p, 'rb') as f:
            return b'State_' in f.read()
    except OSError:
        return False


def _wild_behaviour(mt, species, form):
    """A form without wild behaviour gets its normal form's (saved into the mod with the new point)."""
    root = os.path.normpath(mt.app.root_var.get())
    if has_wild_behaviour(root, species, form) or not has_wild_behaviour(root, species, 0):
        return
    rel = '%s/%04d_%02d_00.bin' % (WILD_DIR, species, form)
    if mt.romfs and os.path.isfile(os.path.join(mt.romfs, *rel.split('/'))):
        return                                          # this mod already has one
    with open(os.path.join(root, *WILD_DIR.split('/'), '%04d_00_00.bin' % species), 'rb') as f:
        mt.__dict__.setdefault('extra_files', {})[rel] = f.read()


TIME_CONDITION = {'day': 'time_noon_condition', 'night': 'time_night_condition'}


def _when(spec):
    """The spawner condition for 'always' / 'day' / 'night' (the game's own day and night conditions)."""
    c = TIME_CONDITION.get(spec.get('when'))
    return [{'element': [{'param': [{'condition': c}]}]}] if c else copy.deepcopy(FORCE)


def _apply_itemball(mt, s, new, spec):
    tid = 'lt_' + new
    _item_table(mt, 'ib_items', tid, [(spec['item'], spec['count'], 100)])
    s['tableInfoList'] = [{'tableId': tid, 'activationCondition': copy.deepcopy(FORCE)}]
    if spec.get('when') in TIME_CONDITION:
        s['activationCondition'] = _when(spec)
    else:
        s.pop('activationCondition', None)


def _apply_random(mt, s, new, spec):
    tid = 'lt_' + new
    _item_table(mt, 'rp_items', tid, [(i, 1, w) for i, w in spec['items']])
    s['tableInfoList'] = [{'tableId': tid, 'activationCondition': copy.deepcopy(FORCE)}]
    s['activationCondition'] = _when(spec)
    for o in s.get('appearanceSpawnerObjectInfoList') or []:
        o['appearanceInfo'] = {'minCount': spec['min'], 'maxCount': max(spec['min'], spec['max'])}


def _apply_gimmick(mt, s, new, spec):
    s['activationCondition'] = copy.deepcopy(FORCE)
    # the placement flags this kind of object has most often in the game (a wall crystal's flags sink a
    # ground crystal into the ground)
    flags = _usual_flags(mt, spec['gimmick'])
    if flags is not None:
        for o in s.get('appearanceSpawnerObjectInfoList') or []:
            o['additionalFlagList'] = list(flags)
    s['spawnWazagimmickInfoList'] = [{'privateId': spec['gimmick'], 'weight': 100, 'maxCount': 1,
                                      'activationCondition': copy.deepcopy(FORCE)}]


def _apply_npc(mt, s, new, spec):
    s['assetId'] = spec['asset']
    s.pop('encountId', None)                         # a person, not one of the story's Pokémon NPCs
    for cond in s.get('appearingConditionList') or []:
        cond['element'] = copy.deepcopy(FORCE)[0]['element']
        if spec['when'] in TIME_CONDITION:                # as the game's day / night NPCs: condition and flag
            cond['element'].append({'param': [{'condition': TIME_CONDITION[spec['when']]}]})
        cond['dayNightFlag'] = {} if spec['when'] == 'always' else {'dayNightFlag': 1 if spec['when'] == 'day' else 2}
        for o in cond.get('spawnerObjectInfoList') or []:
            tags = spec.get('talk')
            if tags is not None and tags != 'keep':
                o['tagInfoList'] = [{'tag': t} for t in tags]


APPLY = {'pokemon': _apply_pokemon, 'itemball': _apply_itemball, 'random': _apply_random,
         'gimmick': _apply_gimmick, 'npc': _apply_npc}


# ---- choices for the menu ------------------------------------------------------------------------------------

# move gimmicks by their group in the game's gimmick table (each area has its own copy of the same object)
_CRYSTAL = {'S': 'small, on the ground', 'M': 'big, on the ground', 'WS': 'small, on a wall', 'WM': 'big, on a wall',
            'WOS': 'small, on an outside corner', 'WOM': 'big, on an outside corner',
            'WIS': 'small, on an inside corner', 'WIM': 'big, on an inside corner'}
_HYPER_CRYSTAL = {'DA': 'small, on the ground', 'DB': 'big, on the ground', 'DC': 'small, on a wall',
                  'DD': 'big, on a wall'}
_WALLS = {'ROCK': 'Breakable rock', 'TSUTA': 'Thorny vines', 'HEDORO': 'Sludge pile'}


def gimmick_label(group):
    """'Mega Shard crystals (small, on the ground)' for a gimmick group id, or None for ones only a side quest uses."""
    g = re.sub(r'^[A-Z]+\d*_', '', group or '')               # A01_ / ZD01_ / BOSS_
    m = re.match(r'^ALL_WAZA_OBJ301_([A-Z]+)_SPAWN$', g)
    if m and m.group(1) in _CRYSTAL:
        return 'Mega Shard crystals (%s)' % _CRYSTAL[m.group(1)]
    m = re.match(r'^MEGA_CRYSTAL_([A-Z]+)_SPAWN$', g)
    if m and m.group(1) in _HYPER_CRYSTAL:
        return 'Mega Shard crystals, Hyperspace (%s)' % _HYPER_CRYSTAL[m.group(1)]
    m = re.match(r'^([A-Z]+)_WALL_([SD])_SPAWN$', g)
    if m and m.group(1) in _WALLS:
        return _WALLS[m.group(1)] + (', Hyperspace' if m.group(2) == 'D' else '')
    m = re.match(r'^DIM_BOX_([SLG])_SPAWN$', g)
    if m:
        return 'Hyperspace box (%s)' % {'S': 'small', 'L': 'large', 'G': 'giant'}[m.group(1)] + \
            (', boss reward' if group.startswith('BOSS_') else '')
    return None


def _gimmick_groups(mt):
    """{gimmick id: its group in the game's gimmick table} (read once)."""
    if '_gimmick_groups' not in mt.__dict__:
        group = {}
        try:
            root = os.path.normpath(mt.app.root_var.get())
            for r in _flat(_table_rows(root, 'world/ik_data/field/field_wazagimmick_private/'
                                             'field_wazagimmick_private/field_wazagimmick_private_array.bin')):
                for o in r.get('objectName') or []:
                    group.setdefault(o, r.get('groupId'))
        except Exception:  # noqa: BLE001
            pass
        mt.__dict__['_gimmick_groups'] = group
    return mt.__dict__['_gimmick_groups']


def _usual_flags(mt, pid):
    """The placement flags the game's spawners of the same kind of object (as `pid`) have most often, or None."""
    group = _gimmick_groups(mt)
    label = gimmick_label(group.get(pid))
    t = mt.tables.get('wg_spawner')
    count = {}
    for r in rows(t) if t and label else []:
        if any(gimmick_label(group.get(g.get('privateId'))) == label for g in r.get('spawnWazagimmickInfoList') or []):
            for o in r.get('appearanceSpawnerObjectInfoList') or []:
                k = tuple(o.get('additionalFlagList') or [])
                count[k] = count.get(k, 0) + 1
    return max(count, key=count.get) if count else None


def gimmick_choices(mt):
    """[(name shown in the menu, the game's gimmick id)], each kind of object once."""
    t = mt.tables.get('wg_spawner')
    used = {}
    for r in rows(t) if t else []:
        for g in r.get('spawnWazagimmickInfoList') or []:
            if g.get('privateId'):
                used[g['privateId']] = used.get(g['privateId'], 0) + 1
    group = _gimmick_groups(mt)
    out = {}
    for pid in sorted(used, key=lambda p: -used[p]):           # the most used copy of each object
        label = gimmick_label(group.get(pid))
        if label and label not in out:
            out[label] = pid
    return sorted(out.items())


def _flat(rs):
    out = []
    for g in rs:
        out += g.get('root', []) if isinstance(g, dict) and 'root' in g else [g]
    return out


def _table_rows(root, rel):
    path = os.path.join(root, *rel.split('/'))
    d = json.loads(zm.to_json(path, zm.schema_for(path)))
    return d[next(k for k in d if isinstance(d[k], list))]


_LOOKS = {}
_NPC_TPL = re.compile(r'^tr(\d{4})_\d\d_(?:(?:fa|bo)(\d\d)_?)?(?:ra(\d\d)|(holo(?:_red)?))?_?(.*)$')
_NAMED_TPL = re.compile(r'^(tr\d{4}_\d\d)_(holo_)?(.+)$')


def npc_looks(root):
    """[(name shown in the menu, the game's NPC asset id)] for every person an NPC spawn can look like: the story
    characters and every townsperson / trainer look, each once. Looks the game only shows at some point in the
    story, and variants that look the same (they differ only in what the scripts make them do), are left out."""
    if root in _LOOKS:
        return _LOOKS[root]
    import za_playas as pa
    try:
        tpl = _table_rows(root, 'world/ik_data/field/npc/npc_object_template_data/npc_object_template_data/'
                                'npc_object_template_data.bin')
        assets = _table_rows(root, 'world/ik_data/field/npc/npc_asset_data/npc_asset_data/npc_asset_data.bin')
    except Exception:  # noqa: BLE001
        return []
    force = json.dumps(FORCE)
    by_id = {t.get('id', 0): t for t in tpl}
    asset_of = {}                     # template file -> an asset that always shows just that look (a townsperson's
    for a in assets:                  # own asset before a battle trainer's)
        infos = a.get('npcInfoList') or []
        if len(infos) == 1 and json.dumps(infos[0].get('activationCondition')) == force and a.get('assetId'):
            path = by_id.get(infos[0].get('objectTemplateId'), {}).get('path')
            if path:
                asset_of.setdefault(path, []).append(a['assetId'])
    asset_of = {k: min(v, key=lambda x: (not x.startswith('npc_'), x)) for k, v in asset_of.items()}
    known = pa.trainer_names(root)
    code_of = {}                                     # trainer class number -> outfit code (for the hologram ones)
    for t in tpl:
        m = _NPC_TPL.match(os.path.basename(t.get('path', ''))[:-6])
        if m and m.group(5) and not m.group(4):
            code_of.setdefault(m.group(1), re.sub(r'_(k\d\d|battle)$', '', m.group(5)))
    groups, done = {}, set()
    for t in tpl:
        aid = asset_of.get(t.get('path'))
        name = os.path.basename(t.get('path', ''))[:-6]
        if not aid or t.get('type') not in ('cc_vr', 'cc_ir', 'uq') or re.search(r'_k\d\d$', name) or \
                t.get('path') in done:
            continue
        done.add(t.get('path'))
        m = _NPC_TPL.match(name)
        if t.get('type') in ('cc_vr', 'cc_ir') and m:
            raw = m.group(5) or code_of.get(m.group(1), '')
            battle = raw.endswith('_battle')
            code = raw[:-7] if battle else raw
            label = NPC_NAMES.get(code) or pa.NPC_NAMES.get(code)
            if not label:
                g = {'m': 'male', 'f': 'female'}.get(code.rsplit('_', 1)[-1]) if '_' in code else None
                key = code.rsplit('_', 1)[0] if g else code
                crew = re.match(r'^([a-z]+)_member$', key)
                label = ((NPC_GROUPS[crew.group(1)] + ' member') if crew and crew.group(1) in NPC_GROUPS else
                         pa.NPC_CLASSES.get(key) or key.replace('_', ' ').capitalize() or 'Townsperson') + \
                    (' (%s)' % g.capitalize() if g else '')
            extra = ', battle outfit' if battle else ''
            if m.group(4):
                extra += ', red hologram' if m.group(4).endswith('red') else ', hologram'
            order = (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 99))
        else:
            n = _NAMED_TPL.match(name)
            if not n:
                continue
            folder = n.group(1) + '_' + n.group(3)
            label = pa.NAMES.get(folder) or known.get(folder) or pa.NICE.get(n.group(3).split('_', 0)[0]) \
                or pa.NICE.get(n.group(3))
            if not label:
                b = re.match(r'^([a-z]+)_(boss|leader)(?:_([mf]))?$', n.group(3))
                label = (('%s %s' % (NPC_GROUPS.get(b.group(1), 'Group'), b.group(2).capitalize())) if b
                         else n.group(3).replace('_', ' ').capitalize())
            extra = ', hologram' if n.group(2) else ''
            order = (int(name[2:6]), 0, 0)
        groups.setdefault((label, extra), []).append((order, aid))
    out = []
    for (label, extra), items in groups.items():
        items.sort()
        for i, (_, aid) in enumerate(items):
            look = (' - look %d' % (i + 1)) if len(items) > 1 else ''
            out.append(('%s%s%s' % (label, look, (' (%s)' % extra[2:]) if extra else ''), aid))
    out.sort(key=lambda t: t[0].lower())
    _LOOKS[root] = out
    return out


# the groups the story's crews belong to, by their outfit code (named after who leads them in the game's text)
NPC_GROUPS = {'ga': "Canari's crew", 'ju': 'Fists of Justice', 'pa': "Jacinthe's crew", 'co': 'Rust Syndicate'}
# outfit codes with a name of their own
NPC_NAMES = {'co_member_m': 'Rust Syndicate Grunt (Male)', 'co_member_f': 'Rust Syndicate Grunt (Female)',
             'nu_member_f': 'Team Flare Cafe Worker (Female)'}

# what an NPC does: the game's behaviour tags, in the combinations its own NPCs use
NPC_BEHAVIOURS = [
    ('Like the NPC it was copied from', 'keep'),
    ('Stands, turns to face you when you talk to them', ['StandTalkNpcLookAtOnInteractTurn']),
    ('Stands, watches you as you pass, turns to face you when you talk',
     ['StandTalkNpcLookAt', 'StandTalkNpcLookAtOnInteractTurn']),
    ('Seated, watches you as you pass, turns to face you when you talk',
     ['SitTalkNpcLookAt', 'StandTalkNpcLookAtOnInteractTurn']),
    ('Stands, only talks when you face them from the front',
     ['StandTalkNpcLookAtOnInteractTurn', 'CheckInteractNpcDir']),
    ("Stands, doesn't react when you talk to them", ['StandTalkNpcLookAtOnInteractTurn', '会話_無反応']),
    ('Stands, never looks at you', ['StandTalkNpcLookAtOnInteractTurn', 'DisableLookAt']),
    ("Background person: never looks at you and can't be talked to or targeted", ['DisableLookAt', 'ProhibitFocus']),
]


def npc_assets(root):
    return [a for _, a in npc_looks(root)]


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
            'What should be here? It copies the nearest point of that kind (its area and scene file) and takes the '
            'height of the points around it - check the height (Y) afterwards.')).pack(anchor='w', pady=(2, 10))
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

    def when_row(g, row, column=0, span=3):
        """'Appears: always / by day / at night' - returns its variable."""
        var = tk.StringVar(value='always')
        ttk.Label(g, text='Appears:').grid(row=row, column=column, sticky='w', pady=(6, 0))
        wf = ttk.Frame(g)
        wf.grid(row=row, column=column + 1, columnspan=span, sticky='w', pady=(6, 0))
        for v, t in (('always', 'always'), ('day', 'by day'), ('night', 'at night')):
            ttk.Radiobutton(wf, text=t, value=v, variable=var).pack(side='left', padx=(0, 8))
        return var

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
            ttk.Label(g, text='0 = the normal form. A form never seen in the wild (a Mega...) gets\nthe normal '
                              "form's wild behaviour.", style='Dim.TLabel', justify='left').grid(
                row=1, column=2, columnspan=2, sticky='w')
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
            rnd_cb = ttk.Checkbutton(sf, text='Random (like wild Pokémon)', variable=rnd)
            rnd_cb.pack(anchor='w')
            sl = ttk.Frame(sf)
            sl.pack(anchor='w')
            ttk.Label(sl, text='tiny').pack(side='left')
            size_sl = ttk.Scale(sl, from_=0, to=255, variable=size, length=200)
            size_sl.pack(side='left', padx=4)
            ttk.Label(sl, text='huge').pack(side='left')
            alpha = tk.BooleanVar(value=False)
            ttk.Checkbutton(g, text='Always an alpha', variable=alpha).grid(row=4, column=1, columnspan=3, sticky='w',
                                                                          pady=(8, 0))
            alpha_note = ttk.Label(g, text='Alphas are always the biggest size (255).', style='Dim.TLabel')

            def alpha_changed(*_):
                # an alpha is always size 255, so the size choice is locked while it's ticked
                on = alpha.get()
                for w in (rnd_cb, size_sl):
                    w.state(['disabled'] if on else ['!disabled'])
                if on:
                    alpha_note.grid(row=5, column=1, columnspan=3, sticky='w')
                else:
                    alpha_note.grid_remove()
            alpha.trace_add('write', alpha_changed)
            when = when_row(g, 6)

            def spec():
                m = re.search(r'#(\d+)\)?\s*$', sp.get())
                if not m:
                    raise ValueError('Pick a Pokémon from the list.')
                big = alpha.get()
                return {'species': int(m.group(1)), 'form': int(form.get()), 'min': int(lo.get()),
                        'max': int(hi.get()), 'size': 255 if big else (-1 if rnd.get() else int(size.get())),
                        'alpha': big, 'when': when.get()}
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
            when = when_row(g, 2, span=1)

            def spec():
                m = re.search(r'#(\d+)\)\s*$', it.get())
                if not m:
                    raise ValueError('Pick an item from the list.')
                return {'item': int(m.group(1)), 'count': int(cnt.get()), 'when': when.get()}
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
            wrow = ttk.Frame(g)
            wrow.grid(row=5, column=0, columnspan=3, sticky='w')
            when = when_row(wrow, 0, span=1)

            def spec():
                chosen = [(int(re.search(r'#(\d+)\)\s*$', lst.item(i, 'text')).group(1)), int(lst.item(i, 'values')[0]))
                          for i in lst.get_children()]
                if not chosen:
                    raise ValueError('Add at least one item.')
                return {'items': chosen, 'min': int(lo.get()), 'max': int(hi.get()), 'when': when.get()}
            nav(kind, spec)
        elif kind == 'gimmick':
            types = dict(gimmick_choices(mt))
            names = list(types)
            ttk.Label(g, text='Object:').grid(row=0, column=0, sticky='w')
            gb = ttk.Combobox(g, values=names, width=52, state='readonly')
            gb.grid(row=0, column=1, sticky='w', pady=2)
            ttk.Label(g, style='Dim.TLabel', wraplength=460, justify='left', text=(
                'A move breaks the crystals and boxes for items (the big ones give more: 6-10 instead of 1-3). '
                'Rocks, vines and sludge give nothing; a move clears them away.')).grid(row=1, column=0, columnspan=2, sticky='w',
                                                                           pady=4)

            def spec():
                if gb.get() not in types:
                    raise ValueError('Pick an object from the list.')
                return {'gimmick': types[gb.get()]}
            nav(kind, spec)
        elif kind == 'npc':
            looks = dict(npc_looks(os.path.normpath(app.root_var.get())))
            names = list(looks)
            ttk.Label(g, text='Who:').grid(row=0, column=0, sticky='w')
            ab = ttk.Combobox(g, values=names, width=52)
            ab.grid(row=0, column=1, sticky='w', pady=2)
            filtered(ab, names)
            when = when_row(g, 1, span=1)
            acts = dict(NPC_BEHAVIOURS)
            ttk.Label(g, text='Behaviour:').grid(row=2, column=0, sticky='w')
            tb = ttk.Combobox(g, values=list(acts), width=52, state='readonly')
            tb.set(NPC_BEHAVIOURS[1][0])
            tb.grid(row=2, column=1, sticky='w', pady=2)
            ttk.Label(g, style='Dim.TLabel', wraplength=460, justify='left', text=(
                'What they say comes from the game\'s scripts, so a new NPC may have nothing to say. Custom lines and '
                'item gifts need the game\'s event scripts and can\'t be set here yet.')).grid(
                row=3, column=0, columnspan=2, sticky='w', pady=4)

            def spec():
                if ab.get() not in looks:
                    raise ValueError('Pick who it is from the list.')
                return {'asset': looks[ab.get()], 'when': when.get(), 'talk': acts[tb.get()]}
            nav(kind, spec)
    step1()
    w.bind('<Escape>', lambda e: w.destroy())
    return w
