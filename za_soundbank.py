"""Sounds and songs of the game (Wwise): a searchable index with names, and replacing them in a mod.

How the game's audio is organised (Wwise 2023, bank version 145)
  audio/wwise/bin/Switch/*.bnk   sound banks. HIRC = objects (events, actions, sounds, containers, music),
                                 DIDX/DATA = sounds stored in the bank.
  Stream.pck, Stream_dlc.pck     streamed sounds (most music and long sounds), by media id.
  Objects only have numbers. Event ids are the FNV-1 hash of the event name ("PLAY_BGM_VS_BOSS_01"); the
  names are found by hashing the strings in the game's data files (world/exl/audio/bgm_command, .trsot,
  .traef...). A sound gets the name of the nearest Play event above it: sound -> parent containers -> event.

Replacing (replace())
  The new audio (any format vgmstream reads: WAV, MP3, OGG, FLAC, M4A...) is stored as 16-bit PCM inside
  every bank that uses that sound, and the sound object is switched to PCM "in bank" - so streamed sounds
  don't need a copy of the 0.9 GB Stream.pck in the mod. Songs (music tracks): the track's length, its
  segment's length and the segment's entry/exit cues (= where the game loops it) are set from the new
  audio. Sounds: loop start/end are written into the audio (smpl chunk); the game uses them when it
  loops that sound. Every replacement is checked by decoding the rebuilt bank again.
"""
import json
import os
import re
import struct
import subprocess
import tempfile
import time

import numpy as np

import za_audio
import za_modding as zm

BANK_DIR = 'audio/wwise/bin/Switch'
PACKAGES = ('Stream.pck', 'Stream_dlc.pck', 'LooseMedia.pck')
INDEX_VERSION = 2
INDEX_PATH = os.path.join(zm.APP_DIR, 'audio_index.json')

SOUND, ACTION, EVENT, MUSIC_SEGMENT, MUSIC_TRACK, MUSIC_SWITCH = 2, 3, 4, 10, 11, 12
CONTAINERS = {5, 6, 7, 9, 10, 12, 13}         # random/sequence, switch, actor-mixer, layer, music containers
PLAY = 0x0403
PCM = 0x00010001
ENTRY_CUE, EXIT_CUE = 0x0298DF12, 0x5BBBD648  # Wwise's fixed marker ids
CODECS = {0x00650002: 'Silence', 0x00010001: 'PCM', 0x00020001: 'ADPCM', 0x00040001: 'Vorbis', 0x00110001: 'Opus', 0x00130001: 'Opus',
          0x00190001: 'PTADPCM'}

# bank -> (type, category). Type answers "song, sound effect or something else".
BANK_KIND = {
    'BGM': ('Music', 'Music'), 'ME': ('Jingle', 'Jingles'), 'COMMON': ('Sound effect', 'Common'),
    'PM_VOICE': ('Pokémon cry', 'Pokémon cries'), 'DIALOG': ('Voice', 'Voices'),
    'PM_SKILLS': ('Sound effect', 'Moves'), 'PM': ('Sound effect', 'Pokémon'), 'ENV': ('Ambience', 'Ambience'),
    'DEMO': ('Sound effect', 'Cutscenes'), 'UI': ('Sound effect', 'Menus'), 'VIB': ('Vibration', 'Vibration'),
    'BATTLE_SYSTEM': ('Sound effect', 'Battle'), 'PL': ('Sound effect', 'Player'), 'NPC': ('Sound effect', 'People'),
    'PROPS': ('Sound effect', 'World objects'),
}
TYPES = ('Music', 'Jingle', 'Sound effect', 'Ambience', 'Pokémon cry', 'Voice', 'Vibration')

# where the event names are: data files that mention sounds (found by scanning the whole export)
NAME_EXTS = {'.trsot', '.traef', '.trtml', '.trmdd', '.arc', '.truiv', '.trscn', '.bin', '.ptcl', '.tracs',
             '.dat', '.tbl', '.trsog', '.trpokecfg', '.json', '.lua', '.luac', '.trcol', '.trmmt'}
NAME_SKIP_DIRS = ('audio',)                  # top-level folders that can't contain names (the banks themselves)


class SoundError(Exception):
    pass


def fnv1(name):
    """Wwise short id of a name (FNV-1, 32 bit, lower case)."""
    h = 2166136261
    for c in name.lower().encode('utf-8'):
        h = ((h * 16777619) & 0xFFFFFFFF) ^ c
    return h


# ---------------------------------------------------------------------------------------------------------
# bank file
# ---------------------------------------------------------------------------------------------------------

class Bank:
    """A .bnk in memory: sections, HIRC objects (patched in place), media (DIDX/DATA)."""

    def __init__(self, data, name=''):
        self.name = name
        self.sections = []
        p = 0
        while p + 8 <= len(data):
            tag, n = data[p:p + 4], struct.unpack_from('<I', data, p + 4)[0]
            self.sections.append([tag, bytearray(data[p + 8:p + 8 + n])])
            p += 8 + n
        self.version = struct.unpack_from('<I', self.section(b'BKHD'), 0)[0] if self.section(b'BKHD') else 0
        self.objects = []                      # [type, id, body start, body end] within the HIRC section
        h = self.section(b'HIRC')
        if h:
            n = struct.unpack_from('<I', h, 0)[0]
            q = 4
            for _ in range(n):
                t = h[q]
                size, oid = struct.unpack_from('<II', h, q + 1)
                self.objects.append([t, oid, q + 9, q + 5 + size])
                q += 5 + size
        self.by_id = {o[1]: o for o in self.objects}

    def section(self, tag):
        for t, b in self.sections:
            if t == tag:
                return b
        return None

    def body(self, oid):
        o = self.by_id[oid]
        return self.section(b'HIRC')[o[2]:o[3]]

    def patch(self, oid, offset, fmt, *values):
        o = self.by_id[oid]
        struct.pack_into(fmt, self.section(b'HIRC'), o[2] + offset, *values)

    def media(self):
        """[(media id, offset, size)] in DIDX order (= vgmstream's subsong order)."""
        d = self.section(b'DIDX') or b''
        return [struct.unpack_from('<III', d, 12 * i) for i in range(len(d) // 12)]

    def media_bytes(self, mid):
        for i, off, size in self.media():
            if i == mid:
                return bytes(self.section(b'DATA')[off:off + size])
        return None

    def set_media(self, mid, blob):
        """Add or replace one sound in DIDX/DATA (DIDX stays sorted by id, data 16-byte aligned)."""
        ents = {i: (off, size) for i, off, size in self.media()}
        data = self.section(b'DATA') or bytearray()
        blobs = {i: bytes(data[off:off + size]) for i, (off, size) in ents.items()}
        if blob is None:
            blobs.pop(mid, None)
        else:
            blobs[mid] = blob
        didx, new = bytearray(), bytearray()
        for i in sorted(blobs):
            while len(new) % 16:
                new += b'\0'
            didx += struct.pack('<III', i, len(new), len(blobs[i]))
            new += blobs[i]
        self._set_section(b'DIDX', didx, after=b'BKHD')
        self._set_section(b'DATA', new, after=b'DIDX')

    def _set_section(self, tag, body, after):
        for s in self.sections:
            if s[0] == tag:
                s[1] = bytearray(body)
                return
        k = next((i for i, s in enumerate(self.sections) if s[0] == after), -1)
        self.sections.insert(k + 1, [tag, bytearray(body)])

    def tobytes(self):
        return b''.join(t + struct.pack('<I', len(b)) + bytes(b) for t, b in self.sections)

    # ---- objects ----
    def sources(self, oid):
        """Sound / music track sources: [(offset of the source in the body, plugin, stream type, media id, size)]"""
        o = self.by_id[oid]
        body = self.body(oid)
        if o[0] == SOUND:
            plugin, st, mid, size = struct.unpack_from('<IBII', body, 0)
            return [(0, plugin, st, mid, size)]
        if o[0] == MUSIC_TRACK:
            n = struct.unpack_from('<I', body, 1)[0]
            out = []
            for k in range(min(n, 256)):
                q = 5 + 14 * k
                plugin, st, mid, size = struct.unpack_from('<IBII', body, q)
                out.append((q, plugin, st, mid, size))
            return out
        return []

    def track_items(self, oid):
        """Music track playlist: [(offset of fPlayAt, media id, play at, begin trim, end trim, source duration ms)]"""
        body = self.body(oid)
        n = struct.unpack_from('<I', body, 1)[0]
        p = 5 + 14 * n
        out = []
        for k in range(struct.unpack_from('<I', body, p)[0]):
            q = p + 4 + 44 * k
            mid = struct.unpack_from('<I', body, q + 4)[0]
            out.append((q + 12, mid) + struct.unpack_from('<dddd', body, q + 12))
        return out

    def segment(self, oid):
        """Music segment end: (offset of fDuration, duration ms, [(marker id, position ms, offset of position)])"""
        body = self.body(oid)
        for p in range(len(body) - 4, 8, -1):
            n = struct.unpack_from('<I', body, p)[0]
            if not 1 <= n <= 64:
                continue
            q, marks = p + 4, []
            for _ in range(n):
                if q + 13 > len(body):
                    break
                mid = struct.unpack_from('<I', body, q)[0]
                pos = struct.unpack_from('<d', body, q + 4)[0]
                ln = body[q + 12]
                marks.append((mid, pos, q + 4))
                q += 13 + ln
            if len(marks) != n or q != len(body):
                continue
            dur = struct.unpack_from('<d', body, p - 8)[0]
            if 0 < dur < 1e8 and all(-1e-3 <= m[1] <= dur + 1e-3 for m in marks):
                return p - 8, dur, marks
        return None


def _container_ids_in(body, containers, own):
    """First container id found in a body = the object's parent (DirectParentID comes before child lists)."""
    mv = memoryview(body)
    for o in range(len(body) - 3):
        v = int.from_bytes(mv[o:o + 4], 'little')
        if v in containers and v != own:
            return v
    return None


# ---------------------------------------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------------------------------------

def bank_dir(root):
    return os.path.join(root, *BANK_DIR.split('/'))


def signature(root):
    d = bank_dir(root)
    sig = []
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        st = os.stat(os.path.join(d, f))
        sig.append([f, st.st_size, int(st.st_mtime)])
    return {'version': INDEX_VERSION, 'root': os.path.normcase(os.path.abspath(root)), 'files': sig}


def load_index(root):
    """The cached index if it still matches the game's audio files, else None."""
    try:
        with open(INDEX_PATH, encoding='utf-8') as f:
            idx = json.load(f)
    except (OSError, ValueError):
        return None
    return idx if idx.get('signature') == signature(root) else None


def scan_names(root, wanted, progress=None, cancel=None):
    """{id: name} for every wanted id whose name appears as a string in the game's data files."""
    tok = re.compile(rb'[A-Za-z][A-Za-z0-9_]{3,100}')
    seen, found = set(), {}
    files = []
    for top in sorted(os.listdir(root)):
        if top.lower() in NAME_SKIP_DIRS or not os.path.isdir(os.path.join(root, top)):
            continue
        for dp, dn, fn in os.walk(os.path.join(root, top)):
            files += [os.path.join(dp, f) for f in fn if os.path.splitext(f)[1].lower() in NAME_EXTS]
    for i, p in enumerate(files):
        if cancel is not None and cancel.is_set():
            raise SoundError('cancelled')
        if progress and i % 500 == 0:
            progress('Finding sound names… %d%%' % (100 * i // max(len(files), 1)))
        try:
            if os.path.getsize(p) > 64 << 20:
                continue
            with open(p, 'rb') as f:
                d = f.read()
        except OSError:
            continue
        for m in set(tok.findall(d)):
            if m in seen:
                continue
            seen.add(m)
            h = fnv1(m.decode('ascii'))
            if h in wanted and h not in found:
                found[h] = m.decode('ascii')
    return found


def _enrich(name, species, moves):
    """Readable extra: Pokémon name for PV_0025 / PM_0445_... / PM0382..., move name for EW0332 / EMW0109."""
    if not name:
        return ''
    m = re.search(r'(?:^|_)(?:PV|PM|OBV|PMV)_?(\d{4})(?:_|$)', name)
    if m and int(m.group(1)) in species:
        return species[int(m.group(1))]
    m = re.search(r'(?:^|_)E(?:M)?W(\d{4})(?:_|$)', name)
    if m and int(m.group(1)) in moves and int(m.group(1)) > 0:
        return moves[int(m.group(1))]
    return ''


def _game_names(root):
    species, moves = {}, {}
    try:
        import za_formats as zf
        base = os.path.join(root, 'ik_message', 'dat', 'English', 'common')
        with open(os.path.join(base, 'wazaname.dat'), 'rb') as f:
            moves = dict(enumerate(zf.decode_msg(f.read())))
        with open(os.path.join(base, 'monsname.tbl'), 'rb') as f:
            labels = zf.parse_tbl(f.read())
        with open(os.path.join(base, 'monsname.dat'), 'rb') as f:
            species = {int(l[9:]): t for l, t in zip(labels, zf.decode_msg(f.read())) if l.startswith('MONSNAME_')}
    except Exception:  # noqa: BLE001 - names are only a nicety
        pass
    return species, moves


def build_index(root, progress=None, cancel=None):
    """Index every sound and song: one entry per audio file (media id). Saved to INDEX_PATH."""
    t0 = time.time()
    d = bank_dir(root)
    if not os.path.isdir(d):
        raise SoundError('No sound banks found (%s). Import the game files first.' % BANK_DIR)
    progress and progress('Reading sound banks…')
    objs, in_bank, banks = {}, {}, []
    for f in sorted(os.listdir(d)):
        if not f.lower().endswith('.bnk'):
            continue
        with open(os.path.join(d, f), 'rb') as fh:
            b = Bank(fh.read(), f)
        name = f[:-4]
        banks.append(name)
        for t, oid, s, e in b.objects:
            objs.setdefault(oid, (t, name, bytes(b.section(b'HIRC')[s:e])))
        for k, (mid, off, size) in enumerate(b.media()):
            in_bank.setdefault(mid, (name, k, size))
    containers = {i for i, o in objs.items() if o[0] in CONTAINERS}
    parent, leaves, play_targets, switch_keys = {}, {}, {}, {}
    for oid, (t, bank, body) in objs.items():
        if t in CONTAINERS or t in (SOUND, MUSIC_TRACK):
            p = _container_ids_in(body, containers, oid)
            if p is not None:
                parent[oid] = p
        if t == SOUND and len(body) >= 13:
            plugin, st, mid, size = struct.unpack_from('<IBII', body, 0)
            leaves[oid] = [(plugin, st, mid)]
        elif t == MUSIC_TRACK and len(body) >= 5:
            n = struct.unpack_from('<I', body, 1)[0]
            leaves[oid] = [struct.unpack_from('<IBI', body, 5 + 14 * k) for k in range(min(n, 256))
                           if 5 + 14 * k + 14 <= len(body)]
        elif t == ACTION and len(body) >= 6:
            atype, target = struct.unpack_from('<HI', body, 0)
            if atype == PLAY:
                play_targets[oid] = target
        elif t == MUSIC_SWITCH:
            # decision tree leaves: [state/switch key u32][child id u32] -> which state plays which child
            for o in range(4, len(body) - 3):
                c = struct.unpack_from('<I', body, o)[0]
                if c in objs and objs[c][0] in CONTAINERS:
                    switch_keys.setdefault(c, struct.unpack_from('<I', body, o - 4)[0])
    events_of = {}
    for oid, (t, bank, body) in objs.items():
        if t == EVENT and body:
            n = body[0]
            for k in range(n):
                if 1 + 4 * k + 4 > len(body):
                    break
                a = struct.unpack_from('<I', body, 1 + 4 * k)[0]
                if a in play_targets:
                    events_of.setdefault(play_targets[a], set()).add(oid)
    progress and progress('Reading streamed sound packages…')
    streamed = {}
    for pk in PACKAGES:
        p = os.path.join(d, pk)
        if os.path.isfile(p):
            for fid, off, size, kind in zm.parse_akpk(p):
                if kind == 'wem':
                    streamed.setdefault(fid, (pk, off, size))
    wanted = {i for i, o in objs.items() if o[0] == EVENT} | set(switch_keys.values())
    names = scan_names(root, wanted, progress, cancel)
    species, moves = _game_names(root)
    progress and progress('Putting the list together…')

    entries, by_media = [], {}
    for leaf in sorted(leaves, key=lambda x: (objs[x][1], x)):
        t, bank, body = objs[leaf]
        # nearest Play event(s) going up the parents
        chain, n, evs = [], leaf, set()
        while n is not None and n not in chain:
            chain.append(n)
            if n in events_of:
                evs = events_of[n]
                break
            n = parent.get(n)
        ev_names = sorted(names.get(e, '') for e in evs)
        named = [x for x in ev_names if x]
        state = next((names[switch_keys[c]] for c in chain if c in switch_keys and switch_keys[c] in names), '')
        for plugin, st, mid in leaves[leaf]:
            if mid in by_media:
                e = by_media[mid]
                if leaf not in e['objects']:
                    e['objects'].append(leaf)
                continue
            kind_type, cat = BANK_KIND.get(bank, ('Sound effect', bank.title()))
            if t == MUSIC_TRACK and kind_type not in ('Jingle',):
                kind_type, cat = 'Music', 'Music' if bank == 'BGM' else cat
            where = 'bank' if mid in in_bank and st != 2 else ('stream' if mid in streamed else
                                                              ('bank' if mid in in_bank else 'missing'))
            e = {'id': mid, 'name': named[0] if named else '', 'events': named[:20], 'state': state,
                 'extra': _enrich(named[0] if named else '', species, moves), 'type': kind_type,
                 'category': cat, 'bank': bank, 'objects': [leaf], 'music': t == MUSIC_TRACK,
                 'codec': CODECS.get(plugin, '%08x' % plugin), 'where': where,
                 'replaceable': plugin & 0xFFFF == 1 and where != 'missing',
                 'event_group': min(evs) if evs else (chain[-1] if chain else leaf)}
            if mid in in_bank:
                e['bank_file'], e['bank_index'], e['size'] = in_bank[mid]
            elif mid in streamed:
                e['package'], e['offset'], e['size'] = streamed[mid]
            if t == MUSIC_TRACK:
                durs = []
                nsrc = struct.unpack_from('<I', body, 1)[0]
                p = 5 + 14 * nsrc
                for k in range(struct.unpack_from('<I', body, p)[0]):
                    q = p + 4 + 44 * k
                    if struct.unpack_from('<I', body, q + 4)[0] == mid:
                        durs.append(struct.unpack_from('<d', body, q + 36)[0])
                if durs:
                    e['length'] = round(max(durs) / 1000.0, 2)
            entries.append(e)
            by_media[mid] = e
    # "part k of n" for songs/sounds that share one event
    groups = {}
    for e in entries:
        groups.setdefault((e['bank'], e['event_group']), []).append(e)
    for g in groups.values():
        if len(g) > 1:
            for k, e in enumerate(g):
                e['part'] = [k + 1, len(g)]
    for e in entries:
        e.pop('event_group', None)
    idx = {'signature': signature(root), 'built': time.time(), 'seconds': round(time.time() - t0),
           'banks': banks, 'entries': entries,
           'named': sum(1 for e in entries if e['name'])}
    try:
        with open(INDEX_PATH, 'w', encoding='utf-8') as f:
            json.dump(idx, f)
    except OSError:
        pass
    return idx


def label(e):
    """One line for the list: name, Pokémon/move, state, part."""
    s = e['name'] or ('(unnamed %s %d)' % ('song' if e['type'] == 'Music' else 'sound', e['id']))
    if e.get('extra'):
        s += '  ·  ' + e['extra']
    if e.get('state'):
        s += '  ·  ' + e['state']
    if e.get('part'):
        s += '  ·  part %d of %d' % tuple(e['part'])
    return s


def matches(e, words):
    hay = ' '.join([e['name'], e.get('extra', ''), e.get('state', ''), e['type'], e['category'], e['bank'],
                    str(e['id'])] + e.get('events', [])).lower().replace('_', ' ')
    return all(w in hay for w in words)


# ---------------------------------------------------------------------------------------------------------
# decoding / playback
# ---------------------------------------------------------------------------------------------------------

def _vgm(args, timeout=300):
    r = subprocess.run([zm.VGMSTREAM] + args, capture_output=True, text=True, timeout=timeout,
                       creationflags=zm.NO_WINDOW)
    if r.returncode:
        raise SoundError((r.stderr or r.stdout or 'vgmstream failed').strip()[-400:])
    return r.stdout


def bank_path(root, mod, bank):
    """The mod's copy of a bank if it has one, else the game's."""
    rel = '%s/%s.bnk' % (BANK_DIR, bank)
    if mod is not None and os.path.isfile(mod.path_for(rel)):
        return mod.path_for(rel)
    return os.path.join(root, *rel.split('/'))


_changed_cache = {}


def changed_ids(root, mod):
    """Media ids whose audio in the mod's banks differs from the game's (cached per bank file version)."""
    out = set()
    if mod is None:
        return out
    for rel in mod.files():
        if not (rel.startswith(BANK_DIR + '/') and rel.lower().endswith('.bnk')):
            continue
        mp = mod.path_for(rel)
        st = os.stat(mp)
        key = (os.path.normcase(mp), st.st_size, st.st_mtime_ns)
        if key not in _changed_cache:
            ids = set()
            try:
                with open(mp, 'rb') as f:
                    mb = Bank(f.read())
                gp = os.path.join(root, *rel.split('/'))
                gb = None
                if os.path.isfile(gp):
                    with open(gp, 'rb') as f:
                        gb = Bank(f.read())
                game = {m: (o, s) for m, o, s in gb.media()} if gb else {}
                gdata = gb.section(b'DATA') if gb else b''
                mdata = mb.section(b'DATA') or b''
                for m, o, s in mb.media():
                    if m not in game or bytes(mdata[o:o + s]) != bytes(gdata[game[m][0]:game[m][0] + game[m][1]]):
                        ids.add(m)
            except (OSError, struct.error):
                pass
            _changed_cache[key] = ids
        out |= _changed_cache[key]
    return out


def in_mod(root, mod, e):
    """True when the current mod has its own version of this sound."""
    return e['id'] in changed_ids(root, mod)


def decode(root, e, out_wav, mod=None):
    """Decode a sound to WAV: the mod's version when it has one, else the game's."""
    if mod is not None and in_mod(root, mod, e):
        with open(bank_path(root, mod, e['bank']), 'rb') as f:
            b = Bank(f.read())
        blob = b.media_bytes(e['id'])
        if blob is not None:
            with tempfile.TemporaryDirectory() as td:
                p = os.path.join(td, 'sound.wem')
                with open(p, 'wb') as f:
                    f.write(blob)
                _vgm(['-i', '-o', out_wav, p])
            return out_wav
    if e.get('bank_file'):
        _vgm(['-i', '-s', str(e['bank_index'] + 1), '-o', out_wav,
              os.path.join(bank_dir(root), e['bank_file'] + '.bnk')])
    elif e.get('package'):
        with open(os.path.join(bank_dir(root), e['package']), 'rb') as f:
            f.seek(e['offset'])
            blob = f.read(e['size'])
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'sound.wem')
            with open(p, 'wb') as f:
                f.write(blob)
            _vgm(['-i', '-o', out_wav, p])
    else:
        raise SoundError('This sound has no audio data in the game files.')
    return out_wav


def info(wav_or_media):
    """vgmstream metadata: rate, channels, samples, loop start/end (samples) or None."""
    out = _vgm(['-m', wav_or_media])
    g = lambda pat: re.search(pat, out)  # noqa: E731
    rate = int(g(r'sample rate:\s*(\d+)').group(1))
    samples = int(g(r'stream total samples:\s*(\d+)').group(1))
    ch = int(g(r'channels:\s*(\d+)').group(1))
    ls, le = g(r'loop start:\s*(\d+)'), g(r'loop end:\s*(\d+)')
    return {'rate': rate, 'channels': ch, 'samples': samples, 'seconds': samples / rate,
            'loop': (int(ls.group(1)), int(le.group(1))) if ls and le else None}


# ---------------------------------------------------------------------------------------------------------
# preparing new audio
# ---------------------------------------------------------------------------------------------------------

def load_audio(path):
    """Any audio file vgmstream reads -> (rate, int16 samples (frames, channels))."""
    if path.lower().endswith('.wav'):
        try:
            return za_audio.read_wav(path)
        except za_audio.AudioError:
            pass                                   # odd WAV variants: let vgmstream try
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 'decoded.wav')
        _vgm(['-o', out, path])
        return za_audio.read_wav(out)


def rms_db(samples):
    x = samples.astype(np.float64) / 32768.0
    r = np.sqrt(np.mean(x * x)) if x.size else 0.0
    return 20 * np.log10(r) if r > 0 else -120.0


def apply_gain(samples, db):
    if not db:
        return samples
    x = samples.astype(np.float64) * (10 ** (db / 20.0))
    return np.clip(np.rint(x), -32768, 32767).astype(np.int16)


def make_wem(rate, samples, loop=None):
    """16-bit PCM .wem; loop = (start sample, end sample) adds a smpl chunk (Wwise/vgmstream loop points)."""
    wem = za_audio.make_pcm_wem(rate, samples)
    if loop:
        start, end = int(loop[0]), int(loop[1])
        smpl = struct.pack('<9I', 0, 0, int(1e9 / rate), 60, 0, 0, 0, 1, 0)
        smpl += struct.pack('<6I', 0, 0, start, max(start, end - 1), 0, 0)
        body = wem[8:] + b'smpl' + struct.pack('<I', len(smpl)) + smpl
        wem = b'RIFF' + struct.pack('<I', len(body)) + body
    return wem


# ---------------------------------------------------------------------------------------------------------
# replace / restore
# ---------------------------------------------------------------------------------------------------------

def banks_using(root, e):
    """Bank names whose objects play this sound (usually one)."""
    out = [e['bank']]
    if e.get('bank_file') and e['bank_file'] not in out:
        out.append(e['bank_file'])
    return out


def _patch_bank(b, e, wem, ms, loop_ms):
    """Point every source of this media in bank b at the new PCM audio. Returns notes."""
    notes = []
    mid = e['id']
    touched = False
    for oid in [o[1] for o in b.objects if o[0] in (SOUND, MUSIC_TRACK)]:
        for q, plugin, st, m, size in b.sources(oid):
            if m != mid:
                continue
            if plugin & 0xFFFF != 1:
                raise SoundError('This sound is made by a plugin, not an audio file - it cannot be replaced.')
            b.patch(oid, q, '<IBII', PCM, 0, mid, len(wem))
            touched = True
            if b.by_id[oid][0] == MUSIC_TRACK:
                notes += _patch_music(b, oid, mid, ms, loop_ms)
    if touched:
        b.set_media(mid, wem)
    return touched, notes


def _patch_music(b, track, mid, ms, loop_ms):
    """New length for the track's playlist item(s), and the segment's length and loop cues."""
    notes = []
    play_at = 0.0
    for off, m, at, bt, et, dur in b.track_items(track):
        if m == mid:
            b.patch(track, off, '<dddd', at, 0.0, 0.0, ms)
            play_at = at
    seg = next((o[1] for o in b.objects if o[0] == MUSIC_SEGMENT and
                struct.pack('<I', track) in b.body(o[1])), None)
    if seg is None:
        return ['no music segment found for this track - only the track was updated']
    r = b.segment(seg)
    if r is None:
        return ['could not read the music segment - its length and loop were not changed']
    dur_off, old_dur, marks = r
    new_dur = play_at + ms
    others = [o for o in b.objects if o[0] == MUSIC_TRACK and o[1] != track and
              struct.pack('<I', seg) in b.body(o[1])]
    if others:
        notes.append('this song part has %d more track(s) layered on it; they keep their own audio' % len(others))
    start, end = loop_ms if loop_ms else (0.0, ms)
    start, end = play_at + max(0.0, start), play_at + min(ms, end)
    b.patch(seg, dur_off, '<d', new_dur)
    for mk, pos, off in marks:
        if mk == ENTRY_CUE:
            b.patch(seg, off, '<d', start)
        elif mk == EXIT_CUE:
            b.patch(seg, off, '<d', end)
        elif pos > new_dur:
            b.patch(seg, off, '<d', new_dur)
    notes.append('song length %.2f s, loops from %.2f s to %.2f s' % (ms / 1000, (start - play_at) / 1000,
                                                                     (end - play_at) / 1000))
    return notes


def replace(root, mod, e, audio_path, gain_db=0.0, match_loudness=False, loop=None, log=None):
    """Replace one sound/song in `mod` with an audio file. loop = (start s, end s) or None (songs: whole
    file; sounds: no loop points). Returns a report (list of lines)."""
    rate, samples = load_audio(audio_path)
    if not len(samples):
        raise SoundError('The audio file is empty.')
    report = []
    if match_loudness:
        with tempfile.TemporaryDirectory() as td:
            orig = os.path.join(td, 'orig.wav')
            decode(root, e, orig)
            _, o = za_audio.read_wav(orig)
        gain_db = rms_db(o) - rms_db(samples) + (gain_db or 0.0)
        report.append('loudness matched to the original (%+.1f dB)' % gain_db)
        peak = int(np.abs(samples.astype(np.int32)).max()) or 1
        headroom = 20 * np.log10(32767.0 / peak)
        if gain_db > headroom:
            gain_db = headroom
            report.append('limited to %+.1f dB so the loudest part does not distort' % gain_db)
    elif gain_db:
        report.append('volume %+.1f dB' % gain_db)
    samples = apply_gain(samples, gain_db)
    ms = len(samples) * 1000.0 / rate
    loop_ms = None
    smpl = None
    if loop:
        s, en = max(0.0, float(loop[0])), min(len(samples) / rate, float(loop[1]))
        if en - s < 0.05:
            raise SoundError('The loop must be at least 0.05 s long.')
        loop_ms = (s * 1000, en * 1000)
        if not e['music']:
            smpl = (int(round(s * rate)), int(round(en * rate)))
    wem = make_wem(rate, samples, smpl)
    done = []
    before = {}                               # the mod's banks as they were, to undo a replace that fails its check
    for bank in banks_using(root, e):
        src = bank_path(root, mod, bank)
        with open(src, 'rb') as f:
            b = Bank(f.read(), bank)
        touched, notes = _patch_bank(b, e, wem, ms, loop_ms)
        if not touched:
            continue
        rel = '%s/%s.bnk' % (BANK_DIR, bank)
        mp = mod.path_for(rel)
        if rel not in before:
            if os.path.isfile(mp):
                with open(mp, 'rb') as f:
                    before[rel] = f.read()
            else:
                before[rel] = None
        mod.add_bytes(rel, b.tobytes())
        done.append(bank)
        report += notes
    if not done:
        raise SoundError('No sound bank uses this sound - nothing was changed.')
    try:
        check = verify(root, mod, e, samples)
    except Exception:
        for rel, data in before.items():
            if data is None:
                mod.remove(rel)
            else:
                mod.add_bytes(rel, data)
        raise
    report.insert(0, 'replaced with %s: %.2f s, %d Hz, %d channel(s), 16-bit PCM in %s' % (
        os.path.basename(audio_path), ms / 1000, rate, samples.shape[1], ', '.join(b + '.bnk' for b in done)))
    if smpl:
        report.append('loop points %.2f s - %.2f s (used when the game loops this sound)' % loop)
    report.append(check)
    return report


def verify(root, mod, e, samples):
    """Decode the sound from the rebuilt bank and compare it with what was written."""
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 'check.wav')
        decode(root, e, out, mod)
        _, back = za_audio.read_wav(out)
    if back.shape == samples.shape and np.array_equal(back, samples):
        return 'checked: the rebuilt bank plays back exactly the new audio'
    raise SoundError('Check failed: the rebuilt bank does not play back the new audio (got %s, expected %s).'
                     % (back.shape, samples.shape))


def restore(root, mod, e):
    """Put the game's original sound back into the mod's bank(s); removes banks left unchanged."""
    restored = []
    for bank in banks_using(root, e):
        rel = '%s/%s.bnk' % (BANK_DIR, bank)
        mp = mod.path_for(rel)
        if not os.path.isfile(mp):
            continue
        with open(os.path.join(root, *rel.split('/')), 'rb') as f:
            orig_bytes = f.read()
        orig = Bank(orig_bytes, bank)
        with open(mp, 'rb') as f:
            b = Bank(f.read(), bank)
        objs = set(e['objects'])
        for oid in list(objs):                       # the segments of music tracks too
            if b.by_id.get(oid, [0])[0] == MUSIC_TRACK:
                objs |= {o[1] for o in b.objects if o[0] == MUSIC_SEGMENT and struct.pack('<I', oid) in b.body(o[1])}
        for oid in objs:
            if oid in b.by_id and oid in orig.by_id:
                body = orig.body(oid)
                if len(body) == len(b.body(oid)):
                    h = b.section(b'HIRC')
                    o = b.by_id[oid]
                    h[o[2]:o[3]] = body
        b.set_media(e['id'], orig.media_bytes(e['id']))
        new = b.tobytes()
        if new == orig_bytes:
            mod.remove(rel)
        else:
            mod.add_bytes(rel, new)
        restored.append(bank)
    return restored
