"""What a new NPC says and gives (Create Spawn Point): a talk trigger, data only - no event script.

Z-A's event triggers (ik_event/bin/trigger/**/trigger_*.bin, layout in tools/schemas/za/za_trigger.fbs) say what
happens when you talk to an NPC: entries checked in order, the first whose conditions hold runs its commands -
3 = a line of dialogue (speaker, text file, label), 11 = give an item (ITEMID_x, count), 9 = set a work value.
A new NPC's trigger goes into the first side quest's trigger file, which the game keeps loaded (the city's talk
triggers live in the quest files; the global file isn't read for them). Its lines are added to that quest's text
file in every language. "Only the first time" uses one of the quest work counters no part of the game uses
(SYS_WORK_QUEST_230 and the like): 0 before the gift, 1 after.
Proven in game (2026-11-19): a new NPC said its own lines and gave 2 Master Balls with command 11.
"""
import glob
import json
import os
import re

import za_formats as zf
import za_merge
import za_modding as zm

TRIGGER = 'ik_event/bin/trigger/sub/trigger_sub_0001.bin'
ASSETS = 'world/ik_data/field/npc/npc_asset_data/npc_asset_data/npc_asset_data.bin'
NAMES = ('common/namelist', 'common/trname')       # the name list the name tag points into, and the trainer one
TEXT = 'sub_001'                                   # script text file of the first side quest
TEXT_DIR = 'ik_message/dat'
CMD_TALK, CMD_GIVE, CMD_SET_WORK, CMD_POKEMON = 3, 11, 9, 10
# gift Pokémon: a row of the game's Pokémon data (as its own gifts: sub_addpoke_*) handed over with command 10
POKEDATA = 'world/ik_data/field/pokemon/pokemon_data/pokemon_data/pokemon_data_array.bin'
PLACES = [('Lumiose City', 'MAPNAME_LUMIOSE'), ('a distant land', 'MAPNAME_TOOKUHANARETATOTI'),
          ('the Pokémon Research Lab', 'MAPNAME_Lab'), ('the Lumiose Museum', 'MAPNAME_Museum'),
          ('Hyperspace Lumiose', 'MAPNAME_DIM'), ('the Kalos region', 'MAPNAME_KAROSU')]


def gift_row(gid, species, form=0, level=5, perfect=False, moves=()):
    """A gift Pokémon's row: species, form, level, all IVs 31 if perfect, up to 4 move numbers (0 = the moves it
    knows at that level). The ball is the game's usual one: gifts have no ball field."""
    iv = 31 if perfect else -1
    row = {'id': gid, 'devNo': int(species), 'minLevel': int(level), 'maxLevel': int(level), 'sex': -1,
           'formNo': int(form), 'rare': 536870911, 'tokusei': 255, 'seikaku': -1, 'talentScale': 128,
           'talentVNum': 6 if perfect else 0, 'oyabunProbability': 0.0, 'oyabunAdditionalLevel': 0,
           'talentValue': {k: iv for k in ('hp', 'atk', 'def', 'spAtk', 'spDef', 'agi')}}
    moves = [int(m) for m in moves if m][:4]
    if moves:
        row['wazaList'] = {'waza%d' % (i + 1): (moves[i] if i < len(moves) else 0) for i in range(4)}
    return row
# The name over a text box comes from the line itself: the game ends a spoken line with [VAR 0114(n)], n (hex) = the
# speaker's place in the name list (common/namelist). Townsfolk lines have none (no name shown).
NAME_TAG = '[VAR 0114(%X)]'
SCHEMA = os.path.join(zm.SCHEMAS, 'za', 'za_trigger.fbs')
_cache = {}


class TalkError(Exception):
    pass


# ---- the game's item names as triggers use them ------------------------------------------------------------------

_ROWS = ('a i u e o|ka ki ku ke ko|sa si su se so|ta ti tu te to|na ni nu ne no|ha hi hu he ho|ma mi mu me mo|'
         'ya - yu - yo|ra ri ru re ro|wa - - - wo|ga gi gu ge go|za zi zu ze zo|da di du de do|ba bi bu be bo|'
         'pa pi pu pe po')
_KATA = ('アイウエオ|カキクケコ|サシスセソ|タチツテト|ナニヌネノ|ハヒフヘホ|マミムメモ|ヤ_ユ_ヨ|ラリルレロ|ワ___ヲ|ガギグゲゴ|'
         'ザジズゼゾ|ダヂヅデド|バビブベボ|パピプペポ')
KANA = {'ン': 'N', 'ん': 'N', 'ヴ': 'VU'}
for _rs, _ks in zip(_ROWS.split('|'), _KATA.split('|')):
    for _r, _k in zip(_rs.split(), _ks):
        if _k != '_' and _r != '-':
            KANA[_k] = KANA[chr(ord(_k) - 0x60)] = _r.upper()
SMALL = {'ャ': 'YA', 'ュ': 'YU', 'ョ': 'YO', 'ゃ': 'YA', 'ゅ': 'YU', 'ょ': 'YO', 'ァ': 'A', 'ィ': 'I', 'ゥ': 'U',
         'ェ': 'E', 'ォ': 'O', 'ぁ': 'A', 'ぃ': 'I', 'ぅ': 'U', 'ぇ': 'E', 'ぉ': 'O'}


def _romaji(s):
    out = ''
    for i, c in enumerate(s):
        if c in SMALL and out:
            out = out[:-1] + SMALL[c]
        elif c in ('ッ', 'っ'):
            out += KANA.get(s[i + 1], '')[:1] if i + 1 < len(s) else ''
        elif c == 'ー':
            out += out[-1:] if out[-1:] in ('A', 'I', 'U', 'E', 'O') else ''
        elif c in KANA:
            out += KANA[c]
        elif c.isascii() and c.isalnum():
            out += c.upper()
    return out


def _norm(s):
    s = s.upper()
    for a, b in (('SHI', 'SI'), ('CHI', 'TI'), ('TSU', 'TU'), ('FU', 'HU'), ('JI', 'ZI'), ('SHA', 'SYA'),
                 ('SHU', 'SYU'), ('SHO', 'SYO'), ('JYA', 'ZYA'), ('JYU', 'ZYU'), ('JYO', 'ZYO'), ('JA', 'ZYA'),
                 ('JU', 'ZYU'), ('JO', 'ZYO'), ('CHA', 'TYA'), ('CHU', 'TYU'), ('CHO', 'TYO'), ('OU', 'OO')):
        s = s.replace(a, b)
    return s


def _game_words(root):
    """Every ITEMID_ / work name the game's event data and scripts use."""
    if ('words', root) not in _cache:
        found = set()
        files = glob.glob(os.path.join(root, 'ik_event', 'bin', '**', '*.bin'), recursive=True)
        files += glob.glob(os.path.join(root, 'script', 'lua', 'bin', 'release', '*', '*.blua'))
        for f in files:
            if os.sep + 'flag' + os.sep in f:
                continue
            with open(f, 'rb') as fh:
                found |= {m.decode() for m in re.findall(rb'(?:ITEMID|SYS_WORK)_[A-Za-z0-9_]+', fh.read())}
        _cache[('words', root)] = found
    return _cache[('words', root)]


def item_ids(root):
    """{item number: 'ITEMID_x'} for the items whose trigger name is certain: every ITEMID_ the game itself uses,
    matched to the Japanese item names (the ITEMID_ names are their romaji)."""
    if ('items', root) not in _cache:
        base = os.path.join(root, TEXT_DIR, 'JPN', 'common', 'itemname')
        out = {}
        try:
            names = zf.decode_msg(open(base + '.dat', 'rb').read())
        except OSError:
            names = []
        by = {}
        for i, n in enumerate(names):
            if n:
                by.setdefault(_norm(_romaji(n)), i)
        for w in sorted(_game_words(root)):
            if w.startswith('ITEMID_'):
                i = by.get(_norm(w[len('ITEMID_'):]))
                if i is not None:
                    out.setdefault(i, w)
        _cache[('items', root)] = out
    return _cache[('items', root)]


# ---- spare quest work counters -------------------------------------------------------------------------------

def _spare_works(root):
    if ('works', root) not in _cache:
        p = os.path.join(root, 'ik_event', 'bin', 'flag', 'quest_work.bin')
        try:
            names = [m.decode() for m in re.findall(rb'SYS_WORK_QUEST_\d+', open(p, 'rb').read())]
        except OSError:
            names = []
        used = _game_words(root)
        _cache[('works', root)] = sorted((n for n in names if n not in used),
                                         key=lambda n: -int(n.rsplit('_', 1)[1]))
    return _cache[('works', root)]


def _works_in(data):
    return set(re.findall(r'SYS_WORK_QUEST_\d+', data.decode('latin-1')))


def free_work(root, pending=()):
    """A spare quest work counter no mod in the mods folder (nor a pending write) uses yet."""
    taken = set()
    for m in zm.list_mods():
        for rel in m.files():
            if re.search(r'/trigger/.*trigger_[^/]+\.bin$', rel):
                try:
                    with open(m.path_for(rel), 'rb') as f:
                        taken |= _works_in(f.read())
                except OSError:
                    pass
    for data in pending:
        taken |= _works_in(data)
    for w in _spare_works(root):
        if w not in taken:
            return w
    raise TalkError('No spare counter left for "only the first time" - give NPCs their item every time, or remove '
                    'one-time gifts from other mods.')


# ---- text and trigger ------------------------------------------------------------------------------------------

def boxes(text, end=''):
    """The lines typed in the menu -> one dialogue box per paragraph (a blank line starts a new box), each ending in
    `end` (the speaker's name tag, or nothing)."""
    out = []
    for para in re.split(r'\n\s*\n', (text or '').strip()):
        rows = [r.strip() for r in para.splitlines() if r.strip()]
        if rows:
            out.append('\\n'.join(rows) + end)
    return out


def _asset_name(root, romfs, pending, asset):
    """The name label of NPC look `asset` (None when it has none)."""
    import tempfile
    data = _read(ASSETS, root, romfs, pending)
    if not asset or data is None:
        return None
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, 'assets.bin')
        with open(src, 'wb') as f:
            f.write(data)
        rows = json.loads(zm.to_json(src, zm.schema_for(os.path.join(root, *ASSETS.split('/')))))['root']
    row = next((r for r in rows if r.get('assetId') == asset), None)
    return next((i.get('name') for i in (row or {}).get('npcInfoList') or [] if i.get('name')), None)


def _name_tag(root, romfs, pending, lang, label):
    """The [VAR 0114(n)] ending that shows name label `label` over the text box, in language `lang` ('' if none)."""
    if not label:
        return ''
    tbl = _read('%s/%s/%s.tbl' % (TEXT_DIR, lang, NAMES[0]), root, romfs, pending)
    labels = [lab for _, lab in za_merge.tbl_entries(tbl)] if tbl else []
    return NAME_TAG % labels.index(label) if label in labels else ''


def _read(rel, root, romfs, pending):
    if rel in pending:
        return pending[rel]
    for base in [b for b in (romfs, root) if b]:
        p = os.path.join(base, *rel.split('/'))
        if os.path.isfile(p):
            with open(p, 'rb') as f:
                return f.read()
    return None


def _languages(root):
    base = os.path.join(root, *TEXT_DIR.split('/'))
    return sorted(d for d in os.listdir(base)
                  if os.path.isfile(os.path.join(base, d, 'script', TEXT + '.dat'))) if os.path.isdir(base) else []


def write_talk(root, romfs, pending, npc, say, item=None, count=1, once=False, after='', pokemon=None,
               place='MAPNAME_LUMIOSE', asset=None, textfile=TEXT, trigger=TRIGGER):
    """Files {rel: bytes} giving NPC `npc` (its placement id) these lines (and item), stacked on `pending` (files
    already waiting to be saved). item = item number or None; once = the lines + item only the first time, then
    `after` (or the same lines without the item). pokemon = gift_row() keywords (species, form, level, perfect,
    moves) to also give a Pokémon, met at `place` (a MAPNAME_ label). asset = the NPC's look: its name (write_name's,
    or the game's own, e.g. 'Old Man') is shown over the text boxes; a look without one shows no name. textfile =
    the script text file the lines go in (made when the game has none)."""
    import za_newitem
    first = boxes(say)
    later = boxes(after) or first
    if not first:
        return {}
    speaker = _asset_name(root, romfs, pending, asset)
    ids = item_ids(root)
    if item is not None and item not in ids:
        raise TalkError('That item can\'t be given by an NPC (its name in the game\'s event data isn\'t known).')
    out = {}
    # 1. the lines, in every language (the same text everywhere)
    tag = re.sub(r'[^a-z0-9_]', '_', npc.lower())
    labels_first = ['msg_lt_%s_%02d' % (tag, i + 1) for i in range(len(first))]
    labels_later = ['msg_lt_%s_%02d' % (tag, 51 + i) for i in range(len(later))] if (once and later is not first) \
        else labels_first
    langs = _languages(root)
    if not langs:
        raise TalkError('The game\'s text files were not found.')
    for lang in langs:
        rel = '%s/%s/script/%s' % (TEXT_DIR, lang, textfile)
        dat, tbl = _read(rel + '.dat', root, romfs, pending), _read(rel + '.tbl', root, romfs, pending)
        if dat is None or tbl is None:
            dat, tbl = za_merge.msg_build([]), za_merge.tbl_build([])
        have = set(zf.parse_tbl(tbl))
        end = _name_tag(root, romfs, pending, lang, speaker)
        lines = list(zip(labels_first, boxes(say, end)))
        if labels_later is not labels_first:
            lines += list(zip(labels_later, boxes(after, end)))
        for label, text in lines:
            if label in have:
                raise TalkError('This NPC already has lines in your mod.')
            dat, tbl = za_newitem.add_name(dat, tbl, label, text)
        out[rel + '.dat'], out[rel + '.tbl'] = dat, tbl
    # 2. the trigger
    data = _read(trigger, root, romfs, pending)
    if data is None:
        raise TalkError('The game\'s trigger file %s was not found.' % trigger)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, 'in.bin')
        with open(src, 'wb') as f:
            f.write(data)
        d = json.loads(zm.to_json(src, SCHEMA))
        d['Triggers'] = [t for t in d.get('Triggers') or []
                         if npc not in ((t.get('Item') or {}).get('Target') or [])]
        talk = [{'Type': CMD_TALK, 'Params': [npc, textfile, lab, '1']} for lab in labels_first]
        gift = []
        if pokemon:
            gid = 'addpoke_lt_' + tag
            game_table = os.path.join(root, *POKEDATA.split('/'))
            schema = zm.schema_for(game_table)
            src = os.path.join(td, 'pokedata.bin')
            with open(src, 'wb') as f:
                f.write(_read(POKEDATA, root, romfs, pending))
            pd = json.loads(zm.to_json(src, schema))
            groups = pd.get('values') or []
            for g in groups:
                if 'root' in g:
                    g['root'] = [r for r in g['root'] if r.get('id') != gid]
            (groups[0].setdefault('root', []) if groups and 'root' in groups[0] else groups).append(
                gift_row(gid, **pokemon))
            dst = os.path.join(td, 'pokedata_out.bin')
            zm.from_json(json.dumps(pd), schema, dst)
            with open(dst, 'rb') as f:
                out[POKEDATA] = f.read()
            gift.append({'Type': CMD_POKEMON, 'Params': [gid, 'NONE', 'NONE', 'NONE', place]})
        if item is not None:
            gift.append({'Type': CMD_GIVE, 'Params': [ids[item], str(max(1, int(count)))]})
        if once:
            work = free_work(root, [v for k, v in pending.items() if k.endswith('.bin')])
            entries = [
                {'ConditionGroups': [{'Conditions': [{'Name': 'work_condition', 'Op': 1, 'Params': [work, '0']}]}],
                 'CommandGroups': [{'Commands': talk + gift + [{'Type': CMD_SET_WORK, 'Params': [work, '1']}]}]},
                {'ConditionGroups': [{'Conditions': [{'Name': 'force_condition', 'Op': 0}]}],
                 'CommandGroups': [{'Commands': [{'Type': CMD_TALK, 'Params': [npc, textfile, lab, '1']}
                                                 for lab in labels_later]}]}]
        else:
            entries = [{'ConditionGroups': [{'Conditions': [{'Name': 'force_condition', 'Op': 0}]}],
                        'CommandGroups': [{'Commands': talk + gift}]}]
        d.setdefault('Triggers', []).append({'Item': {'Kind': 1, 'Target': [npc], 'Entries': entries}})
        dst = os.path.join(td, 'out.bin')
        zm.from_json(json.dumps(d), SCHEMA, dst)
        with open(dst, 'rb') as f:
            out[trigger] = f.read()
    return out


def write_name(root, romfs, pending, asset, npc, name):
    """A look of its own for NPC `npc` (its placement id): a copy of the game's NPC asset `asset` that carries the
    name shown on its talk prompt (and, through write_talk's name tag, over its text boxes). Returns (the new asset
    id, its name label, {rel: bytes}) - the asset table and the name lists in every language."""
    name = ' '.join((name or '').split())
    if not name:
        return asset, None, {}
    import tempfile
    import za_newitem
    tag = re.sub(r'[^a-z0-9_]', '_', npc.lower())
    new_id, label = npc, 'lt_' + tag
    out = {}
    game = os.path.join(root, *ASSETS.split('/'))
    schema = zm.schema_for(game)
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, 'assets.bin')
        data = _read(ASSETS, root, romfs, pending)
        if data is None:
            raise TalkError('The game\'s NPC look table was not found.')
        with open(src, 'wb') as f:
            f.write(data)
        d = json.loads(zm.to_json(src, schema))
        rows = d['root']
        base = next((r for r in rows if r.get('assetId') == asset), None)
        if base is None:
            raise TalkError('The NPC look %s was not found.' % asset)
        row = json.loads(json.dumps(base))
        row['assetId'] = new_id
        for info in row.get('npcInfoList') or []:
            info['name'] = label
        d['root'] = [r for r in rows if r.get('assetId') != new_id] + [row]
        dst = os.path.join(td, 'assets_out.bin')
        zm.from_json(json.dumps(d), schema, dst)
        with open(dst, 'rb') as f:
            out[ASSETS] = f.read()
    for lang in _languages(root):
        for names in NAMES:
            rel = '%s/%s/%s' % (TEXT_DIR, lang, names)
            dat, tbl = _read(rel + '.dat', root, romfs, pending), _read(rel + '.tbl', root, romfs, pending)
            if dat is None or tbl is None:
                continue
            if label not in set(zf.parse_tbl(tbl)):
                dat, tbl = za_newitem.add_name(dat, tbl, label, name)
            out[rel + '.dat'], out[rel + '.tbl'] = dat, tbl
    return new_id, label, out
