"""Merging mods that change the same game file.

Packaging and "Install to Recomp" used to keep only the highest-priority mod's copy of a file. For game
data tables and text this module merges them instead: a 3-way merge against the game's original, row by
row and field by field:
  * rows/lines that different mods add are all kept (e.g. two mods that each add clothing to a shop)
  * a field changed by only one mod keeps that change
  * only a field two mods change *differently* goes to the higher-priority mod (reported)
Files that can't be merged safely (models, meshes, textures, sounds, unknown formats) still go to the
highest-priority mod, as before. Copies that are byte-for-byte identical are never a conflict.
"""
import json
import os
import struct
import tempfile

import za_formats as zf
import za_modding as zm

MISSING = object()
KEY_FIELDS = ('id', 'Id', 'ID', 'itemId', 'ItemId', 'itemID', 'name', 'Name', 'label', 'Label', 'key', 'Key',
              'devName', 'DevName', 'hash', 'Hash', 'type', 'Type')
# where game data tables live; model/mesh/texture/sound files are never merged field by field
TABLE_DIRS = ('world/', 'avalon/', 'system/', 'param_', 'ik_event/', 'script/', 'field/', 'ik_pokemon/catalog/',
              'ik_chara/savm_pc/')
TABLE_EXTS = ('.bin', '.trpmcatalog', '.ccdatam')


class MergeError(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------
# generic 3-way merge of JSON values
# ---------------------------------------------------------------------------------------------------------

def _key_of(lists):
    """A field that identifies each row of these lists of dicts (present and unique in every list)."""
    rows = [r for lst in lists for r in lst]
    if not rows or not all(isinstance(r, dict) for r in rows):
        return None
    for k in KEY_FIELDS:
        ok = True
        for lst in lists:
            vals = [r.get(k, MISSING) for r in lst]
            if any(v is MISSING or isinstance(v, (dict, list)) for v in vals) or len(set(map(repr, vals))) != len(vals):
                ok = False
                break
        if ok:
            return k
    return None


def merge3(base, ours, theirs, path, conflicts):
    """Merge `theirs` (higher priority) into `ours`, both derived from `base`. MISSING = field absent."""
    if _same(theirs, base):
        return ours
    if _same(ours, base) or _same(ours, theirs):
        return theirs
    if isinstance(ours, dict) and isinstance(theirs, dict):
        b = base if isinstance(base, dict) else {}
        out = {}
        for k in list(ours) + [k for k in theirs if k not in ours]:
            v = merge3(b.get(k, MISSING), ours.get(k, MISSING), theirs.get(k, MISSING), path + [k], conflicts)
            if v is not MISSING:
                out[k] = v
        return out
    if isinstance(ours, list) and isinstance(theirs, list):
        b = base if isinstance(base, list) else []
        key = _key_of([b, ours, theirs])
        if key is not None:
            return _merge_keyed(b, ours, theirs, key, path, conflicts)
        if len(b) == len(ours) == len(theirs):             # same length, no key: row by row (by position)
            return [merge3(x, y, z, path + [i], conflicts) for i, (x, y, z) in enumerate(zip(b, ours, theirs))]
    conflicts.append('/'.join(map(str, path)) or '(whole file)')
    return theirs


def _same(a, b):
    return a is b or (a is not MISSING and b is not MISSING and a == b)


def _merge_keyed(base, ours, theirs, key, path, conflicts):
    bm = {repr(r[key]): r for r in base}
    om = {repr(r[key]): r for r in ours}
    tm = {repr(r[key]): r for r in theirs}
    order = [repr(r[key]) for r in ours]
    # rows only `theirs` has go right after the row they follow in `theirs`
    for i, r in enumerate(theirs):
        k = repr(r[key])
        if k in om or (k in bm and _same(bm[k], r)):
            continue        # a row `ours` deleted is only kept when `theirs` changed it (and reported then)
        prev = next((repr(theirs[j][key]) for j in range(i - 1, -1, -1) if repr(theirs[j][key]) in order), None)
        order.insert(order.index(prev) + 1 if prev else len(order), k)
    out = []
    for k in order:
        v = merge3(bm.get(k, MISSING), om.get(k, MISSING), tm.get(k, MISSING), path + ['%s=%s' % (key, k)], conflicts)
        if v is not MISSING:
            out.append(v)
    return out


def merge_json(base, versions):
    """versions: [(mod name, value)] lowest priority first. Returns (merged, [conflict lines])."""
    acc, conflicts = base, []
    for name, v in versions:
        before = len(conflicts)
        acc = merge3(base, acc, v, [], conflicts)
        for i in range(before, len(conflicts)):
            conflicts[i] = '%s: "%s" wins' % (conflicts[i], name)
    return acc, conflicts


# ---------------------------------------------------------------------------------------------------------
# message text (.dat + .tbl) - lossless, lines can be added
# ---------------------------------------------------------------------------------------------------------

def msg_raw(data):
    """[(u16 chars, flag)] of a message .dat, decrypted but not interpreted (lossless)."""
    ns, nl, total, ik, so = struct.unpack_from('<HHIII', data, 0)
    out, key = [], 0x7C89
    for i in range(nl):
        off, ln, flag = struct.unpack_from('<IHH', data, so + 4 + i * 8)
        k, chars = key, []
        for j in range(ln):
            chars.append(struct.unpack_from('<H', data, so + off + 2 * j)[0] ^ k)
            k = ((k << 3) | (k >> 13)) & 0xFFFF
        out.append((tuple(chars), flag))
        key = (key + 0x2983) & 0xFFFF
    return out


def msg_build(lines):
    """Inverse of msg_raw: any number of lines (each re-encrypted for its position)."""
    table, data = bytearray(), bytearray()
    base = 4 + 8 * len(lines)
    key = 0x7C89
    for chars, flag in lines:
        k = key
        enc = bytearray()
        for c in chars:
            enc += struct.pack('<H', (c ^ k) & 0xFFFF)
            k = ((k << 3) | (k >> 13)) & 0xFFFF
        table += struct.pack('<IHH', base + len(data), len(chars), flag)
        data += enc
        while len(data) % 4:
            data += b'\0'
        key = (key + 0x2983) & 0xFFFF
    section = struct.pack('<I', base + len(data)) + table + data
    return struct.pack('<HHIII', 1, len(lines), len(section), 0, 0x10) + section


def tbl_entries(data):
    """[(raw 10-byte header + label bytes, label)] of an AHTB label table."""
    if data[:4] != b'AHTB':
        return []
    n = struct.unpack_from('<I', data, 4)[0]
    p, out = 8, []
    for _ in range(n):
        ln = struct.unpack_from('<H', data, p + 8)[0]
        raw = data[p:p + 10 + ln]
        out.append((bytes(raw), raw[10:].rstrip(b'\0').decode('utf-8', 'replace')))
        p += 10 + ln
    return out


def tbl_build(entries):
    return b'AHTB' + struct.pack('<I', len(entries)) + b''.join(raw for raw, _ in entries)


def merge_text(base_dat, base_tbl, versions):
    """versions: [(mod name, dat bytes, tbl bytes)] lowest priority first. Returns (dat, tbl, conflicts)."""
    def as_rows(dat, tbl):
        lines = msg_raw(dat)
        ents = tbl_entries(tbl) if tbl else []
        if ents and len(ents) >= len(lines):
            # label i names line i; a table can have extra labels without a line (kept, after the lines)
            rows = [{'label': lab, 'raw': raw.hex(), 'text': [list(c), f]} for (raw, lab), (c, f) in zip(ents, lines)]
            return rows + [{'label': lab, 'raw': raw.hex(), 'text': None} for raw, lab in ents[len(lines):]]
        return [{'text': [list(c), f]} for c, f in lines]          # no labels: by position
    base = as_rows(base_dat, base_tbl)
    merged, conflicts = merge_json(base, [(n, as_rows(d, t)) for n, d, t in versions])
    with_text = [r for r in merged if r.get('text') is not None]
    dat = msg_build([(tuple(r['text'][0]), r['text'][1]) for r in with_text])
    tbl = None
    if merged and 'label' in merged[0]:
        tbl = tbl_build([(bytes.fromhex(r['raw']), r['label']) for r in with_text + [r for r in merged if r.get('text') is None]])
    return dat, tbl, conflicts


# ---------------------------------------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------------------------------------

def is_table(rel, game_root):
    r = rel.lower()
    if not r.endswith(TABLE_EXTS) or not any(r.startswith(d) or ('/' + d) in r for d in TABLE_DIRS):
        return False
    return bool(zm.schema_for(os.path.join(game_root, *rel.split('/'))))


def is_text(rel):
    r = rel.lower()
    return r.startswith('ik_message/') and r.endswith(('.dat', '.tbl'))


def merge_table(rel, game_root, versions):
    """versions: [(mod name, bytes)] lowest priority first. Returns (bytes, conflicts)."""
    game = os.path.join(game_root, *rel.split('/'))
    schema = zm.schema_for(game)
    base = json.loads(zm.to_json(game, schema))
    vals = []
    with tempfile.TemporaryDirectory() as td:
        for k, (name, data) in enumerate(versions):
            p = os.path.join(td, '%d%s' % (k, os.path.splitext(rel)[1]))
            with open(p, 'wb') as f:
                f.write(data)
            vals.append((name, json.loads(zm.to_json(p, schema))))
        merged, conflicts = merge_json(base, vals)
        out = os.path.join(td, 'merged' + os.path.splitext(rel)[1])
        zm.from_json(json.dumps(merged), schema, out)
        with open(out, 'rb') as f:
            return f.read(), conflicts


def merge_all(sources, game_root, log=None):
    """sources: [(mod name, files, reader)] highest priority first (as za_modding.merge_mods uses).
    Returns ({rel: bytes} merged files, {rel: report}) for every file more than one mod has that can be
    merged; report = {'mods': [...], 'conflicts': [...], 'how': 'merged' | 'identical'}."""
    by_rel = {}
    for name, files, reader in sources:
        for rel in files:
            by_rel.setdefault(rel, []).append((name, reader))
    out, report = {}, {}
    done_text = set()
    for rel, owners in sorted(by_rel.items()):
        if len(owners) < 2:
            continue
        data = [(n, r(rel)) for n, r in owners]
        if all(d == data[0][1] for _, d in data):
            report[rel] = {'mods': [n for n, _ in owners], 'conflicts': [], 'how': 'identical'}
            continue
        if not game_root:
            continue
        try:
            if is_text(rel):
                stem = rel[:-4]
                if stem in done_text:
                    continue
                done_text.add(stem)
                game_dat = _read(game_root, stem + '.dat')
                game_tbl = _read(game_root, stem + '.tbl')
                if game_dat is None:
                    continue
                vers = []
                for name, files, reader in reversed(sources):        # lowest priority first
                    has_d, has_t = stem + '.dat' in files, stem + '.tbl' in files
                    if has_d or has_t:
                        vers.append((name, reader(stem + '.dat') if has_d else game_dat,
                                     reader(stem + '.tbl') if has_t else game_tbl))
                if len(vers) < 2:
                    continue
                dat, tbl, conflicts = merge_text(game_dat, game_tbl, vers)
                mods = [n for n, _, _ in reversed(vers)]
                out[stem + '.dat'] = dat
                report[stem + '.dat'] = {'mods': mods, 'conflicts': conflicts, 'how': 'merged'}
                if tbl is not None and (game_tbl is None or tbl != game_tbl or stem + '.tbl' in by_rel):
                    out[stem + '.tbl'] = tbl
                    report[stem + '.tbl'] = {'mods': mods, 'conflicts': [], 'how': 'merged'}
            elif is_table(rel, game_root):
                merged, conflicts = merge_table(rel, game_root, list(reversed(data)))
                out[rel] = merged
                report[rel] = {'mods': [n for n, _ in owners], 'conflicts': conflicts, 'how': 'merged'}
        except Exception as e:  # noqa: BLE001 - fall back to "highest priority wins" for this file
            if log:
                log('could not merge %s (%s) - the highest-priority mod\'s copy is used' % (rel, e))
    return out, report


def _read(root, rel):
    p = os.path.join(root, *rel.split('/'))
    if not os.path.isfile(p):
        return None
    with open(p, 'rb') as f:
        return f.read()
