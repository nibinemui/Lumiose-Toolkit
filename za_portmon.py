"""Port a Pokémon: bring a Pokémon from a Scarlet/Violet dump into Z-A, in a species slot Z-A has data for but
does not use, without replacing anything in the game.

What it writes into the current mod (proven in game, 2026-11):
  * the species' personal row: "in this game" flag on, moves limited to ones Z-A can use, evolutions only to
    species that are in the game, optional Pokédex placement;
  * the model: Scarlet/Violet mesh, skeleton, textures and locators as they are; materials rebuilt with Z-A shaders
    (Scarlet/Violet shaders such as SSS or EyeClearCoat crash Z-A);
  * a Z-A "host" Pokémon's animation system, config, alpha config and collision files (Scarlet/Violet's animation
    system crashes Z-A), and every animation slot the host's list names, filled with the Pokémon's own animations
    or the closest ones. The host should move like the Pokémon (skeleton similarity picks it);
  * resource table entry, model size and movement rows, alpha move, wild behaviour, cry, icons;
  * optional spawn points in a wild zone.
"""
import copy
import json
import os
import random
import re
import struct

import za_modding as zm

ZA_SCHEMAS = os.path.join(zm.APP_DIR, 'tools', 'schemas')
TRMTR = os.path.join(ZA_SCHEMAS, 'trinity', 'trmtr.fbs')
TRSKL = os.path.join(ZA_SCHEMAS, 'trinity', 'trskl.fbs')
PERSONAL = 'avalon/data/personal_array.bin'
CATALOG = 'ik_pokemon/catalog/catalog/poke_resource_table.trpmcatalog'
MODEL_PARAM = 'param_chr/data/pokemon/poke_model_param/poke_model_param_array.bin'
MOVE_PARAM = 'param_chr/data/character/pokemon/poke_movement_param/poke_movement_param_array.bin'
OYABUN = 'world/ik_data/field/oyabun/oyabun_waza/oyabun_waza.bin'
WAZA = 'avalon/data/waza_array.bin'
DEX = 'world/exl/pokedex_contents_data/pokedex_contents_data/pokedex_contents_data.bin'
WILD_DIR = 'param_chr/data/character/pokemon/wild_single'
WILD_CAT = 'param_chr/catalog/bin/sources/flatbuffers/character/pokemon/wild_single.data.bin'
ENCOUNT = 'world/ik_data/field/pokemon/encount_data/encount_data/encount_data_array.bin'
SPAWNER = 'world/ik_data/field/pokemon_spawner/pokemon_spawner_data/pokemon_spawner_data_array.bin'
XFORM = ('world/ik_data/field/spawner_transform_data/pokemon_spawner_transform/pokemon_spawner_transform/'
         'pokemon_spawner_transform_array.bin')
REGULAR_DEX = 232            # Regular Pokédex size in the game (the Hyperspace Pokédex follows it)

# files of the host's animation system / config / collision (renamed to the new Pokémon)
SUPPORT = ('_base.tracl', '_base.tracn', '_base.tracp', '_base.tracr', '_base.tracs', '_base.tralk', '_base.trmdt',
           '_base.trslp', '_base.trssp', '_base_motion_detector.trmdd', '.trpokecfg', '_oybn.trpokecfg', '_defence.hkx')
# files that must name only bones the new Pokémon has (layers and collision are handled separately)
BONE_CHECKED = ('_base.tralk', '_base.trslp', '_base.trmdt', '_base.trssp', '.trpokecfg', '_oybn.trpokecfg',
                '_base.tracs', '_base.tracp')
ANIM = re.compile(r'^pm\d{4}_\d\d_\d\d_(\d{5})_([^.]+)\.(tranm|tracm|traef|trcrv)$')
FALLBACK = {
    'turn_r000': ['defaultwait01_loop'], 'turnmove01_r090': ['turn_r090'], 'turnmove01_l090': ['turn_l090'],
    'stepin01_start': ['stepout01_start'], 'stepin01': ['stepout01', 'stepout01_loop'], 'stepin01_end': ['stepout01_end'],
    'stepout01_loop': ['stepout01'], 'stepout01': ['stepout01_loop'],
    'eat02_start': ['eat01_start'], 'eat02_loop': ['eat01_loop'], 'eat02_end': ['eat01_end'],
    'appeal01': ['glad01', 'roar01'], 'search01_start': ['defaultidle01'], 'search01_loop': ['defaultwait01_loop'],
    'search01_end': ['defaultidle01'], 'attack03_start': ['rangeattack02_start', 'attack01'],
    'attack03_loop': ['rangeattack02_loop'], 'attack03_end': ['rangeattack02_end', 'attack01'],
    'charge01_loop': ['rangeattack02_loop', 'defaultwait01_loop'], 'battlewait01_loop': ['defaultwait01_loop'],
    'restidle01': ['rest01_loop'], 'sleepidle01': ['sleep01_loop'], 'battleidle02': ['defaultidle02', 'defaultidle01'],
    'backwalk01_loop': ['walk01_loop'], 'damege02': ['damage02'], 'inquiringly01': ['notice01'],
    'fieldaction01_start': ['roar01'], 'fieldaction01_loop': ['defaultwait01_loop'], 'fieldaction01_end': ['roar01'],
    'categoryin01': ['defaultidle01'], 'categoryout01': ['defaultidle01'], 'megaappeal01': ['roar01'],
}
# Scarlet/Violet shader -> the Z-A shader to rebuild it with
SHADER_MAP = {'SSS': 'IkCharacter', 'Standard': 'IkCharacter', 'EyeClearCoat': 'IkCharacter', 'Toon': 'IkCharacter',
              'Eye': 'Eye', 'Unlit': 'Unlit', 'FresnelEffect': 'FresnelEffect', 'NonDirectional': 'NonDirectional',
              'Iridescence': 'Iridescence', 'Transparent': 'IkCharacterTransparent',
              'TransparentSSS': 'IkCharacterTransparent', 'IkCharacter': 'IkCharacter',
              'IkCharacterTransparent': 'IkCharacterTransparent'}
TEX_ALIAS = {'OcclusionMap': ('OcclusionMap', 'AOMap'), 'NormalMap': ('NormalMap', 'NormalMap1'),
             'BaseColorMap': ('BaseColorMap', 'BaseColorMap1'), 'LayerMaskMap': ('LayerMaskMap',),
             'DisplacementMap': ('DisplacementMap',), 'EmissionMaskMap': ('EmissionMaskMap', 'EmissionMap'),
             'HighlightMaskMap': ('HighlightMaskMap',), 'ParallaxMap': ('ParallaxMap',)}


class PortError(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------------------

def _game(root, rel):
    return os.path.join(root, *rel.split('/'))


def load(root, mod, rel, schema=None):
    """(object, schema path) of a table: the mod's copy if it has one, else the game's."""
    game = _game(root, rel)
    sch = schema or zm.schema_for(game)
    if not sch:
        raise PortError('No schema for ' + rel)
    src = mod.path_for(rel) if mod is not None and os.path.isfile(mod.path_for(rel)) else game
    return json.loads(zm.to_json(src, sch)), sch


def save(mod, rel, obj, sch):
    out = mod.path_for(rel)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    zm.from_json(json.dumps(obj), sch, out)


def _rows(d):
    return next(v for v in d.values() if isinstance(v, list))


def find_sv(path):
    """The 'pokemon' folder of a Scarlet/Violet dump (it has data/ and catalog/), from the dump, its romfs or itself."""
    for cand in (path, os.path.join(path, 'pokemon'), os.path.join(path, 'romfs', 'pokemon')):
        if os.path.isdir(os.path.join(cand, 'data')) and os.path.isfile(
                os.path.join(cand, 'catalog', 'catalog', 'poke_resource_table.trpmcatalog')):
            return cand
    return None


def bones(trskl):
    d = json.loads(zm.to_json(trskl, TRSKL))
    return {n.get('Name') for n in d.get('TransformNodes') or [] if n.get('Name')}


def names(root):
    import za_dataedit
    return za_dataedit.Names(root)


_PRESENT_CACHE = {}


def personal_rows(root, mod=None):
    d, _ = load(root, mod, PERSONAL)
    return d['entries']


def sv_catalog(sv):
    p = os.path.join(sv, 'catalog', 'catalog', 'poke_resource_table.trpmcatalog')
    return json.loads(zm.to_json(p, zm.schema_for(p)))['table']


# ---------------------------------------------------------------------------------------------------------
# what can be ported
# ---------------------------------------------------------------------------------------------------------

def candidates(root, sv, mod=None):
    """Scarlet/Violet Pokémon with a model and a free Z-A slot: [{species, name, folder, reason, free}]."""
    rows = {(r['species']['species_index'], r['species']['species_form']): r for r in personal_rows(root, mod)}
    nm = names(root).species
    za_models = set(os.listdir(os.path.join(root, 'ik_pokemon', 'data')))
    out = []
    seen = set()
    for e in sv_catalog(sv):
        si = e.get('species_info', {})
        sp, form, gender = si.get('species', 0), si.get('form', 0), si.get('gender', 0)
        if not sp or form or gender or sp in seen or sp >= 9999:
            continue
        seen.add(sp)
        folder = e['model_path'].split('/')[1]
        if not os.path.isdir(os.path.join(sv, 'data', 'pm%04d' % sp, folder)):
            continue
        r = rows.get((sp, 0))
        if r is None:
            free, reason = False, 'past the game\'s species limit (1010)'
        elif r.get('is_present'):
            free, reason = False, 'already in Z-A'
        else:
            free, reason = True, 'free slot' + (' (model folder taken)' if 'pm%04d' % sp in za_models else '')
        out.append({'species': sp, 'name': nm.get(sp, '#%d' % sp), 'folder': folder, 'free': free, 'reason': reason,
                    'height': (r or {}).get('species', {}).get('height', 0)})
    return sorted(out, key=lambda c: c['species'])


# ---------------------------------------------------------------------------------------------------------
# host choice
# ---------------------------------------------------------------------------------------------------------

def _limb_bones(bs):
    """The body bones used to compare skeletons (no face rig, meshes, LODs, effect points)."""
    skip = re.compile(r'(eyelid|eyebrow|jaw|mouth|tongue|eye|_mesh|^lod|^pm\d|light|^origin$|camera|^effect|_eff$)')
    return {b for b in bs if not skip.search(b)}


GROUPS = (('legs', re.compile(r'(leg|foot|knee|thigh|toe)')), ('arms', re.compile(r'(arm|hand|shoulder|elbow|wing)')),
          ('body', re.compile(r'(spine|hips|waist|chest|belly)')), ('head', re.compile(r'(head|neck)')),
          ('tail', re.compile(r'tail')))


def gait(folder_dir, stem):
    """How the Pokémon walks: average swing (degrees) of its legs, arms, body, head and tail over the walk cycle,
    and how much the waist bobs relative to its height. None if it has no walk animation."""
    import numpy as np
    import za_trinity as zt
    path = None
    for slot in ('00030_walk01_loop', '00100_run01_loop', '00000_defaultwait01_loop'):
        p = os.path.join(folder_dir, '%s_%s.tranm' % (stem, slot))
        if os.path.isfile(p):
            path = p
            break
    if not path:
        return None
    try:
        a = zt.Animation(path)
    except Exception:  # noqa: BLE001
        return None
    out = {g: [] for g, _ in GROUPS}
    bob = 0.0
    for bone, ch in a.tracks.items():
        r = ch.get('r')
        if r is not None and r[0] is not None and len(r[1]) > 1:
            q = np.asarray(r[1], dtype=np.float64)
            q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-9)
            mean = q.mean(0)
            mean /= max(np.linalg.norm(mean), 1e-9)
            ang = float(np.degrees(2 * np.arccos(np.clip(np.abs(q @ mean), 0, 1))).max())
            for g, rx in GROUPS:
                if rx.search(bone):
                    out[g].append(ang)
                    break
        if bone == 'waist':
            t = ch.get('t')
            if t is not None and t[0] is not None and len(t[1]) > 1:
                y = np.asarray(t[1])[:, 1]
                bob = float((y.max() - y.min()) / max(abs(y.mean()), 1e-3))
    prof = {g: round(sum(v) / len(v), 2) if v else 0.0 for g, v in out.items()}
    prof['bob'] = round(bob, 3)
    prof['anim'] = os.path.basename(path)
    return prof


def gait_similarity(a, b):
    """1 = moves the same way, towards 0 = very different (by limb swing and bobbing in the walk cycle)."""
    if not a or not b:
        return 0.3
    d = 0.0
    for g, _ in GROUPS:
        d += abs(a[g] - b[g]) / (max(a[g], b[g]) + 15)
    d += 3 * abs(a['bob'] - b['bob'])
    return round(1 / (1 + d), 3)


HOSTS_CACHE = os.path.join(zm.APP_DIR, 'port_hosts.json')
# hosts confirmed in game (model folder: what for) - preferred when they fit
PROVEN = {'pm0498_00_00': 'walking four-legged Pokémon (tested with Cyndaquil)'}


def host_profiles(root, progress=None):
    """Every Z-A Pokémon that can serve as a host, with what it needs and how it moves (cached)."""
    rows = personal_rows(root)
    present = {r['species']['species_index'] for r in rows if r.get('is_present')}
    height = {r['species']['species_index']: r['species']['height'] for r in rows if r['species']['species_form'] == 0}
    cat = json.loads(zm.to_json(_game(root, CATALOG)))['table']
    folders = {}
    for e in cat:
        sp = e.get('species_info', {}).get('species', 0)
        if sp in present and not e['species_info'].get('form') and not e['species_info'].get('gender'):
            folders.setdefault(e['model_path'].split('/')[1], sp)
    sig = [len(folders), os.path.getsize(_game(root, CATALOG)), int(os.path.getmtime(_game(root, CATALOG)))]
    try:
        with open(HOSTS_CACHE, encoding='utf-8') as f:
            cache = json.load(f)
        if cache.get('sig') == sig and cache.get('root') == os.path.normcase(root) and cache.get('version') == 2:
            return cache['hosts']
    except (OSError, ValueError):
        pass
    out = []
    for k, (folder, sp) in enumerate(sorted(folders.items())):
        if progress:
            progress(k + 1, len(folders))
        d = os.path.join(root, 'ik_pokemon', 'data', folder[:6], folder)
        skl = os.path.join(d, folder + '.trskl')
        if not os.path.isfile(skl) or not os.path.isfile(os.path.join(d, folder + '_base.tracr')):
            continue
        try:
            hb = bones(skl)
        except Exception:  # noqa: BLE001
            continue
        need = set()
        for suf in BONE_CHECKED:
            p = os.path.join(d, folder + suf)
            if os.path.isfile(p):
                for s_ in re.findall(rb'[a-z][a-z0-9_]{2,}', open(p, 'rb').read()):
                    s_ = s_.decode()
                    if s_ in hb and not s_.startswith(folder) and not s_.startswith('lod'):
                        need.add(s_)
        out.append({'folder': folder, 'species': sp, 'height': height.get(sp, 0), 'need': sorted(need),
                    'limb': sorted(_limb_bones(hb)), 'gait': gait(d, folder),
                    'slots': len({m.group(1) for f in os.listdir(d) for m in [ANIM.match(f)] if m})})
    try:
        with open(HOSTS_CACHE, 'w', encoding='utf-8') as f:
            json.dump({'version': 2, 'sig': sig, 'root': os.path.normcase(root), 'hosts': out}, f)
    except OSError:
        pass
    return out


def hosts(root, guest_dir, guest_stem, guest_height=0, limit=8, progress=None):
    """Z-A Pokémon whose animation system can drive this model, best first:
    [{folder, species, name, score, similarity, gait, slots, missing}]. The best host moves like the Pokémon
    (walk cycle), has a similar skeleton and size, and its files only name bones the Pokémon has."""
    gb = bones(os.path.join(guest_dir, guest_stem + '.trskl'))
    glimb = _limb_bones(gb)
    ggait = gait(guest_dir, guest_stem)
    nm = names(root).species
    out = []
    for h in host_profiles(root, progress):
        missing = sorted(set(h['need']) - gb)
        sim = len(glimb & set(h['limb'])) / max(1, len(glimb | set(h['limb'])))
        gs = gait_similarity(ggait, h['gait'])
        hh = h['height']
        size = 1 - min(1, abs(hh - guest_height) / max(hh, guest_height, 1)) if guest_height else 0.5
        score = (0 if missing else 100) + gs * 60 + sim * 30 + size * 10
        score += 25 if h['folder'] in PROVEN and gs >= 0.3 else 0
        out.append({'folder': h['folder'], 'species': h['species'], 'name': nm.get(h['species'], '#%d' % h['species']),
                    'score': round(score, 1), 'similarity': round(sim, 2), 'gait': gs, 'slots': h['slots'],
                    'missing': missing, 'proven': PROVEN.get(h['folder'], '')})
    out.sort(key=lambda x: -x['score'])
    return out[:limit]


# ---------------------------------------------------------------------------------------------------------
# materials
# ---------------------------------------------------------------------------------------------------------

_TEMPLATES = {}


def templates(root):
    """{Z-A shader name: (template material, its folder)} - one real Z-A material per shader."""
    if root in _TEMPLATES:
        return _TEMPLATES[root]
    out = {}
    prefer = {'IkCharacter': 'pm0025_00_00', 'Unlit': 'pm0004_00_00'}
    for shader, folder in prefer.items():
        p = os.path.join(root, 'ik_pokemon', 'data', folder[:6], folder, folder + '.trmtr')
        for m in json.loads(zm.to_json(p, TRMTR))['Materials']:
            if m['Shader'][0]['Name'] == shader and ('body' in m['Name'] or shader != 'IkCharacter') and shader not in out:
                out[shader] = (m, os.path.dirname(p))
    import glob
    for p in sorted(glob.glob(os.path.join(root, 'ik_pokemon', 'data', 'pm*', 'pm*_00_00', 'pm*_00_00.trmtr'))):
        if len(out) >= 9:
            break
        try:
            for m in json.loads(zm.to_json(p, TRMTR))['Materials']:
                out.setdefault(m['Shader'][0]['Name'], (m, os.path.dirname(p)))
        except Exception:  # noqa: BLE001
            continue
    _TEMPLATES[root] = out
    return out


def _tex(m, *slots):
    for s in slots:
        for t in m.get('Textures', []):
            if t.get('Name') == s:
                return t.get('File')
    return None


def convert_material(root, src, stem, add_file):
    """A Z-A material rebuilt from a template, keeping the Scarlet/Violet material's name, textures and colours.
    add_file(name, source path) copies a template's own texture into the new model folder."""
    tpl = templates(root)
    sv_shader = src['Shader'][0]['Name']
    za_shader = SHADER_MAP.get(sv_shader, 'IkCharacter')
    if za_shader not in tpl:
        za_shader = 'IkCharacter'
    t, tdir = tpl[za_shader]
    m = copy.deepcopy(t)
    for tx in m['Textures']:
        slot = tx['Name']
        own = _tex(src, *TEX_ALIAS.get(slot, (slot,)))
        if own:
            tx['File'] = own
        elif 'Mask' in slot and slot != 'LayerMaskMap':
            tx['File'] = stem + ('_white_msk.bntx' if slot in ('ShadowingColorMap', 'RimLightMaskMap') else '_black_msk.bntx')
        elif slot == 'OcclusionMap':
            tx['File'] = stem + '_white_msk.bntx'
        elif slot == 'LayerMaskMap':
            tx['File'] = stem + '_black_msk.bntx'
        elif tx['File'].startswith('../'):
            pass                                            # shared game texture (reflections): keep
        else:
            name = stem + '_tpl_' + re.sub(r'^pm\d{4}_\d\d_\d\d_', '', tx['File'])
            add_file(name, os.path.join(tdir, tx['File']))
            tx['File'] = name
    vec = {v['Name']: v for v in m.get('Vec4fParams', [])}
    neutral = {'X': 1.0, 'Y': 1.0, 'Z': 1.0, 'W': 1.0}
    own = {v['Name']: v.get('Value') for v in src.get('Vec4fParams', [])}
    for k in range(1, 5):
        if 'BaseColorLayer%d' % k in vec:
            vec['BaseColorLayer%d' % k]['Value'] = own.get('BaseColorLayer%d' % k) or neutral
        if 'ShadowingColorLayer%d' % k in vec and za_shader.startswith('IkCharacter'):
            vec['ShadowingColorLayer%d' % k]['Value'] = {'X': 0.35, 'Y': 0.25, 'Z': 0.3, 'W': 0.0}
    for k in ('BaseColor', 'UVScaleOffset', 'UVScaleOffset3'):
        if k in vec:
            vec[k]['Value'] = own.get(k) or ({'X': 1.0, 'Y': 1.0, 'Z': 0.0, 'W': 0.0} if 'UV' in k else neutral)
    if not _tex(src, 'LayerMaskMap'):
        for v in m['Shader'][0]['Values']:
            if v['Name'] == 'NumMaterialLayer':
                v['Value'] = '1'
    for s in m.get('Samplers', []):
        if s.get('RepeatU') == 'MIRROR':
            s['RepeatU'] = 'WRAP'
    m['Name'] = src['Name']
    return m, sv_shader, za_shader


# ---------------------------------------------------------------------------------------------------------
# the port
# ---------------------------------------------------------------------------------------------------------

def _anims_by(files_rel, folder_rel):
    by_slot, by_name = {}, {}
    for rel in files_rel:
        if not rel.startswith(folder_rel + '/'):
            continue
        m = ANIM.match(rel.rsplit('/', 1)[-1])
        if m:
            by_slot.setdefault((m.group(1), m.group(2)), {})[m.group(3)] = rel
    for (code, name), exts in by_slot.items():
        by_name.setdefault(name, []).append((code, exts))
    return by_slot, by_name


def _pick_anim(code, name, by_slot, by_name):
    if (code, name) in by_slot:
        return by_slot[(code, name)], False
    for c, exts in sorted(by_name.get(name, [])):
        return exts, False
    for alt in FALLBACK.get(name, []) + (['defaultwait01_loop'] if name.endswith('loop') else ['defaultidle01']):
        if by_name.get(alt):
            return sorted(by_name[alt])[0][1], True
    return sorted(by_name['defaultwait01_loop'])[0][1], True


def port_model(root, sv, mod, species, host_folder, sv_folder=None, log=print):
    """Copy the Scarlet/Violet model folder into the mod and make it Z-A-ready. Returns a report dict."""
    sv_folder = sv_folder or 'pm%04d_00_00' % species
    stem = sv_folder
    src_dir = os.path.join(sv, 'data', sv_folder[:6], sv_folder)
    rel_dir = 'ik_pokemon/data/%s/%s' % (sv_folder[:6], sv_folder)
    hdir = os.path.join(root, 'ik_pokemon', 'data', host_folder[:6], host_folder)
    if not os.path.isdir(src_dir):
        raise PortError('Model folder not found in the dump: ' + src_dir)
    rep = {'folder': rel_dir, 'materials': [], 'stand_ins': [], 'dropped': [], 'host': host_folder}
    skip_names = {stem + s for s in SUPPORT}
    # 1. the Scarlet/Violet files that are kept: mesh, skeleton, textures, locators, animations, model, materials
    trmmt = open(os.path.join(src_dir, stem + '.trmmt'), 'rb').read() if os.path.isfile(
        os.path.join(src_dir, stem + '.trmmt')) else b''
    used_mtr = {stem + '.trmtr'} | {m.decode() for m in re.findall(rb'[a-z0-9_]+\.trmtr', trmmt)}
    for dp, dn, fn in os.walk(src_dir):
        for f in fn:
            sub = os.path.relpath(os.path.join(dp, f), src_dir).replace(os.sep, '/')
            if f in skip_names:
                continue                                   # replaced by the host's Z-A versions
            if sub.startswith('icon/'):
                continue                                   # Z-A icons are made separately
            if f.endswith('.trmtr') and f not in used_mtr:
                rep['dropped'].append(f)
                continue
            if ANIM.match(f):
                continue                                   # animations are placed per the host's list below
            mod.add_file(rel_dir + '/' + sub, os.path.join(dp, f))
    log('copied the model (mesh, skeleton, textures, locators)')
    # 2. materials rebuilt with Z-A shaders
    pika = os.path.join(root, 'ik_pokemon', 'data', 'pm0025', 'pm0025_00_00')
    for k in ('white_msk', 'black_msk'):
        mod.add_file('%s/%s_%s.bntx' % (rel_dir, stem, k), os.path.join(pika, 'pm0025_00_00_%s.bntx' % k))
    for f in sorted(used_mtr):
        p = mod.path_for(rel_dir + '/' + f)
        if not os.path.isfile(p):
            continue
        d = json.loads(zm.to_json(p, TRMTR))
        mats = []
        for s in d['Materials']:
            m, a, b = convert_material(root, s, stem, lambda n, src: mod.add_file(rel_dir + '/' + n, src))
            mats.append(m)
            rep['materials'].append('%s: %s -> %s' % (s['Name'], a, b))
        d['Materials'] = mats
        zm.from_json(json.dumps(d), TRMTR, p)
    log('materials: ' + '; '.join(rep['materials']))
    # 3. the host's animation system, config, alpha config and collision, renamed
    gb = bones(mod.path_for(rel_dir + '/' + stem + '.trskl'))
    for s in SUPPORT:
        p = os.path.join(hdir, host_folder + s)
        if not os.path.isfile(p):
            continue
        data = open(p, 'rb').read().replace(host_folder.encode(), stem.encode())
        if s == '_defence.hkx':
            need = {x.decode()[4:] for x in re.findall(rb'rdb_[a-z0-9_]+', data)}
            if not need <= gb:
                data = _waist_ragdoll(root)              # a waist-only ragdoll fits any skeleton
                rep['ragdoll'] = 'waist-only (the host\'s needs %s)' % ', '.join(sorted(need - gb))
        mod.add_bytes(rel_dir + '/' + stem + s, data)
    # 4. every animation the host's list names
    tracr = open(os.path.join(hdir, host_folder + '_base.tracr'), 'rb').read()
    wanted = sorted({m.decode() for m in re.findall(rb'pm\d{4}_\d\d_\d\d_\d{5}_[a-z0-9_]+\.(?:tranm|tracm|traef|trcrv)', tracr)})
    sv_files = []
    for f in os.listdir(src_dir):
        if ANIM.match(f):
            sv_files.append(rel_dir + '/' + f)
    by_slot, by_name = _anims_by(sv_files, rel_dir)
    if not by_name.get('defaultwait01_loop'):
        raise PortError('The model has no idle animation (defaultwait01_loop).')
    for w in wanted:
        m = ANIM.match(w)
        code, nm, ext = m.group(1), m.group(2), m.group(3)
        exts, stand_in = _pick_anim(code, nm, by_slot, by_name)
        srcrel = exts.get(ext)
        dst = rel_dir + '/' + w.replace(host_folder, stem)
        if srcrel:
            mod.add_file(dst, os.path.join(src_dir, srcrel.rsplit('/', 1)[-1]))
        else:
            hp = os.path.join(hdir, w)                     # no file of this kind for it: the host's (renamed)
            if not os.path.isfile(hp):
                continue
            mod.add_bytes(dst, open(hp, 'rb').read().replace(host_folder.encode(), stem.encode()))
            stand_in = True
        if stand_in:
            rep['stand_ins'].append('%s_%s' % (code, nm))
    rep['stand_ins'] = sorted(set(rep['stand_ins']))
    log('animation slots: %d files, stand-ins for %d' % (len(wanted), len(rep['stand_ins'])))
    return rep


_RAGDOLL = {}


def _waist_ragdoll(root):
    """A Z-A collision/ragdoll file that only uses the waist bone (every Pokémon skeleton has one)."""
    if 'b' not in _RAGDOLL:
        _RAGDOLL['b'] = open(os.path.join(root, 'ik_pokemon', 'data', 'pm0498', 'pm0498_00_00',
                                          'pm0498_00_00_defence.hkx'), 'rb').read()
    return _RAGDOLL['b']


def add_catalog(root, sv, mod, species, gender=0):
    za, sch = load(root, mod, CATALOG)
    svc = sv_catalog(sv)
    e = next((r for r in svc if r.get('species_info', {}).get('species') == species
              and not r['species_info'].get('form') and r['species_info'].get('gender', 0) == gender), None)
    if e is None:
        raise PortError('The dump has no resource entry for species %d.' % species)
    e = json.loads(json.dumps(e))
    stem = e['model_path'].split('/')[1]
    e['defence_path'] = '%s/%s/%s_defence.hkx' % (stem[:6], stem, stem)
    table = za['table']
    table[:] = [r for r in table if not (r.get('species_info', {}).get('species') == species and
                                          r['species_info'].get('gender', 0) == gender and not r['species_info'].get('form'))]
    # after the Pokémon's earlier species, and the male / genderless model before the female one (lookups take the
    # first entry of a species)
    i = next((i for i, r in enumerate(table) if r.get('species_info', {}).get('species', 0) > species or
              (r.get('species_info', {}).get('species', 0) == species and r['species_info'].get('gender', 0) > gender)),
             len(table))
    table.insert(i, e)
    save(mod, CATALOG, za, sch)
    return e


def usable_moves(root):
    d, _ = load(root, None, WAZA)
    rows = _rows(d)
    return {r.get('MoveID') for r in rows if r.get('CanUseMove')}, {r.get('MoveID'): r for r in rows}


def update_personal(root, mod, species, log=print, dex=None):
    """Flag on; moves limited to ones Z-A can use; evolutions only to species in the game. Returns notes."""
    d, sch = load(root, mod, PERSONAL)
    rows = d['entries']
    present = {r['species']['species_index'] for r in rows if r.get('is_present')} | {species}
    usable, _ = usable_moves(root)
    notes = []
    for r in rows:
        if r['species']['species_index'] != species:
            continue
        r['is_present'] = True
        before = len(r.get('learnset') or [])
        r['learnset'] = [m for m in r.get('learnset') or [] if m.get('move') in usable]
        for k in ('egg_moves', 'reminder_moves', 'technical_machines'):
            if r.get(k):
                r[k] = [m for m in r[k] if m in usable] if k != 'technical_machines' else r[k]
        evo = r.get('evo_table') or []
        r['evo_table'] = [e for e in evo if e.get('species') in present]
        if r['species']['species_form'] == 0:
            notes.append('moves kept %d of %d (Z-A-usable); evolutions kept %d of %d' % (
                len(r['learnset']), before, len(r['evo_table']), len(evo)))
        if dex is not None:
            r['dex'] = {'index': dex, 'group': 0}
    save(mod, PERSONAL, d, sch)
    return notes


def add_params(root, mod, species, host_species, guest_height=0):
    """Model size and movement rows copied from the host (collision scaled to the guest's height)."""
    host_h = next((r['species']['height'] for r in personal_rows(root) if r['species']['species_index'] == host_species
                   and r['species']['species_form'] == 0), 0)
    scale = guest_height / host_h if guest_height and host_h else 1.0
    for rel, key in ((MODEL_PARAM, 'devId'), (MOVE_PARAM, 'devNo')):
        d, sch = load(root, mod, rel)
        groups = d['values']
        groups[:] = [g for g in groups if g['root'][0].get(key) != species]
        src = next((g for g in groups if g['root'][0].get(key) == host_species), None)
        if src is None:
            raise PortError('The host Pokémon #%d has no row in %s.' % (host_species, rel.rsplit('/', 1)[-1]))
        g = json.loads(json.dumps(src))
        g['root'] = [g['root'][0]]
        g['root'][0][key] = species
        g['root'][0].pop('formNo', None)
        if key == 'devId':
            for k in ('stageCollision', 'interactionCollision', 'npcCollision'):
                c = g['root'][0].get(k)
                if isinstance(c, dict) and 'radius' in c:
                    c['radius'] = round(c['radius'] * scale, 4)
        i = next((i for i, x in enumerate(groups) if x['root'][0].get(key, 0) > species), len(groups))
        groups.insert(i, g)
        save(mod, rel, d, sch)


def add_alpha_move(root, mod, species, host_species):
    """The alpha move: the strongest Z-A-usable damaging move in its level-up moves (else the host's)."""
    usable, moves = usable_moves(root)
    learn = next((r.get('learnset') or [] for r in personal_rows(root, mod) if r['species']['species_index'] == species
                  and r['species']['species_form'] == 0), [])
    best = None
    for m in learn:
        mv = moves.get(m.get('move'))
        if mv and m.get('move') in usable and (mv.get('Power') or 0) > 0 and (m.get('level') or 0) <= 60:
            if best is None or (mv.get('Power') or 0) > (moves[best].get('Power') or 0):
                best = m.get('move')
    d, sch = load(root, mod, OYABUN)
    rows = d['root']
    rows[:] = [r for r in rows if r.get('devNo') != species]
    src = next((r for r in rows if r.get('devNo') == host_species), None)
    if src is None:
        raise PortError('The host Pokémon #%d has no alpha move row.' % host_species)
    r = json.loads(json.dumps(src))
    r['devNo'] = species
    if best:
        r['formTableList'] = [{'wazaNo': best}]
    rows.append(r)
    save(mod, OYABUN, d, sch)
    return best or (r['formTableList'][0].get('wazaNo') if r.get('formTableList') else None)


def add_behaviour(root, mod, species, host_species):
    """Wild behaviour (temperament, senses, home range) copied from the host."""
    host_name = '%04d_00_00' % host_species
    src = _game(root, '%s/%s.bin' % (WILD_DIR, host_name))
    if not os.path.isfile(src):
        return None
    name = '%04d_00_00' % species
    for ext in ('.bin', '.bfbs'):
        p = _game(root, '%s/%s%s' % (WILD_DIR, host_name, ext))
        if os.path.isfile(p):
            mod.add_file('%s/%s%s' % (WILD_DIR, name, ext), p)
    d, sch = load(root, mod, WILD_CAT)
    if name not in d['names']:
        d['names'].append(name)
        d['names'].sort()
        save(mod, WILD_CAT, d, sch)
    return name


# ---- cry -----------------------------------------------------------------------------------------------

def _voice_rel(root):
    import za_soundbank as sb
    return os.path.relpath(os.path.join(sb.bank_dir(root), 'PM_VOICE.bnk'), root).replace(os.sep, '/')


def add_cry(root, mod, species, from_species, audio=None):
    """Event PLAY_PV_<species>: a copy of another Pokémon's cry, or - with audio - a new sound from that file."""
    import za_soundbank as sb
    rel = _voice_rel(root)
    path = mod.path_for(rel) if os.path.isfile(mod.path_for(rel)) else _game(root, rel)
    b = sb.Bank(open(path, 'rb').read())
    ev_new = sb.fnv1('PLAY_PV_%04d' % species)
    ev_src = sb.fnv1('PLAY_PV_%04d' % from_species)
    if ev_src not in b.by_id:
        raise PortError('Species %d has no cry to copy.' % from_species)
    h = b.section(b'HIRC')
    if ev_new in b.by_id:                              # replace an earlier one: drop the old event
        o = b.by_id[ev_new]
        h[o[2] - 9:o[3]] = b''
        struct.pack_into('<I', h, 0, struct.unpack_from('<I', h, 0)[0] - 1)
        b = sb.Bank(b.tobytes())
        h = b.section(b'HIRC')
    body = bytes(b.body(ev_src))
    n = body[0]
    actions = list(struct.unpack_from('<%dI' % n, body, 1))
    rng = random.Random(species * 7919)

    def new_id():
        i = rng.getrandbits(32)
        while i in b.by_id or i == 0:
            i = rng.getrandbits(32)
        b.by_id[i] = None
        return i
    objs, new_actions = [], []
    sound_id = None
    if audio:
        idx = sb.load_index(root) or {}
        ent = next((e for e in idx.get('entries', []) if e.get('name') == 'PLAY_PV_%04d' % from_species and e.get('objects')), None)
        if not ent:
            raise PortError('Open the Audio tab once (it builds the sound list) before making a cry from a file.')
        leaf = ent['objects'][0]
        lbody = bytearray(b.body(leaf))
        rate, samples = sb.load_audio(audio)
        wem = sb.make_wem(rate, samples)
        mid = new_id()
        struct.pack_into('<IBII', lbody, 0, sb.PCM, 0, mid, len(wem))
        sound_id = new_id()
        objs.append(struct.pack('<BII', sb.SOUND, len(lbody) + 4, sound_id) + bytes(lbody))
    for aid in actions:
        ab = bytearray(b.body(aid))
        if sound_id:
            struct.pack_into('<I', ab, 2, sound_id)
        nid = new_id()
        objs.append(struct.pack('<BII', sb.ACTION, len(ab) + 4, nid) + bytes(ab))
        new_actions.append(nid)
    eb = bytes([len(new_actions)]) + struct.pack('<%dI' % len(new_actions), *new_actions) + body[1 + 4 * n:]
    objs.append(struct.pack('<BII', sb.EVENT, len(eb) + 4, ev_new) + eb)
    nh = bytearray(h) + b''.join(objs)
    struct.pack_into('<I', nh, 0, struct.unpack_from('<I', h, 0)[0] + len(objs))
    b._set_section(b'HIRC', nh, after=b'DATA')
    if audio:
        b2 = sb.Bank(b.tobytes())
        b2.set_media(mid, wem)
        data = b2.tobytes()
    else:
        data = b.tobytes()
    mod.add_bytes(rel, data)
    sb.Bank(data)                                      # must still parse
    return 'PLAY_PV_%04d (%s)' % (species, 'own sound: ' + os.path.basename(audio) if audio
                                  else 'copy of #%d\'s cry' % from_species)


# ---- Pokédex -------------------------------------------------------------------------------------------

def dex_entries(root, mod=None):
    d, _ = load(root, mod, DEX)
    return d['entries']


def set_pokedex(root, mod, species, mode, other=None):
    """mode 'none' | 'replace' (take over species `other`'s entry and number) | 'append' (a new last entry,
    experimental: the game's Regular/Hyperspace sizes are partly fixed in its code). Returns the dex number or None."""
    if mode == 'none':
        return None
    d, sch = load(root, mod, DEX)
    entries = d['entries']
    pd, psch = load(root, mod, PERSONAL)
    rows = pd['entries']
    if any(e.get('species') == species for e in entries):     # ported before: keep the entry it already has
        return next(((r.get('dex') or {}).get('index') for r in rows if r['species']['species_index'] == species
                     and r['species']['species_form'] == 0), None)
    if mode == 'replace':
        e = next((e for e in entries if e.get('species') == other), None)
        if e is None:
            raise PortError('#%d is not in the Pokédex.' % other)
        e['species'] = species
        num = next(((r.get('dex') or {}).get('index') for r in rows if r['species']['species_index'] == other), 0)
        for r in rows:
            if r['species']['species_index'] == other:
                r['dex'] = {'index': 0, 'group': 0}
    else:
        num = max((r.get('dex') or {}).get('index', 0) for r in rows) + 1
        entries.append({'order': len(entries) + 1, 'species': species, 'dex': 1})
    for r in rows:
        if r['species']['species_index'] == species:
            r['dex'] = {'index': num, 'group': 0}
    for i, e in enumerate(entries):
        e['order'] = i + 1
    save(mod, DEX, d, sch)
    save(mod, PERSONAL, pd, psch)
    return num


# ---- icons ---------------------------------------------------------------------------------------------

def write_icons(root, mod, folder_rel, stem, png_normal, png_shiny):
    """160x160 Z-A icons (_00_0 normal, _00_1 shiny) from two PNGs, encoded like the game's own."""
    from PIL import Image
    import tempfile
    for k, png in (('0', png_normal), ('1', png_shiny)):
        tpl = open(os.path.join(root, 'ik_pokemon', 'data', 'pm0025', 'pm0025_00_00', 'icon',
                                'pm0025_00_00_00_%s.bntx' % k), 'rb').read()
        with tempfile.TemporaryDirectory() as td:
            small = os.path.join(td, 'icon.png')
            Image.open(png).convert('RGBA').resize((160, 160), Image.LANCZOS).save(small)
            data = zm.import_png(small, tpl)
        data = data[0] if isinstance(data, tuple) else data
        data = data.replace(b'pm0025_00_00_00_' + k.encode(), (stem + '_00_' + k).encode())
        mod.add_bytes('%s/icon/%s_00_%s.bntx' % (folder_rel, stem, k), data)


# ---- spawns --------------------------------------------------------------------------------------------

def wild_zones(root):
    """[(zone id, display name)] of the wild zones."""
    import za_formats as zf
    base = os.path.join(root, 'ik_message', 'dat', 'English', 'common', 'place_name')
    try:
        lab = dict(zip(zf.parse_tbl(open(base + '.tbl', 'rb').read()), zf.decode_msg(open(base + '.dat', 'rb').read())))
    except (OSError, ValueError):
        lab = {}
    out = []
    for k, v in lab.items():
        m = re.match(r'PLACENAME_wild_(a\d{4}_w\d\d)$', k)
        if m:
            out.append((m.group(1), v))

    def key(z):
        n = re.search(r'(\d+)$', z[1])
        return (int(n.group(1)) if n else 999, z[0])
    return sorted(out, key=key)


def add_spawns(root, mod, species, zone, count=3, levels=(5, 10)):
    """`count` new spawn points in a wild zone (copies of its first points, 4 m away) that spawn this Pokémon."""
    import glob
    scenes = glob.glob(os.path.join(root, 'world', 'ik_scene', '**', '*_%s_pokemon_spawner_0.trscn' % zone), recursive=True)
    if not scenes:
        raise PortError('No spawner scene for zone %s.' % zone)
    scene_rel = os.path.relpath(scenes[0], root).replace(os.sep, '/')
    sd, ssch = load(root, mod, SPAWNER)
    srows = [(gi, i, r) for gi, g in enumerate(sd['values']) for i, r in enumerate(g.get('root', []))]
    zone_sp = [x for x in srows if any((o.get('zoneInfo') or {}).get('zoneId') == zone
                                       for o in x[2].get('appearanceSpawnerObjectInfoList', []))]
    if not zone_sp:
        raise PortError('No spawn points in zone %s.' % zone)
    tmpl_points = zone_sp[:count]
    taken = {o.get('objectName') for _, _, r in srows for o in r.get('appearanceSpawnerObjectInfoList', [])}
    for other in zm.list_mods():                       # names other mods use too, so mods can be installed together
        if os.path.normcase(other.romfs) == os.path.normcase(mod.romfs) or not os.path.isfile(other.path_for(SPAWNER)):
            continue
        try:
            od, _ = load(root, other, SPAWNER)
            taken |= {o.get('objectName') for g in od['values'] for r in g.get('root', [])
                      for o in r.get('appearanceSpawnerObjectInfoList', [])}
        except Exception:  # noqa: BLE001
            pass
    enc_id = 'ect_%s_port_z%04d' % (zone, species)
    already = [o.get('objectName') for _, _, r in srows if any(e.get('encountDataId') == enc_id
                                                                for e in r.get('encountDataInfoList', []))
               for o in r.get('appearanceSpawnerObjectInfoList', [])]
    if already:                                      # ported before: keep its points, don't add more
        return already
    # new names: the template's name with its last 3 digits replaced by an unused 9xx (same length)
    new = []
    k = 900
    for _, _, r in tmpl_points:
        old = r['appearanceSpawnerObjectInfoList'][0]['objectName']
        if len(old) < 4 or not old[-3:].isdigit():
            raise PortError("Spawn point %s has an unexpected name, so it can't be copied." % old)
        while True:
            k += 1
            if k > 999:
                raise PortError('No free spawn point names left in zone %s.' % zone)
            cand = old[:-3] + '%03d' % k
            if cand not in taken:
                break
        taken.add(cand)
        new.append((old, cand, r))
    # everything is prepared in memory first and only saved when every part worked, so a failure leaves the
    # mod as it was
    # encounter
    ed, esch = load(root, mod, ENCOUNT)
    tmpl_enc_id = new[0][2]['encountDataInfoList'][0]['encountDataId']
    erows = [(gi, i, r) for gi, g in enumerate(ed['values']) for i, r in enumerate(g.get('root', []))]
    hit = next(((gi, i, r) for gi, i, r in erows if r.get('id') == tmpl_enc_id), None)
    if hit is None:
        raise PortError('The encounter %s of spawn point %s is missing.' % (tmpl_enc_id, new[0][0]))
    gi, i, r = hit
    e = copy.deepcopy(r)
    e['id'] = enc_id
    e['devNo'] = species
    e.pop('formNo', None)
    e['minLevel'], e['maxLevel'] = int(levels[0]), int(levels[1])
    ed['values'][gi]['root'].insert(i + 1, e)
    # spawner rows
    for old, cand, r in new:
        gi, i = next((gi, i) for gi, i, x in srows if x is r)
        s = copy.deepcopy(r)
        s['id'] = 'id_' + cand
        for o in s['appearanceSpawnerObjectInfoList']:
            o['objectName'] = cand
        info = copy.deepcopy(s['encountDataInfoList'][0])
        info['encountDataId'] = enc_id
        s['encountDataInfoList'] = [info]
        sd['values'][gi]['root'].insert(i + 1, s)
    # positions
    xd, xsch = load(root, mod, XFORM)
    pos = {}
    for old, cand, r in new:
        for g in xd['values']:
            rr = g.get('root', [])
            j = next((j for j, p in enumerate(rr) if p.get('Name') == old), None)
            if j is not None:
                p = copy.deepcopy(rr[j])
                pos[old] = (p['Position'].get('X', 0.0), p['Position'].get('Y', 0.0), p['Position'].get('Z', 0.0))
                p['Name'] = cand
                p['Position']['X'] = p['Position'].get('X', 0.0) + 4.0
                p['Position']['Z'] = p['Position'].get('Z', 0.0) + 4.0
                rr.insert(j + 1, p)
                break
        else:
            raise PortError('Spawn point %s has no position row.' % old)
    # scene: copy each template point's chunk tree, renamed, with fresh ids for its own chunks, moved 4 m
    sc, scsch = load(root, mod, scene_rel)
    chunks = sc['Chunks']

    def all_ids(cs, out):
        for c in cs:
            out.append(c.get('Id'))
            all_ids(c.get('Children') or [], out)
        return out
    ids = all_ids(chunks, [])
    unique = {i for i in ids if ids.count(i) == 1}
    taken_ids = set(ids)
    rng = random.Random(species * 104729 + len(chunks))

    def rename(c, old, cand):
        b = bytes(c.get('Data', []))
        if old.encode() in b:
            c['Data'] = list(b.replace(old.encode(), cand.encode()))
        if c.get('Id') in unique:
            nid = rng.getrandbits(64)
            while nid in taken_ids:
                nid = rng.getrandbits(64)
            taken_ids.add(nid)
            c['Id'] = nid
        for x in c.get('Children') or []:
            rename(x, old, cand)
    for old, cand, r in new:
        i = next((i for i, c in enumerate(chunks) if old.encode() in bytes(c.get('Data', []))), None)
        if i is None:
            raise PortError("Spawn point %s isn't in the scene file %s." % (old, scene_rel))
        c = copy.deepcopy(chunks[i])
        rename(c, old, cand)
        b = bytearray(c['Data'])
        off = pos_offset(b, *pos[old])
        if off is None:
            raise PortError("Could not find where %s's position is stored in its scene file." % old)
        x, y, z = struct.unpack_from('<3f', b, off)
        struct.pack_into('<3f', b, off, x + 4.0, y, z + 4.0)
        c['Data'] = list(b)
        chunks.insert(i + 1, c)
    save(mod, ENCOUNT, ed, esch)
    save(mod, SPAWNER, sd, ssch)
    save(mod, XFORM, xd, xsch)
    save(mod, scene_rel, sc, scsch)
    return [c for _, c, _ in new]


def pos_offset(data, x, y, z):
    """Byte offset of a position (three floats) inside a scene chunk's data, or None."""
    b = bytes(data)
    for k in range(0, len(b) - 11, 4):
        a, bb, c = struct.unpack_from('<3f', b, k)
        if abs(a - x) < 0.01 and abs(bb - y) < 0.01 and abs(c - z) < 0.01:
            return k
    return None


# ---- check ---------------------------------------------------------------------------------------------

def check(root, mod, species):
    """Problems that would stop a ported Pokémon working: [(ok, text)]."""
    out = []
    rows = {(r['species']['species_index'], r['species']['species_form']): r for r in personal_rows(root, mod)}
    r = rows.get((species, 0))
    out.append((bool(r and r.get('is_present')), 'species row present and flagged "in this game"'))
    cat, _ = load(root, mod, CATALOG)
    e = next((x for x in sorted(cat['table'], key=lambda x: x.get('species_info', {}).get('gender', 0))
              if x.get('species_info', {}).get('species') == species and not x['species_info'].get('form')), None)
    out.append((e is not None, 'resource table entry'))
    if e:
        stem = e['model_path'].split('/')[1]
        rel_dir = 'ik_pokemon/data/%s/%s' % (stem[:6], stem)
        d = mod.path_for(rel_dir)
        have = set(os.listdir(d)) if os.path.isdir(d) else set()
        missing = []
        for f in [x for x in have if x.endswith(('.tracn', '.tracr', '.trmdl', '.trmmt', '.trpokecfg'))]:
            for ref in set(re.findall(rb'%s[a-z0-9_]*\.[a-z]{4,5}' % stem.encode(), open(os.path.join(d, f), 'rb').read())):
                ref = ref.decode()
                if ref not in have and not os.path.isfile(os.path.join(d, 'locators', ref)) and not \
                        os.path.isfile(os.path.join(d, 'icon', ref)):
                    missing.append(ref)
        base_refs = {'%s.tracn' % stem, '%s_00000.trskl' % stem, '%s_10000.trskl' % stem}
        missing = sorted(set(missing) - base_refs)
        out.append((not missing, 'every file the model and animation system name exists' +
                    ('' if not missing else ' - missing: ' + ', '.join(missing[:6]))))
        za_shaders = set(templates(root))
        bad = []
        for f in [x for x in have if x.endswith('.trmtr')]:
            for m in json.loads(zm.to_json(os.path.join(d, f), TRMTR))['Materials']:
                if m['Shader'][0]['Name'] not in za_shaders:
                    bad.append('%s/%s (%s)' % (f, m['Name'], m['Shader'][0]['Name']))
        out.append((not bad, 'materials use Z-A shaders' + ('' if not bad else ' - not: ' + ', '.join(bad[:4]))))
        icons = all(os.path.isfile(os.path.join(d, 'icon', '%s_00_%s.bntx' % (stem, k))) for k in '01')
        out.append((icons, 'icons (normal and shiny)'))
    for rel, key, label in ((MODEL_PARAM, 'devId', 'model size row'), (MOVE_PARAM, 'devNo', 'movement row')):
        dd, _ = load(root, mod, rel)
        out.append((any(g['root'][0].get(key) == species for g in dd['values']), label))
    oy, _ = load(root, mod, OYABUN)
    out.append((any(x.get('devNo') == species for x in oy['root']), 'alpha move'))
    import za_soundbank as sb
    rel = _voice_rel(root)
    bp = mod.path_for(rel) if os.path.isfile(mod.path_for(rel)) else _game(root, rel)
    out.append((sb.fnv1('PLAY_PV_%04d' % species) in sb.Bank(open(bp, 'rb').read()).by_id, 'cry'))
    return out


def port(root, sv, mod, species, host, options, log=print):
    """Everything in one go. options: dex ('none'|'replace'|'append'), dex_other, cry_audio, cry_from, behaviour,
    spawn_zone, spawn_count, spawn_levels, icons (normal png, shiny png). Returns a report dict."""
    rows = {(r['species']['species_index'], r['species']['species_form']): r for r in personal_rows(root, mod)}
    r = rows.get((species, 0))
    if r is None:
        raise PortError('Z-A has no data slot for #%d (its species list ends at 1010).' % species)
    if r.get('is_present') and not os.path.isfile(mod.path_for(PERSONAL)):
        raise PortError('#%d is already in Z-A.' % species)
    height = r['species'].get('height', 0)
    host_species = host['species']
    mod.snapshot()
    rep = {'species': species}
    rep['model'] = port_model(root, sv, mod, species, host['folder'], log=log)
    if options.get('female'):
        fem = 'pm%04d_01_00' % species
        if os.path.isdir(os.path.join(sv, 'data', fem[:6], fem)):
            port_model(root, sv, mod, species, host['folder'], sv_folder=fem, log=log)
            add_catalog(root, sv, mod, species, gender=1)
            rep['female'] = True
    rep['catalog'] = add_catalog(root, sv, mod, species)['model_path']
    rep['personal'] = update_personal(root, mod, species, log)
    add_params(root, mod, species, host_species, height)
    rep['alpha_move'] = add_alpha_move(root, mod, species, host_species)
    rep['cry'] = add_cry(root, mod, species, options.get('cry_from') or host_species, options.get('cry_audio'))
    if options.get('behaviour', True):
        rep['behaviour'] = add_behaviour(root, mod, species, host_species)
    rep['dex'] = set_pokedex(root, mod, species, options.get('dex', 'none'), options.get('dex_other'))
    if options.get('icons'):
        stem = 'pm%04d_00_00' % species
        write_icons(root, mod, 'ik_pokemon/data/pm%04d/%s' % (species, stem), stem, *options['icons'])
        rep['icons'] = True
    if options.get('spawn_zone'):
        rep['spawns'] = add_spawns(root, mod, species, options['spawn_zone'], options.get('spawn_count', 3),
                                   options.get('spawn_levels', (5, 10)))
    rep['check'] = check(root, mod, species)
    mod.snapshot('ported #%d' % species)
    return rep


# ---------------------------------------------------------------------------------------------------------
# the window
# ---------------------------------------------------------------------------------------------------------

ZOOMS = {'Close': (0.58, 0.3), 'Medium': (0.75, 0.18), 'Full': (1.0, 0.04)}


class PortWindow:
    """Pokémon data tab > Port a Pokémon…"""

    def __init__(self, app):
        import tkinter as tk
        from tkinter import ttk
        self.app = app
        self.root = app.root_var.get()
        self.cands = []
        self.host_list = []
        w = self.win = tk.Toplevel(app)
        w.title('Port a Pokémon from Scarlet/Violet')
        w.geometry('1080x720')
        bg = ttk.Style(w).lookup('TFrame', 'background') or None
        if bg:
            w.configure(background=bg)
        top = ttk.Frame(w, padding=(10, 10, 10, 4))
        top.pack(fill='x')
        ttk.Label(top, text='Scarlet/Violet dump:').pack(side='left')
        self.sv_var = tk.StringVar(value=app.settings.get('sv_dump', ''))
        ttk.Entry(top, textvariable=self.sv_var).pack(side='left', fill='x', expand=True, padx=4)
        ttk.Button(top, text='Browse…', command=self.browse).pack(side='left')
        ttk.Button(top, text='Load', command=self.load).pack(side='left', padx=(4, 0))
        ttk.Label(w, style='Dim.TLabel', wraplength=1040, justify='left', padding=(10, 0), text=(
            'Brings a Pokémon from your Scarlet/Violet dump (its romfs, or the folder with pokemon/data) into the '
            'current mod, in a species slot Z-A has data for but doesn\'t use. Nothing in the game is replaced. '
            'Its model keeps its own animations; a Z-A Pokémon that moves the same way lends it Z-A\'s animation '
            'system. Test with a copy of your save: a caught ported Pokémon needs the mod installed.')).pack(fill='x')
        body = ttk.PanedWindow(w, orient='horizontal')
        body.pack(fill='both', expand=True, padx=10, pady=6)
        left = ttk.Frame(body)
        body.add(left, weight=1)
        f = ttk.Frame(left)
        f.pack(fill='x')
        ttk.Label(f, text='Find:').pack(side='left')
        self.q = tk.StringVar()
        ttk.Entry(f, textvariable=self.q).pack(side='left', fill='x', expand=True, padx=3)
        self.q.trace_add('write', lambda *a: self.fill())
        self.only_free = tk.BooleanVar(value=True)
        ttk.Checkbutton(left, text='Only free slots', variable=self.only_free, command=self.fill).pack(anchor='w')
        lf = ttk.Frame(left)
        lf.pack(fill='both', expand=True, pady=(4, 0))
        self.lst = ttk.Treeview(lf, columns=('name', 'state'), show='headings', selectmode='browse')
        self.lst.heading('name', text='Pokémon')
        self.lst.heading('state', text='Slot')
        self.lst.column('name', width=170)
        self.lst.column('state', width=170)
        sb = ttk.Scrollbar(lf, orient='vertical', command=self.lst.yview)
        self.lst.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.lst.pack(side='left', fill='both', expand=True)
        self.lst.bind('<<TreeviewSelect>>', lambda e: self.select())
        right = ttk.Frame(body, padding=(10, 0, 0, 0))
        body.add(right, weight=2)
        self.title = tk.StringVar(value='Load your dump, then pick a Pokémon.')
        ttk.Label(right, textvariable=self.title, style='Head.TLabel').pack(anchor='w')
        hf = ttk.LabelFrame(right, text='Animation system from (a Z-A Pokémon that moves the same way)', padding=6)
        hf.pack(fill='x', pady=(6, 4))
        self.host_var = tk.StringVar()
        self.host_box = ttk.Combobox(hf, textvariable=self.host_var, state='readonly', width=70)
        self.host_box.pack(fill='x')
        self.host_note = tk.StringVar()
        ttk.Label(hf, textvariable=self.host_note, style='Dim.TLabel', wraplength=640, justify='left').pack(anchor='w')
        of = ttk.LabelFrame(right, text='Options', padding=6)
        of.pack(fill='x', pady=4)
        self.behaviour = tk.BooleanVar(value=True)
        ttk.Checkbutton(of, text='Wild behaviour (temperament, sight, home range) from the same Pokémon',
                        variable=self.behaviour).grid(row=0, column=0, columnspan=4, sticky='w')
        self.female = tk.BooleanVar(value=True)
        ttk.Checkbutton(of, text='Female model too, if it has one', variable=self.female).grid(
            row=1, column=0, columnspan=4, sticky='w')
        ttk.Label(of, text='Cry:').grid(row=2, column=0, sticky='w', pady=(6, 0))
        self.cry_mode = tk.StringVar(value='host')
        ttk.Radiobutton(of, text='the same Pokémon\'s', value='host', variable=self.cry_mode).grid(row=2, column=1, sticky='w', pady=(6, 0))
        ttk.Radiobutton(of, text='from a sound file:', value='file', variable=self.cry_mode).grid(row=2, column=2, sticky='w', pady=(6, 0))
        self.cry_file = tk.StringVar()
        cf = ttk.Frame(of)
        cf.grid(row=2, column=3, sticky='ew', pady=(6, 0))
        ttk.Entry(cf, textvariable=self.cry_file, width=28).pack(side='left', fill='x', expand=True)
        ttk.Button(cf, text='…', width=3, command=self.pick_cry).pack(side='left')
        ttk.Label(of, text='Pokédex:').grid(row=3, column=0, sticky='w', pady=(6, 0))
        self.dex_mode = tk.StringVar(value='none')
        ttk.Radiobutton(of, text='not listed', value='none', variable=self.dex_mode).grid(row=3, column=1, sticky='w', pady=(6, 0))
        ttk.Radiobutton(of, text='take over the entry of:', value='replace', variable=self.dex_mode).grid(row=3, column=2, sticky='w', pady=(6, 0))
        self.dex_other = tk.StringVar()
        self.dex_box = ttk.Combobox(of, textvariable=self.dex_other, width=28)
        self.dex_box.grid(row=3, column=3, sticky='ew', pady=(6, 0))
        ttk.Radiobutton(of, text='add as a new entry (experimental: the game may only show 364)', value='append',
                        variable=self.dex_mode).grid(row=4, column=1, columnspan=3, sticky='w')
        ttk.Label(of, text='Spawns:').grid(row=5, column=0, sticky='w', pady=(6, 0))
        self.zone_var = tk.StringVar(value='none')
        self.zone_box = ttk.Combobox(of, textvariable=self.zone_var, state='readonly', width=24)
        self.zone_box.grid(row=5, column=1, sticky='w', pady=(6, 0))
        sf = ttk.Frame(of)
        sf.grid(row=5, column=2, columnspan=2, sticky='w', pady=(6, 0))
        self.count = tk.IntVar(value=3)
        self.lv0, self.lv1 = tk.IntVar(value=5), tk.IntVar(value=10)
        for lab, var, lo, hi in (('points', self.count, 1, 10), ('level', self.lv0, 1, 100), ('to', self.lv1, 1, 100)):
            ttk.Label(sf, text=lab).pack(side='left', padx=(6, 2))
            ttk.Spinbox(sf, from_=lo, to=hi, textvariable=var, width=4).pack(side='left')
        of.columnconfigure(3, weight=1)
        bar = ttk.Frame(right)
        bar.pack(fill='x', pady=8)
        self.go = ttk.Button(bar, text='Port into current mod', command=self.port, state='disabled')
        self.go.pack(side='left')
        self.icon_btn = ttk.Button(bar, text='Make icons…', command=self.icons, state='disabled')
        self.icon_btn.pack(side='left', padx=6)
        self.status = tk.StringVar()
        ttk.Label(bar, textvariable=self.status).pack(side='left', padx=6)
        self.report = tk.Text(right, height=12, wrap='word', borderwidth=0, font=('Segoe UI', 9))
        self.report.pack(fill='both', expand=True)
        try:
            st = app.style
            self.report.configure(background=st.lookup('Treeview', 'fieldbackground') or 'white',
                                  foreground=st.lookup('Treeview', 'foreground') or 'black')
        except Exception:  # noqa: BLE001
            pass
        w.bind('<Escape>', lambda e: w.destroy())
        self.sel = None
        if self.sv_var.get():
            self.load()

    # ---- loading ----
    def browse(self):
        from tkinter import filedialog
        p = filedialog.askdirectory(parent=self.win, title='Scarlet/Violet dump (romfs or the folder with pokemon)')
        if p:
            self.sv_var.set(p)
            self.load()

    def pick_cry(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(parent=self.win, title='Sound for the cry',
                                       filetypes=[('Audio', '*.wav *.mp3 *.ogg *.flac *.m4a'), ('All', '*')])
        if p:
            self.cry_file.set(p)
            self.cry_mode.set('file')

    def load(self):
        sv = find_sv(self.sv_var.get().strip())
        if not sv:
            self.title.set('No Scarlet/Violet Pokémon data there (looked for pokemon/data and its resource table).')
            return
        self.sv = sv
        self.app.settings['sv_dump'] = self.sv_var.get().strip()
        try:
            self.app.save_settings()
        except Exception:  # noqa: BLE001
            pass
        self.title.set('Reading the dump…')
        mod = self.app.current_mod(ask=False)

        def job():
            c = candidates(self.root, sv, mod)
            zones = wild_zones(self.root)
            dex = dex_entries(self.root, mod)
            nm = names(self.root).species
            return c, zones, [('%d %s' % (e['species'], nm.get(e['species'], ''))) for e in dex]

        def done(r):
            self.cands, zones, dex = r
            self.zones = zones
            self.zone_box.configure(values=['none'] + ['%s - %s' % (n, z) for z, n in zones])
            self.dex_box.configure(values=dex)
            self.fill()
            self.title.set('%d Pokémon in the dump have a free slot in Z-A. Pick one.' % sum(c['free'] for c in self.cands))
        self.app.run_bg('Reading the Scarlet/Violet dump', job, done)

    def fill(self):
        self.lst.delete(*self.lst.get_children())
        q = self.q.get().strip().lower()
        for c in self.cands:
            if self.only_free.get() and not c['free']:
                continue
            if q and q not in c['name'].lower() and q != str(c['species']):
                continue
            self.lst.insert('', 'end', str(c['species']), values=('#%d %s' % (c['species'], c['name']), c['reason']))

    def select(self):
        sel = self.lst.selection()
        if not sel:
            return
        c = next(x for x in self.cands if str(x['species']) == sel[0])
        self.sel = c
        self.title.set('#%d %s - %s' % (c['species'], c['name'], c['reason']))
        self.go.configure(state='disabled')
        self.host_box.configure(values=[])
        self.host_var.set('')
        self.host_note.set('Finding Z-A Pokémon that move like it… (the first time reads every Pokémon: about 30 s)')
        sv = self.sv

        def prog(k, n):
            if k % 40 == 0:
                self.app.q.put(lambda: self.host_note.set('Reading Z-A Pokémon %d / %d…' % (k, n)))

        def job():
            d = os.path.join(sv, 'data', c['folder'][:6], c['folder'])
            return hosts(self.root, d, c['folder'], c['height'], progress=prog)

        def done(hs):
            if self.sel is not c:
                return
            self.host_list = hs
            labels = []
            for h in hs:
                labels.append('%s  -  score %.0f, moves alike %d%%, skeleton alike %d%%%s%s' % (
                    h['name'], h['score'], h['gait'] * 100, h['similarity'] * 100,
                    ', tested in game' if h['proven'] else '',
                    ' - needs bones it lacks: ' + ', '.join(h['missing'][:4]) if h['missing'] else ''))
            self.host_box.configure(values=labels)
            if labels:
                self.host_box.current(0)
            self.host_note.set('The best match is picked. A Pokémon that moves differently (hops, floats, flies) '
                               'makes it move like that too.')
            self.go.configure(state='normal' if c['free'] and hs and not hs[0]['missing'] else 'disabled')
        self.app.run_bg('Finding a matching Z-A Pokémon', job, done)

    # ---- porting ----
    def options(self):
        opts = {'behaviour': self.behaviour.get(), 'female': self.female.get(), 'dex': self.dex_mode.get()}
        if self.cry_mode.get() == 'file' and self.cry_file.get().strip():
            opts['cry_audio'] = self.cry_file.get().strip()
        if opts['dex'] == 'replace':
            try:
                opts['dex_other'] = int(self.dex_other.get().split()[0])
            except (ValueError, IndexError):
                raise PortError('Pick the Pokémon whose Pokédex entry to take over.')
        z = self.zone_var.get()
        if z and z != 'none':
            zid = next((zid for zid, n in self.zones if z.startswith(n + ' - ') or z.endswith(zid)), None)
            if zid is None:
                raise PortError('Pick a wild zone from the list.')
            opts['spawn_zone'] = zid
            try:
                count, a, b = self.count.get(), self.lv0.get(), self.lv1.get()
            except Exception:  # noqa: BLE001  (TclError: not a whole number)
                raise PortError('Spawn count and levels must be whole numbers.')
            opts['spawn_count'] = max(1, min(10, count))
            opts['spawn_levels'] = (max(1, min(a, b)), min(100, max(a, b)))
        return opts

    def port(self):
        from tkinter import messagebox
        mod = self.app.current_mod()
        if not mod or not self.sel:
            return
        try:
            opts = self.options()
        except PortError as e:
            messagebox.showerror('Port a Pokémon', str(e), parent=self.win)
            return
        if not getattr(self, 'host_list', None) or self.host_box.current() < 0:
            messagebox.showerror('Port a Pokémon', 'Pick the Pokémon whose animation system to use.', parent=self.win)
            return
        host = self.host_list[self.host_box.current()]
        c, sv = self.sel, self.sv
        self.report.delete('1.0', 'end')
        lines = []

        def log(s):
            lines.append(s)
            self.app.q.put(lambda: (self.report.insert('end', s + '\n'), self.report.see('end')))

        def job():
            return port(self.root, sv, mod, c['species'], host, opts, log=log)

        def done(rep):
            self.app.mod_changed(mod, '', 'ported #%d %s' % (c['species'], c['name']))
            out = ['', 'Ported #%d %s into "%s" (animation system from %s).' % (c['species'], c['name'], mod.name,
                                                                             host['name'])]
            out += ['  ' + x for x in rep.get('personal', [])]
            out.append('  alpha move: %s   cry: %s' % (names(self.root).move(rep.get('alpha_move')), rep.get('cry')))
            if rep.get('dex'):
                out.append('  Pokédex number %s' % rep['dex'])
            if rep.get('spawns'):
                out.append('  spawn points: ' + ', '.join(rep['spawns']))
            out.append('  materials: ' + '; '.join(sorted(set(rep['model']['materials']))))
            out.append('  animation slots filled with stand-ins: %d' % len(rep['model']['stand_ins']))
            out.append('')
            out.append('Check:')
            for ok, text in rep['check']:
                out.append('  %s %s' % ('✓' if ok else '✗', text))
            if not all(ok for ok, _ in rep['check']):
                out.append('Icons are made next: Make icons….')
            self.report.insert('end', '\n'.join(out) + '\n')
            self.report.see('end')
            self.icon_btn.configure(state='normal')
            self.ported = c
            self.icons()
        self.app.run_bg('Porting #%d %s' % (c['species'], c['name']), job, done)

    def icons(self):
        mod = self.app.current_mod()
        c = getattr(self, 'ported', None) or self.sel
        if mod and c:
            IconPicker(self.app, mod, c['species'], c['name'])


def _purge_old_icon_temp(hours=6):
    """Remove icon temp folders left by earlier sessions (glb models and renders, several MB each)."""
    import shutil
    import tempfile
    import time
    base = tempfile.gettempdir()
    try:
        names = [n for n in os.listdir(base) if n.startswith('icons_')]
    except OSError:
        return
    for n in names:
        p = os.path.join(base, n)
        try:
            if os.path.isdir(p) and time.time() - os.path.getmtime(p) > hours * 3600:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


class IconPicker:
    """Pick a pose for a ported Pokémon's icons, rendered in Z-A's style; writes the normal and shiny icons."""

    def __init__(self, app, mod, species, name):
        import tkinter as tk
        from tkinter import ttk
        self.app, self.mod, self.species = app, mod, species
        self.root = app.root_var.get()
        self.stem = 'pm%04d_00_00' % species
        self.rel = 'ik_pokemon/data/pm%04d/%s' % (species, self.stem)
        w = self.win = tk.Toplevel(app)
        w.title('Icons for %s' % name)
        w.geometry('900x700')
        bg = ttk.Style(w).lookup('TFrame', 'background') or None
        if bg:
            w.configure(background=bg)
        ttk.Label(w, padding=(10, 10, 10, 0), wraplength=860, justify='left', text=(
            'Pick a pose for %s\'s icons, then "Edit this pose…" to fine-tune it - turn it, zoom, move it in the frame '
            'and choose the exact moment of the animation - or "Use as is". Icons are rendered from its model in Z-A\'s '
            'icon style; the shiny icon uses the same pose.' % name)).pack(fill='x')
        zf = ttk.Frame(w, padding=(10, 4))
        zf.pack(fill='x')
        ttk.Label(zf, text='Framing for "Use as is":').pack(side='left')
        self.zoom = tk.StringVar(value='Close')
        for z in ZOOMS:
            ttk.Radiobutton(zf, text=z, value=z, variable=self.zoom).pack(side='left', padx=4)
        self.status = tk.StringVar(value='Rendering poses…')
        ttk.Label(zf, textvariable=self.status).pack(side='left', padx=12)
        self.grid = ttk.Frame(w, padding=10)
        self.grid.pack(fill='both', expand=True)
        bar = ttk.Frame(w, padding=(10, 0, 10, 10))
        bar.pack(fill='x')
        self.use = ttk.Button(bar, text='Use as is', command=self.make, state='disabled')
        self.use.pack(side='right')
        self.edit_btn = ttk.Button(bar, text='Edit this pose…', command=self.edit, state='disabled')
        self.edit_btn.pack(side='right', padx=6)
        self.preview = ttk.Label(bar)
        self.preview.pack(side='left')
        self.choice = None
        self.thumbs = []
        import tempfile
        _purge_old_icon_temp()
        self.tmp = tempfile.mkdtemp(prefix='icons_')
        self.busy, self.closed = 0, False
        w.protocol('WM_DELETE_WINDOW', self.close)
        w.bind('<Escape>', lambda e: self.close())
        self.render_sheet()

    def bg(self, label, fn, done):
        """run_bg, keeping the temp folder until the last job using it has finished."""
        import shutil
        self.busy += 1

        def work():
            try:
                return fn()
            finally:
                self.busy -= 1
                if self.closed and not self.busy:
                    shutil.rmtree(self.tmp, ignore_errors=True)
        self.app.run_bg(label, work, done)

    def close(self):
        import shutil
        self.closed = True
        self.win.destroy()
        if not self.busy:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def render_sheet(self):
        import za_iconrender as ir
        mdl = self.mod.path_for(self.rel + '/' + self.stem + '.trmdl')
        rare = self.mod.path_for(self.rel + '/' + self.stem + '_rare.trmtr')

        def job():
            models, anims = ir.export_models(mdl, self.mod.romfs, self.root, self.tmp,
                                             rare_path=rare if os.path.isfile(rare) else None)
            self.models, self.anims = models, anims
            excl = self.effect_materials()
            shots = [{'name': 'p_%d_%d' % (ci, int(t * 100)), 'model': 'normal', 'clip': ci, 't': t, 'size': 220,
                      'margin': 0.8, 'up': 0.14} for ci in range(min(len(anims), 7)) for t in (0.35, 0.6, 0.85)]
            self.exclude = excl
            return ir.render({'models': models, 'exclude': excl, 'shots': shots}, self.tmp), shots

        self.bg('Rendering icon poses', job, self.show_sheet)

    def effect_materials(self):
        """Materials left out of the framing (flames and other unlit effects)."""
        out = []
        for f in os.listdir(self.mod.path_for(self.rel)):
            if f.endswith('.trmtr') and not f.endswith('_rare.trmtr'):
                for m in json.loads(zm.to_json(self.mod.path_for(self.rel + '/' + f), TRMTR))['Materials']:
                    if m['Shader'][0]['Name'] in ('Unlit', 'Transparent', 'IkCharacterTransparent', 'FresnelEffect'):
                        out.append(re.escape(m['Name']))
        return '^(' + '|'.join(sorted(set(out))) + ')$' if out else ''

    def show_sheet(self, r):
        from PIL import Image, ImageTk
        from tkinter import ttk
        files, shots = r
        for wdg in self.grid.winfo_children():
            wdg.destroy()
        self.thumbs = []
        for i, s in enumerate(shots):
            p = files.get(s['name'])
            if not p:
                continue
            im = ImageTk.PhotoImage(Image.open(p).convert('RGBA').resize((110, 110), Image.LANCZOS))
            self.thumbs.append(im)
            b = ttk.Button(self.grid, image=im, command=lambda s=s: self.pick(s))
            b.grid(row=i // 7, column=i % 7, padx=2, pady=2)
        self.status.set('%d poses - click one.' % len(self.thumbs) if self.thumbs else
                        'Nothing was rendered (is WebView2 installed?).')

    def pick(self, s):
        self.choice = s
        self.use.configure(state='normal')
        self.edit_btn.configure(state='normal')
        self.status.set('Selected: %s at %d%%.' % (self.anims[s['clip']], int(s['t'] * 100)))

    def edit(self):
        import za_iconrender as ir
        s = self.choice or {'clip': 0, 't': 0.5}
        margin, up = ZOOMS[self.zoom.get()]
        job = {'models': self.models, 'exclude': self.exclude, 'anims': self.anims, 'title': self.win.title(),
               'start': {'clip': s['clip'], 't': s['t'], 'margin': margin, 'up': up, 'yaw': 55, 'pitch': 10}}
        self.status.set('Editing in the icon editor window…')

        def job_fn():
            res = ir.edit(job, self.tmp)
            n = res.get('final_normal')
            if not n:
                return None
            write_icons(self.root, self.mod, self.rel, self.stem, n, res.get('final_shiny') or n)
            return n, res.get('final_shiny') or n

        def done(r):
            if not r:
                self.status.set('Icon editor closed without changes.')
                return
            self.show_result(r)
        self.bg('Icon editor', job_fn, done)

    def show_result(self, r):
        from PIL import Image, ImageTk
        self.app.mod_changed(self.mod, '', 'icons for #%d' % self.species)   # first: the window may be closed
        if self.closed:
            return
        ims = [Image.open(p).convert('RGBA').resize((96, 96), Image.LANCZOS) for p in r]
        sheet = Image.new('RGBA', (200, 96), (0, 0, 0, 0))
        for i, im in enumerate(ims):
            sheet.alpha_composite(im, (i * 104, 0))
        self.prev_img = ImageTk.PhotoImage(sheet)
        self.preview.configure(image=self.prev_img)
        self.status.set('Icons written (normal and shiny) to the mod.')

    def make(self):
        import za_iconrender as ir
        s = self.choice
        margin, up = ZOOMS[self.zoom.get()]
        shots = [dict(s, name='final_' + k, model=k, size=640, margin=margin, up=up) for k in self.models]

        def job():
            res = ir.render({'models': self.models, 'exclude': self.exclude, 'shots': shots}, self.tmp)
            n = res.get('final_normal')
            sh = res.get('final_shiny') or n
            if not n:
                raise PortError('The icon could not be rendered.')
            write_icons(self.root, self.mod, self.rel, self.stem, n, sh)
            return n, sh

        self.bg('Rendering the icons', job, self.show_result)


def open_window(app):
    w = getattr(app, 'port_win', None)
    try:
        if w is not None and w.win.winfo_exists() and w.root != app.root_var.get():
            w.win.destroy()                 # the game folder changed: start over
        elif w is not None and w.win.winfo_exists():
            w.win.lift()
            return w
    except Exception:  # noqa: BLE001
        pass
    app.port_win = PortWindow(app)
    return app.port_win
