"""New music: add a song to the game's battle music list (the "Battle BGM" setting of online Private Battles), as a
new entry - none of
the game's songs is replaced.

How the game plays a picked song: battle_bgm_select_array lists the entries (a command name such as
VS_SELECT_01_PLAY, a name label, an unlock flag). The command is looked up in bgm_command.bin, whose rows say
which Wwise event to post on which music layer (PLAY_BGM_IK_VS_SELECT_01 on VS_TRAINER). That event plays a
music playlist in BGM.bnk: segments, each with a track that holds the audio.

A new song is all of those, copied from one of the game's battle songs and pointed at the new audio:
  BGM.bnk: event + action + playlist + segment(s) + track(s) (new ids), the audio as 16-bit PCM in the bank
  bgm_command.bin: the game's three rows for that command, renamed
  battle_bgm_select_array.bin: a picker row (unlocked from the start)
  btl_bgm_select (every language): the song's name
"""
import json
import os
import struct

import za_modding as zm
import za_soundbank as sb

COMMANDS = 'world/exl/audio/bgm_command/bgm_command.bin'
PICKER = 'world/ik_data/ui/battle_bgm_select/battle_bgm_select_array.bin'
NAMES = 'ik_message/dat/%s/common/btl_bgm_select'
BANK = 'BGM'
SINGLE_TEMPLATE = 10        # VS_SELECT_10: one segment (the song loops as a whole)
INTRO_TEMPLATE = 1          # VS_SELECT_01: intro segment, then a looping segment
FIRST_NUMBER = 60           # new entries: VS_SELECT_60, 61, ... (the game uses 01-35)
MUSIC_TYPES = (sb.MUSIC_SEGMENT, sb.MUSIC_TRACK, 13)    # 13 = music playlist


class BgmError(Exception):
    pass


def _load(root, mod, rel):
    game = os.path.join(root, *rel.split('/'))
    src = mod.path_for(rel) if os.path.isfile(mod.path_for(rel)) else game
    sch = zm.schema_for(game)
    return json.loads(zm.to_json(src, sch)), sch


def _save(mod, rel, obj, sch):
    out = mod.path_for(rel)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    zm.from_json(json.dumps(obj), sch, out)


def songs(root, mod):
    """The picker's entries: [(number, command, name label)] (game and mod)."""
    d, _ = _load(root, mod, PICKER)
    out = []
    for r in d['values']:
        c = r.get('sound_call_name') or ''
        if c.startswith('VS_SELECT_') and c.endswith('_PLAY'):
            try:
                out.append((int(c[10:-5]), c, r.get('text_label', '')))
            except ValueError:
                pass
    return out


def free_number(root, mod):
    """First entry number no picker (this mod's or another mod's) uses."""
    used = {n for n, _, _ in songs(root, mod)}
    for other in zm.list_mods():
        if os.path.isfile(other.path_for(PICKER)):
            try:
                used |= {n for n, _, _ in songs(root, other)}
            except Exception:  # noqa: BLE001
                pass
    n = FIRST_NUMBER
    while n in used:
        n += 1
    if n > 99:
        raise BgmError('No free battle music numbers left (60-99).')
    return n


# ---- the bank ----------------------------------------------------------------------------------------------

def _refs(b, oid, types):
    """Objects of `types` whose id appears in oid's body, in order of appearance (no repeats)."""
    body = bytes(b.body(oid))
    out = []
    for q in range(len(body) - 3):
        v = struct.unpack_from('<I', body, q)[0]
        if v != oid and v in b.by_id and b.by_id[v][0] in types and v not in out:
            out.append(v)
    return out


def _template(b, number):
    """(action id, playlist id, [(segment id, [track ids])]) of the game's VS_SELECT_<number> song."""
    ev = sb.fnv1('PLAY_BGM_IK_VS_SELECT_%02d' % number)
    if ev not in b.by_id:
        raise BgmError('The game\'s battle song %02d is missing from BGM.bnk.' % number)
    eb = bytes(b.body(ev))
    act = struct.unpack_from('<I', eb, 1)[0]
    at, pl = struct.unpack_from('<HI', bytes(b.body(act)), 0)
    if at != sb.PLAY or pl not in b.by_id or b.by_id[pl][0] != 13:
        raise BgmError('Battle song %02d is not a music playlist.' % number)
    segs = []
    for s in _refs(b, pl, (sb.MUSIC_SEGMENT,)):
        tracks = [o[1] for o in b.objects if o[0] == sb.MUSIC_TRACK and struct.pack('<I', s) in b.body(o[1])]
        segs.append((s, tracks))
    if not segs or any(len(t) != 1 for _, t in segs):
        raise BgmError('Battle song %02d has an unexpected layout.' % number)
    return act, pl, segs


def _new_id(b, name, taken):
    i = sb.fnv1(name)
    while i in b.by_id or i in taken or i == 0:
        i = (i * 16777619 + 1) & 0xFFFFFFFF
    taken.add(i)
    return i


def _add_object(b, t, oid, body):
    h = b.section(b'HIRC')
    struct.pack_into('<I', h, 0, struct.unpack_from('<I', h, 0)[0] + 1)
    h += struct.pack('<BII', t, len(body) + 4, oid) + bytes(body)
    q = len(h) - len(body)
    b.objects.append([t, oid, q, q + len(body)])
    b.by_id[oid] = b.objects[-1]


def _remap(body, mapping):
    """Body with every 4-byte occurrence of an old id replaced by its new id."""
    out = bytearray(body)
    for old, new in mapping.items():
        key, rep = struct.pack('<I', old), struct.pack('<I', new)
        q = out.find(key)
        while q != -1:
            out[q:q + 4] = rep
            q = out.find(key, q + 4)
    return out


def add_to_bank(b, number, wem, ms, loop_ms):
    """Add the event PLAY_BGM_IK_VS_SELECT_<number> and its music to bank b. loop_ms: (start, end) in ms."""
    start, end = loop_ms
    intro = start > 1.0                    # a loop that doesn't start at 0: intro segment + loop segment
    act, pl, segs = _template(b, INTRO_TEMPLATE if intro else SINGLE_TEMPLATE)
    if intro and len(segs) != 2:
        raise BgmError('The intro + loop template song has an unexpected layout.')
    tag = 'LUMI_BGM_VS_SELECT_%02d' % number
    taken = set()
    event = sb.fnv1('PLAY_BGM_IK_VS_SELECT_%02d' % number)
    if event in b.by_id:
        raise BgmError('Battle song %02d already exists in BGM.bnk.' % number)
    media = sb.fnv1(tag + '_MEDIA')
    while media in {m for m, _, _ in b.media()}:
        media = (media + 1) & 0xFFFFFFFF
    mapping = {act: _new_id(b, tag + '_ACTION', taken), pl: _new_id(b, tag + '_PLAYLIST', taken)}
    for k, (s, tracks) in enumerate(segs):
        mapping[s] = _new_id(b, '%s_SEGMENT_%d' % (tag, k), taken)
        mapping[tracks[0]] = _new_id(b, '%s_TRACK_%d' % (tag, k), taken)
        for src in b.sources(tracks[0]):
            mapping[src[3]] = media                  # the track's audio (in its sources and playlist items)
    # children first: tracks, segments, playlist, action, event
    for k, (s, tracks) in enumerate(segs):
        t = tracks[0]
        _add_object(b, sb.MUSIC_TRACK, mapping[t], _remap(b.body(t), mapping))
        nt = mapping[t]
        for q, plugin, st, mid, size in b.sources(nt):
            b.patch(nt, q, '<IBII', sb.PCM, 0, media, len(wem))
        for off, mid, at, bt, et, dur in b.track_items(nt):
            b.patch(nt, off, '<dddd', 0.0, 0.0, 0.0, ms)
    for k, (s, tracks) in enumerate(segs):
        _add_object(b, sb.MUSIC_SEGMENT, mapping[s], _remap(b.body(s), mapping))
        ns = mapping[s]
        r = b.segment(ns)
        if r is None:
            raise BgmError('Could not read a music segment of the template song.')
        dur_off, _, marks = r
        entry = 0.0 if (intro and k == 0) else start
        b.patch(ns, dur_off, '<d', ms)
        for mk, pos, off in marks:
            if mk == sb.ENTRY_CUE:
                b.patch(ns, off, '<d', entry)
            elif mk == sb.EXIT_CUE:
                b.patch(ns, off, '<d', end)
            elif pos > ms:
                b.patch(ns, off, '<d', ms)
    _add_object(b, 13, mapping[pl], _remap(b.body(pl), mapping))
    _add_object(b, sb.ACTION, mapping[act], _remap(b.body(act), mapping))
    _add_object(b, sb.EVENT, event, bytes([1]) + struct.pack('<I', mapping[act]))
    b.set_media(media, wem)
    return event, media


# ---- tables and text ---------------------------------------------------------------------------------------

# the music event every trainer battle starts (Battle Zones, trainers who challenge you, story trainers)
TRAINER_EVENT = 'PLAY_BGM_VS_TRAINER'


def _use_for_trainers(root, mod, number):
    """Trainer battles play song `number` instead of the normal trainer battle theme: the commands that start
    the theme are pointed at the song's event (the game's own song stays in the bank). Returns how many."""
    d, sch = _load(root, mod, COMMANDS)
    n = 0
    for r in d['commands']:
        if r.get('event') == TRAINER_EVENT or (r.get('name') in TRAINER_COMMANDS and
                                               str(r.get('event', '')).startswith('PLAY_BGM_IK_VS_SELECT_')):
            r['event'] = 'PLAY_BGM_IK_VS_SELECT_%02d' % number
            n += 1
    if not n:
        raise BgmError('The game\'s trainer battle music commands were not found.')
    _save(mod, COMMANDS, d, sch)
    return n


TRAINER_COMMANDS = ('VS_TRAINER_INTERACT', 'TL_TRAINER_START', 'TL_TRAINER_ZA_START')


def _add_commands(root, mod, number):
    d, sch = _load(root, mod, COMMANDS)
    rows = d['commands']
    tpl = 'VS_SELECT_%02d_PLAY' % SINGLE_TEMPLATE
    name = 'VS_SELECT_%02d_PLAY' % number
    rows[:] = [r for r in rows if r.get('name') != name]
    new = []
    for r in rows:
        if r.get('name') == tpl:
            c = dict(r, name=name)
            if c.get('event') == 'PLAY_BGM_IK_VS_SELECT_%02d' % SINGLE_TEMPLATE:
                c['event'] = 'PLAY_BGM_IK_VS_SELECT_%02d' % number
            new.append(c)
    if not new:
        raise BgmError('The game\'s music commands for battle song %02d are missing.' % SINGLE_TEMPLATE)
    last = max(i for i, r in enumerate(rows) if r.get('name') == tpl)
    rows[last + 1:last + 1] = new
    _save(mod, COMMANDS, d, sch)


def _add_picker(root, mod, number):
    d, sch = _load(root, mod, PICKER)
    rows = d['values']
    cmd = 'VS_SELECT_%02d_PLAY' % number
    rows[:] = [r for r in rows if r.get('sound_call_name') != cmd]
    sort = max([r.get('sort_num', 0) for r in rows if r.get('sort_num', 0) < 99] + [0]) + 1
    tpl = next((r for r in rows if r.get('sound_call_name') == 'VS_SELECT_01_PLAY'), None) or {}
    row = dict(tpl, sound_call_name=cmd, text_label='bgm_vs_select_%02d' % number, sort_num=sort,
               is_initial_release=True, release_flag_name='')
    i = next((i for i, r in enumerate(rows) if r.get('sort_num', 0) >= 99), len(rows))   # before "Random"
    rows.insert(i, row)
    _save(mod, PICKER, d, sch)


def _add_name(root, mod, number, title):
    import za_newitem
    label = 'bgm_vs_select_%02d' % number
    langs = sorted(x for x in os.listdir(os.path.join(root, 'ik_message', 'dat'))
                   if os.path.isdir(os.path.join(root, 'ik_message', 'dat', x)))
    n = 0
    for lang in langs:
        base = NAMES % lang
        files = {}
        for ext in ('.dat', '.tbl'):
            p = mod.path_for(base + ext)
            if not os.path.isfile(p):
                p = os.path.join(root, *(base + ext).split('/'))
            if not os.path.isfile(p):
                break
            with open(p, 'rb') as f:
                files[ext] = f.read()
        if len(files) != 2:
            continue
        import za_merge
        if any(lab == label for _, lab in za_merge.tbl_entries(files['.tbl'])):
            continue
        dat, tbl = za_newitem.add_name(files['.dat'], files['.tbl'], label, title)
        mod.add_bytes(base + '.dat', dat)
        mod.add_bytes(base + '.tbl', tbl)
        n += 1
    return n


def add_battle_song(root, mod, audio_path, title, loop=None, gain_db=0.0, log=None, trainer=False):
    """Add a new battle song to the picker in `mod`. loop = (start s, end s) or None (the whole song loops).
    trainer: also play it in every trainer battle instead of the normal trainer battle theme.
    Returns a report (list of lines)."""
    say = log or (lambda s: None)
    rate, samples = sb.load_audio(audio_path)
    if not len(samples):
        raise BgmError('The audio file is empty.')
    if gain_db:
        samples = sb.apply_gain(samples, gain_db)
    secs = len(samples) / rate
    ms = secs * 1000.0
    start, end = (0.0, secs) if not loop else (max(0.0, float(loop[0])), min(secs, float(loop[1])))
    if end - start < 1.0:
        raise BgmError('The loop must be at least a second long.')
    number = free_number(root, mod)
    title = title.strip() or os.path.splitext(os.path.basename(audio_path))[0]
    if not title.endswith('♪'):
        title += ' ♪'
    wem = sb.make_wem(rate, samples)
    bank_rel = '%s/%s.bnk' % (sb.BANK_DIR, BANK)
    with open(sb.bank_path(root, mod, BANK), 'rb') as f:
        b = sb.Bank(f.read(), BANK)
    add_to_bank(b, number, wem, ms, (start * 1000, end * 1000))
    data = b.tobytes()
    check = sb.Bank(data, BANK)                      # read back: the new event leads to the new audio
    ev = sb.fnv1('PLAY_BGM_IK_VS_SELECT_%02d' % number)
    if ev not in check.by_id:
        raise BgmError('Check failed: the new song is not in the rebuilt bank.')
    snap = {rel: (open(mod.path_for(rel), 'rb').read() if os.path.isfile(mod.path_for(rel)) else None)
            for rel in (bank_rel, COMMANDS, PICKER)}
    try:
        mod.add_bytes(bank_rel, data)
        say('BGM.bnk: new song %02d (%.1f s, %d Hz, %d channel(s))' % (number, secs, rate, samples.shape[1]))
        _add_commands(root, mod, number)
        if trainer:
            _use_for_trainers(root, mod, number)
        _add_picker(root, mod, number)
        langs = _add_name(root, mod, number, title)
    except Exception:
        for rel, old in snap.items():               # leave the mod as it was
            if old is None:
                if os.path.isfile(mod.path_for(rel)):
                    mod.remove(rel)
            else:
                mod.add_bytes(rel, old)
        raise
    return ['added "%s" to the Private Battles "Battle BGM" list as entry %02d (unlocked from the start)'
            % (title, number)] + \
        (['plays in every trainer battle (Battle Zones, trainers) instead of the normal trainer battle theme']
         if trainer else []) + [
            'song %.1f s, %d Hz, %d channel(s), 16-bit PCM in BGM.bnk; %s' % (
                secs, rate, samples.shape[1], 'loops the whole song' if not loop else
                'plays from the start, then loops %.2f s - %.2f s' % (start, end) if start > 0.001 else
                'loops 0 - %.2f s' % end),
            'name added in %d languages' % langs]
