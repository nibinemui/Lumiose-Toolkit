"""In-game name index: lets you search by Pokémon name or clothing item name and
find the folders/files that belong to it.

Sources
  * Pokémon names    : ik_message/dat/English/common/monsname.dat (+ .tbl) from the export,
                       falling back to names/monsname_english.txt
  * Clothing names   : names/dressup_item_name_english.txt (English text dump; this export
                       is missing the English clothing table), falling back to any
                       dressup_item_name.dat that is in the export (e.g. LATAM)
  * Clothing -> model: world/exl/dress_up_data/dress_up_data/dress_up_data.bin
"""
import difflib
import os
import re
import struct
import unicodedata

import za_formats as zf

APP_DIR = os.path.dirname(os.path.abspath(__file__))
NAMES_DIR = os.path.join(APP_DIR, 'names')

CLOTHING_CATS = {
    1: 'Top', 2: 'Bottoms', 3: 'Outfit', 4: 'Headwear', 5: 'Eyewear',
    6: 'Gloves', 7: 'Legwear', 8: 'Footwear', 9: 'Bag', 10: 'Accessory',
}
# keys that are not regular shop items (holo / uniform / placeholder variants)
_SPECIAL = re.compile(r'_(x|y|n_\d+|p_\d+|j_\d+|c_\d+|holo_[xy])$')
_THUMB = re.compile(r'^p\d_([a-z]+\d{4}_\d\d_[a-z0-9]+?)_(?:normal|tex)')


# Species numbering
#   * MONSNAME_<n> text labels use the game's *internal* species ID. For Gen 1-8 that equals the
#     National Dex number; Paldea Pokémon (internal 917+) are shuffled.
#   * Model folders: Gen 1-8 use pm<internal>; Gen 9 (internal 906+) use pm<internal + 104>.
#     Confirmed by world/exl/pokemon/megaevo_data (Scovillain internal 939 -> m_shinka_pm1043,
#     Tatsugiri 952 -> pm1056, Baxcalibur 951 -> pm1055, Glimmora 967 -> pm1071) and by the
#     skeletons of the other pm10xx/pm11xx folders.
# Internal -> National offsets for internal 917+, from PKHeX SpeciesConverter (Table9InternalToNational).
_GEN9_TO_NATIONAL = [
    65, -1, -1, -1, -1, 31, 31, 47, 47, 29, 29, 53, 31, 31, 46, 44, 30, 30, -7, -7, -7, 13, 13, -2,
    -2, 23, 23, 24, -21, -21, 27, 27, 47, 47, 47, 26, 14, -33, -33, -33, -17, -17, 3, -29, 12, -12,
    -31, -31, -31, 3, 3, -24, -24, -44, -44, -30, -30, -28, -28, 23, 23, 6, 7, 29, 8, 3, 4, 4, 20,
    4, 23, 6, 3, 3, 4, -1, 13, 9, 7, 5, 7, 9, 9, -43, -43, -43, -68, -68, -68, -58, -58, -25, -29,
    -31, 6, -1, 6, 0, 0, 0, 3, 3, 4, 2, 3, 3, -5, -12, -12,
]
GEN9_FIRST = 906
GEN9_FOLDER_SHIFT = 104


# Kalos-and-later Pokémon (internal 650-905) use development model numbers that don't follow
# the dex. Only these were confirmed (from their menu icons and the mega-evolution table);
# the full list is in avalon/data/personal_array.bin, which is read when present in the dump.
_VERIFIED_MODEL = {
    654: 718,   # Braixen (icon)
    677: 733,   # Espurr (icon)
    678: 734,   # Meowstic (mega table, male+female forms)
    681: 746,   # Aegislash (icon)
    697: 738,   # Tyrantrum (icon)
    718: 770,   # Zygarde (icon)
    720: 774,   # Hoopa (icon)
    740: 860,   # Crabominable (mega table)
    780: 856,   # Drampa (icon)
    802: 883,   # Marshadow (icon)
    807: 888,   # Zeraora (mega table)
}
UNKNOWN_FIRST, UNKNOWN_LAST = 650, 905


def national_dex(internal):
    i = internal - 917
    return internal + _GEN9_TO_NATIONAL[i] if 0 <= i < len(_GEN9_TO_NATIONAL) else internal


def model_id(internal, table=None):
    """Internal species ID (MONSNAME_<n>) -> pmXXXX model folder number, or None if unknown."""
    if table and internal in table:
        return table[internal]
    if internal >= GEN9_FIRST:
        return internal + GEN9_FOLDER_SHIFT
    if UNKNOWN_FIRST <= internal <= UNKNOWN_LAST:
        return _VERIFIED_MODEL.get(internal)
    return internal


def _catalog_models(root):
    """species -> model number from the game's Pokémon model catalog (most reliable source)."""
    p = os.path.join(root, 'ik_pokemon', 'catalog', 'catalog', 'poke_resource_table.trpmcatalog')
    try:
        with open(p, 'rb') as f:
            d = f.read()
    except OSError:
        return {}
    try:
        u32 = lambda o: struct.unpack_from('<I', d, o)[0]

        def fields(t):
            vt = t - struct.unpack_from('<i', d, t)[0]
            vl = struct.unpack_from('<H', d, vt)[0]
            return [struct.unpack_from('<H', d, vt + 4 + 2 * i)[0] for i in range((vl - 4) // 2)]

        r = u32(0)
        vp = r + fields(r)[1]                     # Catalog.table
        v = vp + u32(vp)
        out = {}
        for k in range(u32(v)):
            e = v + 4 + 4 * k
            t = e + u32(e)
            tf = fields(t)
            if len(tf) < 2 or not tf[0] or not tf[1]:
                continue
            si = t + tf[0]
            si += u32(si)                          # SpeciesInfo table
            sf = fields(si) + [0, 0]
            species = struct.unpack_from('<H', d, si + sf[0])[0] if sf[0] else 0
            form = struct.unpack_from('<H', d, si + sf[1])[0] if sf[1] else 0
            q = t + tf[1] + u32(t + tf[1])          # model_path string
            path = d[q + 4:q + 4 + u32(q)].decode('ascii', 'replace')
            m = re.search(r'pm(\d{4})_', path)
            if m and form == 0 and species:
                out.setdefault(species, int(m.group(1)))
        return out if out.get(25) == 25 else {}
    except (struct.error, IndexError):
        return {}


def _personal_models(root):
    """species -> model number from avalon/data/personal_array.bin (if the dump has it)."""
    p = os.path.join(root, 'avalon', 'data', 'personal_array.bin')
    try:
        with open(p, 'rb') as f:
            d = f.read()
    except OSError:
        return {}
    try:
        def fields(o):
            vt = o - struct.unpack_from('<i', d, o)[0]
            vl = struct.unpack_from('<H', d, vt)[0]
            return [struct.unpack_from('<H', d, vt + 4 + 2 * i)[0] for i in range((vl - 4) // 2)]
        r = struct.unpack_from('<I', d, 0)[0]
        f0 = r + fields(r)[0]
        vec = f0 + struct.unpack_from('<I', d, f0)[0]
        out = {}
        for i in range(struct.unpack_from('<I', d, vec)[0]):
            e = vec + 4 + 4 * i
            t = e + struct.unpack_from('<I', d, e)[0]
            fo = fields(t)
            if not fo or not fo[0]:
                continue
            species, form, model = struct.unpack_from('<HHH', d, t + fo[0])  # SpeciesInfo struct
            if form == 0 and species and model:
                out.setdefault(species, model)
        # sanity check: Pikachu must map to itself, otherwise the layout guess was wrong
        return out if out.get(25) == 25 and len(out) > 500 else {}
    except (struct.error, IndexError):
        return {}


_UNNAMED = None


def unnamed_info(filename):
    """Description of a hash-named file the export could not name, or None."""
    global _UNNAMED
    m = re.match(r'^([0-9A-Fa-f]{16})\.bin$', os.path.basename(filename))
    if not m:
        return None
    if _UNNAMED is None:
        try:
            import json
            with open(os.path.join(NAMES_DIR, 'unnamed_files.json'), encoding='utf-8') as f:
                _UNNAMED = json.load(f)
        except (OSError, ValueError):
            _UNNAMED = {}
    return _UNNAMED.get(m.group(1).upper())


def norm(s):
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]', '', s.lower())


class NameEntry:
    __slots__ = ('name', 'kind', 'detail', 'pattern', 'blob', 'words', 'number')

    def __init__(self, name, kind, detail, pattern, extra='', number=None):
        self.name = name
        self.kind = kind
        self.detail = detail
        self.pattern = pattern.lower()          # substring that appears in file/folder names ('' = unknown)
        self.blob = norm(name) + ' ' + norm(extra)
        self.words = [norm(w) for w in re.split(r'[\s&/\-,.]+', name) if norm(w)]
        self.number = number

    def label(self):
        return '%s   [%s · %s]' % (self.name, self.kind, self.detail)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def _read_text_dump(path):
    """Parse 'index<TAB>hash<TAB>label<TAB>text' lines."""
    out = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            parts = line.rstrip('\n').split('\t')
            if len(parts) >= 4:
                out[parts[2]] = parts[3]
    return out


def _read_msg(dat_path):
    tbl_path = os.path.splitext(dat_path)[0] + '.tbl'
    with open(dat_path, 'rb') as f:
        dat = f.read()
    with open(tbl_path, 'rb') as f:
        labels = zf.parse_tbl(f.read())
    return dict(zip(labels, zf.decode_msg(dat)))


def _pokemon(root):
    names = {}
    p = os.path.join(root, 'ik_message', 'dat', 'English', 'common', 'monsname.dat')
    try:
        names = _read_msg(p)
    except (OSError, ValueError, struct.error):
        pass
    if not names:
        fb = os.path.join(NAMES_DIR, 'monsname_english.txt')
        if os.path.isfile(fb):
            names = _read_text_dump(fb)
    table = _catalog_models(root) or _personal_models(root)
    out = []
    for label, name in names.items():
        m = re.match(r'MONSNAME_(\d+)$', label)
        if not m or not name.strip() or int(m.group(1)) == 0:
            continue
        n = int(m.group(1))            # internal species ID
        mid, nat = model_id(n, table), national_dex(n)
        if mid is None:
            out.append(NameEntry(name, 'Pokémon', 'Dex #%04d  model folder unknown' % nat, '',
                                 extra='pokemon', number=nat))
        else:
            out.append(NameEntry(name, 'Pokémon', 'Dex #%04d  pm%04d' % (nat, mid), 'pm%04d' % mid,
                                 extra='pokemon pm%04d' % mid, number=nat))
    return out


def _dressup_names(root):
    msg = os.path.join(root, 'ik_message', 'dat')
    own = os.path.join(msg, 'English', 'common', 'dressup_item_name.dat')
    if os.path.isfile(own):  # the game's own English table, when the export has it
        try:
            names = _read_msg(own)
            if names:
                return names
        except (OSError, ValueError, struct.error):
            pass
    fb = os.path.join(NAMES_DIR, 'dressup_item_name_english.txt')
    if os.path.isfile(fb):
        return _read_text_dump(fb)
    if os.path.isdir(msg):
        for lang in sorted(os.listdir(msg)):
            p = os.path.join(msg, lang, 'common', 'dressup_item_name.dat')
            if os.path.isfile(p):
                try:
                    return _read_msg(p)
                except (OSError, ValueError, struct.error):
                    continue
    return {}


def _dressup_rows(root):
    """Return list of (id, key, thumb) from dress_up_data.bin (a FlatBuffer)."""
    p = os.path.join(root, 'world', 'exl', 'dress_up_data', 'dress_up_data', 'dress_up_data.bin')
    with open(p, 'rb') as f:
        d = f.read()

    def fields(o):
        vt = o - struct.unpack_from('<i', d, o)[0]
        vl = struct.unpack_from('<H', d, vt)[0]
        return [struct.unpack_from('<H', d, vt + 4 + 2 * i)[0] for i in range((vl - 4) // 2)]

    def string(p):
        q = p + struct.unpack_from('<I', d, p)[0]
        n = struct.unpack_from('<I', d, q)[0]
        return d[q + 4:q + 4 + n].decode('utf-8', 'replace')

    root_t = struct.unpack_from('<I', d, 0)[0]
    f0 = root_t + fields(root_t)[0]
    vec = f0 + struct.unpack_from('<I', d, f0)[0]
    rows = []
    for i in range(struct.unpack_from('<I', d, vec)[0]):
        e = vec + 4 + 4 * i
        t = e + struct.unpack_from('<I', d, e)[0]
        fo = fields(t)
        if len(fo) < 4 or not fo[0] or not fo[1] or not fo[3]:
            continue
        rows.append((struct.unpack_from('<I', d, t + fo[0])[0], string(t + fo[1]), string(t + fo[3])))
    return rows


_KEY_NAMES = {}


def clothing_by_id(root):
    """{dress-up item id (e.g. 1110101): English name} - colour variants share their item's name."""
    _KEY_NAMES.clear()
    _clothing(root)
    try:
        rows = _dressup_rows(root)
    except (OSError, struct.error, IndexError):
        return {}
    return {i: _KEY_NAMES[k] for i, k, _ in rows if k in _KEY_NAMES}


def _clothing(root):
    names = _dressup_names(root)
    try:
        rows = _dressup_rows(root)
    except (OSError, struct.error, IndexError):
        return []
    cats, thumb = {}, {}
    for id_, key, th in rows:
        cats.setdefault(id_ // 1000000, [])
        if key not in thumb:
            cats[id_ // 1000000].append(key)
            thumb[key] = th
    groups = {}
    for label, text in names.items():
        m = re.match(r'dressup_item_group_(\d)(\d\d)$', label)
        if m:
            groups.setdefault(int(m.group(1)), []).append((int(m.group(2)), text))
    for g in groups.values():
        g.sort()

    out = []

    def add(key, name, cat):
        m = _THUMB.match(thumb.get(key, ''))
        if name.strip():
            _KEY_NAMES[key] = name
        if not m or not name.strip():
            return
        model = m.group(1)
        out.append(NameEntry(name, 'Clothing', '%s · %s' % (CLOTHING_CATS.get(cat, '?'), model), model,
                             extra='%s %s' % (key, model)))

    named = {}
    for cat in range(1, 10):
        keys = [k for k in cats.get(cat, []) if not _SPECIAL.search(k) and not re.search(r'_ju_\d+$', k)]
        names = groups.get(cat - 1, [])
        if len(keys) < len(names):
            # uniform ("ju") items are named after all the regular ones
            keys += [k for k in cats.get(cat, []) if re.search(r'_ju_\d+$', k)]
        for key, (_, name) in zip(keys, names):
            add(key, name, cat)
            named[key] = name
    # group 9xx: earrings (category 10) followed by the holo-X / holo-Y variants
    extra = [n for _, n in groups.get(9, [])]
    holo = [n for n in extra if n.lower().startswith('holo-')]
    plain = [n for n in extra if not n.lower().startswith('holo-')]
    for key, name in zip(cats.get(10, []), plain):
        add(key, name, 10)
    holo_keys = [(c, k) for c in sorted(cats) for k in cats[c] if re.search(r'_(x|y)$', k)]
    left = list(holo)
    for cat, key in holo_keys:
        xy = key[-1].upper()
        base = named.get(re.sub(r'_[xy]$', '_1', key), '')
        base = re.sub(r'\s+Set$', '', base)
        want = 'Holo-%s %s' % (xy, base)
        pick = next((n for n in left if n == want), None)
        if pick is None:
            pick = next((n for n in left if n.startswith('Holo-%s' % xy)), None)
        if pick:
            left.remove(pick)
            add(key, pick, cat)
    return out


def build(root):
    entries = []
    for fn in (_pokemon, _clothing):
        try:
            entries.extend(fn(root))
        except Exception:  # noqa: BLE001 - a missing source just means fewer names
            pass
    return entries


# ---------------------------------------------------------------------------
# searching
# ---------------------------------------------------------------------------

def search(entries, query, limit=40):
    """Return entries matching the query, best first.

    Every word must match: as part of the name (ignoring spaces/punctuation/accents),
    as a close spelling of one of the name's words, or as a Pokédex number.
    """
    toks = [norm(t) for t in query.split()]
    toks = [t for t in toks if t]
    if not toks:
        return []
    qn = ''.join(toks)
    # only allow "close spelling" matches for words that match nothing exactly (typos)
    fuzzy_ok = {t: len(t) >= 4 and not t.isdigit() and not any(t in e.blob for e in entries) for t in toks}
    scored = []
    for e in entries:
        score = 0
        ok = True
        for t in toks:
            if t.isdigit() and e.number is not None:
                if int(t) == e.number:
                    score += 50
                    continue
                ok = False
                break
            if t in e.blob:
                score += 10 + (5 if any(w.startswith(t) for w in e.words) else 0)
                continue
            if fuzzy_ok[t]:
                best = max((difflib.SequenceMatcher(None, t, w).ratio() for w in e.words), default=0)
                if best >= 0.75:
                    score += int(best * 8)
                    continue
            ok = False
            break
        if not ok:
            continue
        name_n = norm(e.name)
        if name_n == qn:
            score += 100
        elif name_n.startswith(qn):
            score += 30
        score -= len(name_n) / 100.0
        scored.append((score, e))
    scored.sort(key=lambda x: (-x[0], x[1].name))
    return [e for _, e in scored[:limit]]


def clothing_colour_names(root, folder):
    """{(set, group): [colour name per choice]} for a clothing model folder, from the dress-up data
    (texturePath '<folder>_<set>_r<main>_c<second>' with primaryColor / secondaryColor labels)."""
    import json
    import za_modding as zm
    try:
        rows = json.loads(zm.to_json(os.path.join(root, 'world', 'exl', 'dress_up_data', 'dress_up_data',
                                                  'dress_up_data.bin')))['items']
    except Exception:  # noqa: BLE001
        return {}
    names = _dressup_names(root)
    out = {}
    pre = folder + '_'
    for r in rows:
        tp = r.get('texturePath', '')
        if not tp.startswith(pre):
            continue
        m = re.match(r'(.+)_r(\d+)_c(\d+)$', tp[len(pre):])
        if not m:
            continue
        st, ri, ci = m.group(1), int(m.group(2)), int(m.group(3))
        for grp, idx, key in (('main', ri, 'primaryColor'), ('second', ci, 'secondaryColor')):
            lab = names.get(r.get(key, ''), '')
            lst = out.setdefault((st, grp), {})
            if lab and lab.strip():
                lst.setdefault(idx, lab.strip())
    return {k: v for k, v in out.items()}
