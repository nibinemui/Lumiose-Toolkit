"""Encounter overview: every Pokémon the game's spawners bring, in one sortable, searchable list.

Each spawner row of pokemon_spawner_data_array names its spawn points (appearanceSpawnerObjectInfoList) and the
encounters it picks from by weight (encountDataInfoList -> encount_data_array: species, form, levels, alpha chance).
The list shows one row per spawner encounter, or - combined - one row per Pokémon, place and settings with the
number of spawn points. Levels, weight and alpha chance are edited in the Map tab's own tables, so they are saved
with the Map tab's "Save changes to current mod" and the two never write different copies.
"""
import copy
import os
import re
import tkinter as tk
from tkinter import ttk, messagebox

import za_formats as zf
import za_modding as zm

TYPES = ['Normal', 'Fighting', 'Flying', 'Poison', 'Ground', 'Rock', 'Bug', 'Ghost', 'Steel', 'Fire', 'Water',
         'Grass', 'Electric', 'Psychic', 'Ice', 'Dragon', 'Dark', 'Fairy']
CATEGORIES = ('Wild Zones', 'City streets', 'Lysandre Labs and Sewers', 'Hyperspace', 'Battles and story')
ALL = 'Everything'
WEATHER_MARK = {1: 'clear weather', 2: 'rain'}       # appearedWeatherCondition (2 is the Goomy line: rain)

COLUMNS = (   # key, heading, width, stretch
    ('mon', 'Pokémon', 130, True), ('no', '#', 44, False), ('form', 'Form', 40, False),
    ('where', 'Where', 200, True), ('pts', 'Points', 50, False), ('lv', 'Levels', 70, False),
    ('w', 'Weight', 54, False), ('ch', 'Chance', 58, False), ('alpha', 'Alpha', 54, False),
    ('when', 'When', 70, False), ('cond', 'Conditions', 150, True), ('point', 'Spawn point', 190, True))
EDITABLE = {'lv', 'w', 'alpha'}


def _rows(table):
    for g in (table.data.get('values') or []) if table else []:
        for r in g.get('root') or []:
            yield r


def _params(cond):
    for el in cond or []:
        for x in el.get('element') or []:
            for p in x.get('param') or []:
                yield p


def place_names(root):
    base = os.path.join(root, 'ik_message', 'dat', 'English', 'common', 'place_name')
    try:
        return dict(zip(zf.parse_tbl(open(base + '.tbl', 'rb').read()), zf.decode_msg(open(base + '.dat', 'rb').read())))
    except (OSError, ValueError):
        return {}


def hyperspace_spawners(mt):
    """{spawner id: [(type, rank)]} for the spawners Hyperspace Lumiose's zone types and ranks use."""
    t = mt.tables.get('dim_wild')
    out = {}
    for g in (t.data.get('values') or []) if t else []:
        for row in (g.get('root') if isinstance(g, dict) and 'root' in g else [g]):
            for ri in row.get('rankItems') or []:
                for p in ri.get('patternItems') or []:
                    for s in p.get('spawnerItems') or []:
                        k = (row.get('pokeType', 0), ri.get('rank', 0))
                        lst = out.setdefault(s.get('spawnerDataId'), [])
                        if k not in lst:
                            lst.append(k)
    return out


def _type(n):
    try:
        return TYPES[int(n)]
    except (ValueError, IndexError, TypeError):
        return 'type %s' % n


class Places:
    """Where a spawner's points are: (category, plain name)."""

    def __init__(self, mt):
        root = os.path.normpath(mt.app.root_var.get())
        cached = mt.__dict__.get('_place_names')
        if not cached or cached[0] != root:
            cached = mt.__dict__['_place_names'] = (root, place_names(root))
        self.lab = cached[1]
        self.hyper = hyperspace_spawners(mt)

    def district(self, code):        # a0201 -> Bleu District
        return self.lab.get('PLACENAME_A%s' % code[1:3]) or 'District %s' % code[1:3]

    def of(self, s, o):
        name = o.get('objectName') or ''
        zone = (o.get('zoneInfo') or {}).get('zoneId')
        m = re.match(r'spn_(a\d{4}_w\d\d)_', name)
        zone = zone or (m.group(1) if m else None)
        if zone:
            return CATEGORIES[0], self.lab.get('PLACENAME_wild_' + zone) or 'Wild Zone %s' % zone
        m = re.match(r'spn_outzone_(a\d{4})', name)
        if m:
            d = self.district(m.group(1))
            return CATEGORIES[1], d if d.endswith('Plaza') else d + ' streets'
        dn = o.get('dungeonName') or ''
        scene = o.get('createScenePath') or ''
        for code, extra in (('t3_2', ' (2)'), ('t3', ''), ('t2', '')):
            if dn == code or scene.startswith(code + '_'):
                return CATEGORIES[2], (self.lab.get('PLACENAME_' + code[:2]) or code) + extra
        hy = self.hyper.get(s.get('id'))
        if hy:
            text = ', '.join('%s r%d' % (_type(t), r + 1) for t, r in sorted(hy)[:3]) + (' …' if len(hy) > 3 else '')
            return CATEGORIES[3], '%s: %s' % (self.lab.get('PLACENAME_dim_1') or 'Hyperspace Lumiose', text)
        m = re.match(r'zdm_random_t(\d\d)_r(\d\d)_', name)
        if m:                         # named by type and rank, but in none of the spawn patterns
            return CATEGORIES[3], '%s: %s r%d (in no spawn pattern)' % (
                self.lab.get('PLACENAME_dim_1') or 'Hyperspace Lumiose', _type(m.group(1)), int(m.group(2)))
        m = re.search(r'zdm(\d{3})', name)
        if m:
            return CATEGORIES[3], 'Hyperspace area zdm%s' % m.group(1)
        if 'boss' in name:
            return CATEGORIES[4], 'Boss battle'
        if re.search(r'subq|sub\d|chapter|_ev_|izigen|rest\d|rom_|dlc_', name):
            return CATEGORIES[4], 'Story / side mission'
        m = re.search(r'(?:^|_)(t\d(?:_\d\d)?|d\d\d)(?=_)', name)
        return CATEGORIES[4], 'Area %s' % (m.group(1) if m else 'other')


def when_and_conditions(s, info, e=None):
    """('Day' / 'Night' / 'Any time', conditions text) of one spawner encounter (spawner, its info, the encounter)."""
    when, notes = set(), []
    t = info.get('appearedTimeCondition')
    if t in (1, 2):
        when.add('Day' if t == 1 else 'Night')
    w = info.get('appearedWeatherCondition')
    if w:
        notes.append(WEATHER_MARK.get(w, 'weather %s' % w))
    story, hyper, weather, other = [], [], [], []
    for p in list(_params(s.get('activationCondition'))) + list(_params(info.get('activationCondition'))) +             list(_params((e or {}).get('activationCondition'))):
        c, args = p.get('condition'), p.get('param') or []
        if c == 'time_noon_condition':
            when.add('Day')
        elif c == 'time_night_condition':
            when.add('Night')
        elif c == 'time_current_condition' and len(args) == 2:
            when.add('%s:00-%s:00' % tuple(args))
        elif c == 'weather_condition' and args:
            weather.append(('not ' if p.get('op') == 2 else '') + args[0].replace('_', ' '))
        elif c == 'phase_condition':
            if all(str(x) == '0' for x in args) and args:
                continue                                 # from the start of the game: always true
            story.append('story phase %s' % '/'.join(map(str, args)) if args else 'story progress')
        elif c == 'flag_condition':
            story.append('story flag')
        elif c == 'work_condition':
            story.append('quest progress')
        elif c == 'another_dimension_poke_type_condition' and args:
            hyper.append('%s type' % _type(args[0]))
        elif c == 'another_dimension_rank_condition' and args:
            hyper.append('rank %s' % args[0])
        elif c == 'another_dimension_wild_poke_pattern_condition' and len(args) >= 2:
            hyper.append('pattern %s' % args[1])
        elif c and c != 'force_condition':
            other.append(' '.join([c.replace('_condition', '').replace('_', ' ')] + [str(a) for a in args]))
    if weather:
        notes.append('weather: ' + ', '.join(dict.fromkeys(weather)))
    if hyper:
        notes.append(', '.join(dict.fromkeys(hyper)))
    notes += list(dict.fromkeys(story))
    notes += list(dict.fromkeys(other))
    return ('/'.join(sorted(when)) if when else 'Any time'), '; '.join(notes)


def _gate(s, info):
    """What decides whether an encounter of a spawner can be picked at a given moment (empty: always)."""
    return (info.get('appearedTimeCondition'), info.get('appearedWeatherCondition'),
            repr(info.get('activationCondition')) if any(
                p.get('condition') != 'force_condition' for p in _params(info.get('activationCondition'))) else '')


_NONE = (None, None, '')


def build_slots(mt, places):
    """One dict per spawner encounter (spawner row x encounter info)."""
    sp = mt.tables.get('spawner')
    out = []
    for s in _rows(sp):
        infos = s.get('encountDataInfoList') or []
        objs = s.get('appearanceSpawnerObjectInfoList') or []
        if not objs:
            continue
        cat, where = places.of(s, objs[0])
        gates = [_gate(s, i) for i in infos]
        for info, gate in zip(infos, gates):
            e = mt.enc_by_id.get(info.get('encountDataId'))
            if e is None:
                continue
            total = sum(i.get('weight') or 0 for i, g in zip(infos, gates) if g == gate or (g == _NONE and gate != _NONE))
            wt = info.get('weight') or 0
            when, cond = when_and_conditions(s, info, e)
            alpha = float(e.get('oyabunProbability') or 0)
            out.append({
                'dev': e.get('devNo', 0), 'form': e.get('formNo') or 0, 'cat': cat, 'where': where,
                'points': [o.get('objectName') or '' for o in objs], 'lo': e.get('minLevel', 0),
                'hi': e.get('maxLevel', 0), 'add': info.get('additionalLevel') or 0, 'w': wt,
                'ch': 100.0 * wt / total if total else 0.0, 'alpha': alpha, 'when': when, 'cond': cond,
                'slots': [(s, info)]})
    return out


def combine(slots):
    groups = {}
    for r in slots:
        k = (r['dev'], r['form'], r['cat'], r['where'], r['lo'], r['hi'], r['add'], r['w'], round(r['ch'], 1),
             r['alpha'], r['when'], r['cond'])
        g = groups.get(k)
        if g is None:
            groups[k] = dict(r, points=list(r['points']), slots=list(r['slots']))
        else:
            g['points'] += [p for p in r['points'] if p not in g['points']]
            g['slots'] += r['slots']
    return list(groups.values())


def _alpha_text(a):
    return 'Alpha' if a >= 100 else ('%g%%' % a if a else '')


def _levels_text(r):
    return ('%d' % r['lo'] if r['lo'] == r['hi'] else '%d-%d' % (r['lo'], r['hi'])) + \
        (' +%d' % r['add'] if r['add'] else '')


def parse_levels(text):
    """'5-10', '5 - 10', '7' -> (lo, hi); raises ValueError."""
    nums = [int(x) for x in re.findall(r'\d+', text)]
    if not nums or len(nums) > 2 or re.search(r'[^\d\s\-–to]', text):
        raise ValueError('Levels are one number or two, like 5-10.')
    lo, hi = min(nums), max(nums)
    if lo < 1 or hi > 100:
        raise ValueError('Levels go from 1 to 100.')
    return lo, hi


class Overview:
    def __init__(self, mt):
        self.mt = mt
        app = self.app = mt.app
        w = self.w = tk.Toplevel(app)
        w.title('Encounter overview')
        w.transient(app)
        w.geometry('1320x760')
        bg = ttk.Style(w).lookup('TFrame', 'background') or None
        if bg:
            w.configure(background=bg)
        f = ttk.Frame(w, padding=10)
        f.pack(fill='both', expand=True)
        ttk.Label(f, text=zm.EXPERIMENTAL, style='Warn.TLabel', wraplength=1200, justify='left').pack(anchor='w')
        ttk.Label(f, style='Dim.TLabel', wraplength=1200, justify='left', text=(
            'Every Pokémon the game\'s spawners bring. Click a column heading to sort, type in Search to filter (every '
            'word must match; a number only matches whole numbers - try "wild zone 1", "pikachu night", "alpha"). '
            'Double-click Levels, Weight or Alpha to change it there, or select rows and use the '
            'fields below; double-click anywhere else to show the spawn point on the map (again for the next one). '
            'Chance: how often the Pokémon is picked at its spawn point among those that can appear at the same time; '
            '"+N" after the levels: levels the spawner adds. '
            'Changes go into the Map tab and are saved with its "Save changes to current mod".')).pack(
            anchor='w', pady=(2, 8))
        top = ttk.Frame(f)
        top.pack(fill='x')
        ttk.Label(top, text='Search:').pack(side='left')
        self.q = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.q, width=34)
        e.pack(side='left', padx=(4, 12))
        e.focus_set()
        ttk.Label(top, text='Show:').pack(side='left')
        self.cat = tk.StringVar(value=ALL)
        ttk.Combobox(top, textvariable=self.cat, values=(ALL,) + CATEGORIES, state='readonly', width=24).pack(
            side='left', padx=(4, 12))
        self.combined = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text='One row per Pokémon and place (count the spawn points)', variable=self.combined,
                        command=self.refresh).pack(side='left')
        self.count = tk.StringVar()
        ttk.Label(top, textvariable=self.count, style='Dim.TLabel').pack(side='right')

        tf = ttk.Frame(f)
        tf.pack(fill='both', expand=True, pady=(6, 0))
        self.tree = t = ttk.Treeview(tf, columns=[c[0] for c in COLUMNS], show='headings', selectmode='extended')
        for key, head, wd, st in COLUMNS:
            t.heading(key, text=head, command=lambda k=key: self.sort_by(k))
            t.column(key, width=wd, stretch=st, anchor='w' if st else 'center')
        ys = ttk.Scrollbar(tf, orient='vertical', command=t.yview)
        t.configure(yscrollcommand=ys.set)
        ys.pack(side='right', fill='y')
        t.pack(side='left', fill='both', expand=True)

        ed = ttk.LabelFrame(f, text='Selected rows', padding=6)
        ed.pack(fill='x', pady=(8, 0))
        self.lo, self.hi, self.wt, self.al = tk.StringVar(), tk.StringVar(), tk.StringVar(), tk.StringVar()
        for col, (lab, var, wd) in enumerate((('Levels', self.lo, 5), ('to', self.hi, 5), ('Weight', self.wt, 6),
                                               ('Alpha chance %', self.al, 6))):
            ttk.Label(ed, text=lab).grid(row=0, column=2 * col, sticky='e', padx=(10 if col else 0, 3))
            ttk.Entry(ed, textvariable=var, width=wd).grid(row=0, column=2 * col + 1, sticky='w')
        ttk.Button(ed, text='Apply to selected', command=self.apply_fields).grid(row=0, column=8, padx=(12, 0))
        ttk.Button(ed, text='Show on map', command=self.show_on_map).grid(row=0, column=9, padx=(6, 0))
        self.note = tk.StringVar()
        ttk.Label(ed, textvariable=self.note, style='Dim.TLabel', wraplength=1200, justify='left').grid(
            row=1, column=0, columnspan=12, sticky='w', pady=(4, 0))
        ttk.Label(ed, text='Empty fields are left as they are. Levels apply to every spawn point that shares the '
                  'encounter - you are asked first when others share it.', style='Dim.TLabel').grid(
            row=2, column=0, columnspan=12, sticky='w')
        bar = ttk.Frame(f)
        bar.pack(fill='x', pady=(8, 0))
        ttk.Label(bar, textvariable=mt.dirty_var, style='Dim.TLabel').pack(side='left')
        ttk.Button(bar, text='Close', command=self.close).pack(side='right')
        ttk.Button(bar, text='Save changes to current mod', command=mt.save_to_mod).pack(side='right', padx=6)

        self.sort = ('where', False)
        self.rows, self.shown, self.cycle = [], [], {}
        self.version = None
        self.editor = None
        self._after = None
        t.bind('<<TreeviewSelect>>', lambda e: self.on_select())
        t.bind('<Double-1>', self.on_double)
        t.bind('<Return>', lambda e: self.show_on_map())
        self.q.trace_add('write', lambda *a: self.filter_soon())
        self.cat.trace_add('write', lambda *a: self.fill())
        w.bind('<FocusIn>', self.on_focus)
        w.bind('<Escape>', lambda e: self.close() if self.editor is None else None)
        w.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh()

    # ---------------- data ----------------
    def refresh(self, keep=None):
        """Read the rows again from the Map tab's tables (keep: infos whose rows stay selected)."""
        self.version = getattr(self.mt, 'spawn_version', None)
        places = Places(self.mt)
        self.map_points = set()
        for L in self.mt.layers:
            if L.stem == 'pokemon_spawner_transform_array':
                self.map_points |= {p.get('Name') for _, _, p in L.points()}
        slots = build_slots(self.mt, places)
        self.rows = combine(slots) if self.combined.get() else slots
        names = self.mt.species
        for r in self.rows:
            r['name'] = names.get(r['dev'], '#%d' % r['dev'])
            pts = r['points']
            first = next((p for p in pts if p in self.map_points), pts[0] if pts else '')
            r['point'] = first + (' (+%d more)' % (len(pts) - 1) if len(pts) > 1 else '') + \
                ('' if first in self.map_points else '  (not on the map)')
            r['search'] = ' '.join(str(x) for x in (
                r['name'], r['dev'], 'form %d' % r['form'] if r['form'] else '', r['cat'], r['where'],
                _levels_text(r), 'alpha %g' % r['alpha'] if r['alpha'] else '', r['when'], r['cond'],
                ' '.join(pts), ' '.join(i.get('encountDataId', '') for _, i in r['slots']))).lower()
            r['nums'] = set(re.findall(r'\d+', r['search']))
        self.fill(keep)

    def sort_key(self, key):
        return {
            'mon': lambda r: (r['name'].lower(), r['form']), 'no': lambda r: (r['dev'], r['form']),
            'form': lambda r: (r['form'], r['dev']), 'where': lambda r: (CATEGORIES.index(r['cat']), _natural(r['where']), r['name']),
            'pts': lambda r: len(r['points']), 'lv': lambda r: (r['lo'], r['hi'], r['add']), 'w': lambda r: r['w'],
            'ch': lambda r: r['ch'], 'alpha': lambda r: r['alpha'], 'when': lambda r: r['when'],
            'cond': lambda r: r['cond'], 'point': lambda r: r['point']}[key]

    def sort_by(self, key):
        self.sort = (key, not self.sort[1] if self.sort[0] == key else False)
        self.fill(self.selected_infos())

    def filter_soon(self):
        if self._after:
            self.w.after_cancel(self._after)
        self._after = self.w.after(200, self.fill)

    def fill(self, keep=None):
        self._after = None
        self.close_editor()
        t = self.tree
        cat = self.cat.get()
        test = _matcher(self.q.get())
        rows = [r for r in self.rows if (cat == ALL or r['cat'] == cat) and test(r)]
        key, rev = self.sort
        rows.sort(key=self.sort_key(key), reverse=rev)
        for k, head, _, _ in COLUMNS:
            t.heading(k, text=head + (' ▼' if rev else ' ▲') if k == key else head)
        t.delete(*t.get_children())
        self.shown = rows
        self.cycle = {}
        sel = []
        for n, r in enumerate(rows):
            iid = str(n)
            t.insert('', 'end', iid, values=(
                r['name'], r['dev'], r['form'] or '', r['where'], len(r['points']), _levels_text(r), r['w'],
                '%.0f%%' % r['ch'] if r['ch'] >= 1 or not r['ch'] else '%.1f%%' % r['ch'], _alpha_text(r['alpha']),
                r['when'], r['cond'], r['point']))
            if keep and any(id(i) in keep for _, i in r['slots']):
                sel.append(iid)
        if sel:
            t.selection_set(sel)
            t.see(sel[0])
        n_pts = len({p for r in rows for p in r['points']})
        self.count.set('%d row(s), %d spawn point(s)%s' % (len(rows), n_pts,
                                                          '' if len(rows) == len(self.rows) else ' (filtered)'))

    def selected(self):
        return [self.shown[int(i)] for i in self.tree.selection() if int(i) < len(self.shown)]

    def selected_infos(self):
        return {id(i) for r in self.selected() for _, i in r['slots']}

    def on_focus(self, e):
        if getattr(self.mt, 'spawn_version', None) != self.version and self.editor is None:
            self.refresh(self.selected_infos())        # the Map tab (or Hyperspace window) changed something

    def on_select(self):
        rows = self.selected()
        if not rows:
            return

        def same(f):
            vals = {f(r) for r in rows}
            return str(vals.pop()) if len(vals) == 1 else ''
        self.lo.set(same(lambda r: r['lo']))
        self.hi.set(same(lambda r: r['hi']))
        self.wt.set(same(lambda r: r['w']))
        self.al.set(same(lambda r: '%g' % r['alpha']))
        users, own = self.mt.enc_users, {}
        for r in rows:
            for _, i in r['slots']:
                own[i.get('encountDataId')] = own.get(i.get('encountDataId'), 0) + 1
        shared = sum(1 for k, n in own.items() if users.get(k, 0) > n)
        ids = sorted(own)
        self.note.set('%d spawn point(s), encounter %s%s' % (
            len({p for r in rows for p in r['points']}), ', '.join(ids[:4]) + (' …' if len(ids) > 4 else ''),
            ' - %d of them shared with other spawn points' % shared if shared else ''))

    # ---------------- map ----------------
    def show_on_map(self, row=None, iid=None):
        rows = [row] if row else self.selected()
        if not rows:
            return
        r = rows[0]
        pts = [p for p in r['points'] if p in self.map_points]
        if not pts:
            self.app.status_var.set('%s is not placed on the map (no position in the Pokémon spawn layer).'
                                    % ', '.join(r['points'][:3]))
            self.note.set('Not on the map: this spawner is placed by an event or by Hyperspace.')
            return
        iid = iid or self.tree.selection()[0]
        k = self.cycle.get(iid, -1) + 1
        k = k if k < len(pts) else 0
        self.cycle[iid] = k
        mt = self.mt
        L = next(L for L in mt.layers if L.stem == 'pokemon_spawner_transform_array')
        hit = next(((gi, si, s) for gi, si, s in L.points() if s.get('Name') == pts[k]), None)
        if hit is None:
            return
        gi, si, s = hit
        mt.sel = (L, gi, si)
        if L.var and not L.var.get():
            L.var.set(True)
        import za_map
        x, y, z = za_map._pos(s)
        if not mt.in_area(pts[k], x, z):
            code = mt.point_area.get(pts[k])
            label = next((a for a, v in mt.area_labels.items() if v == (code or za_map.CITY)), za_map.CITY)
            mt.area_var.set(label)
            mt.set_area(label, keep_sel=True)
        self.app.nb.select(mt.parent)
        mt.show_sel()
        mt.zoom_to_sel()
        self.app.status_var.set('Spawn point %s (%d of %d) - %s' % (pts[k], k + 1, len(pts), r['where']))

    # ---------------- editing ----------------
    def on_double(self, e):
        if self.tree.identify_region(e.x, e.y) != 'cell':
            return
        iid = self.tree.identify_row(e.y)
        col = self.tree.identify_column(e.x)
        if not iid:
            return
        key = COLUMNS[int(col[1:]) - 1][0]
        row = self.shown[int(iid)]
        if key in EDITABLE:
            self.open_editor(iid, col, key, row)
        else:
            self.show_on_map(row, iid)
        return 'break'

    def open_editor(self, iid, col, key, row):
        self.close_editor()
        bbox = self.tree.bbox(iid, col)
        if not bbox:
            return
        x, y, wd, ht = bbox
        var = tk.StringVar(value={'lv': '%d-%d' % (row['lo'], row['hi']), 'w': str(row['w']),
                                  'alpha': '%g' % row['alpha']}[key])
        ent = ttk.Entry(self.tree, textvariable=var)
        ent.place(x=x, y=y, width=max(wd, 70), height=ht)
        ent.focus_set()
        ent.select_range(0, 'end')
        self.editor = ent

        def commit(_e=None):
            if self.editor is not ent:
                return
            text = var.get().strip()
            self.close_editor()
            try:
                if key == 'lv':
                    lo, hi = parse_levels(text)
                    self.apply([row], lo=lo, hi=hi)
                elif key == 'w':
                    self.apply([row], weight=_int(text, 'Weight'))
                else:
                    self.apply([row], alpha=_pct(text))
            except ValueError as ex:
                messagebox.showerror('Encounter overview', str(ex), parent=self.w)
        ent.bind('<Return>', commit)
        ent.bind('<KP_Enter>', commit)
        ent.bind('<FocusOut>', commit)
        ent.bind('<Escape>', lambda e: (self.close_editor(), 'break')[1])

    def close_editor(self):
        if self.editor is not None:
            ent, self.editor = self.editor, None
            ent.destroy()
            self.tree.focus_set()

    def apply_fields(self):
        rows = self.selected()
        if not rows:
            self.note.set('Select one or more rows first.')
            return
        try:
            lo = _int(self.lo.get(), 'Levels') if self.lo.get().strip() else None
            hi = _int(self.hi.get(), 'Levels') if self.hi.get().strip() else None
            if lo is not None and hi is None or hi is not None and lo is None:
                raise ValueError('Fill in both levels (or neither).')
            if lo is not None:
                lo, hi = parse_levels('%d-%d' % (lo, hi))
            wt = _int(self.wt.get(), 'Weight') if self.wt.get().strip() else None
            al = _pct(self.al.get()) if self.al.get().strip() else None
        except ValueError as ex:
            messagebox.showerror('Encounter overview', str(ex), parent=self.w)
            return
        if lo is None and wt is None and al is None:
            return
        self.apply(rows, lo=lo, hi=hi, weight=wt, alpha=al)

    def apply(self, rows, lo=None, hi=None, weight=None, alpha=None):
        """Change levels / alpha chance (in the encounters) and weight (in the spawners) of these rows."""
        mt = self.mt
        if getattr(mt, 'spawn_version', None) != self.version:      # the Map tab read its tables again meanwhile
            self.refresh()
            self.note.set('The Map tab changed or reloaded its tables - the list was read again. Please try again.')
            return
        slots = [(s, i) for r in rows for s, i in r['slots']]
        enc_change = lo is not None or alpha is not None
        if enc_change:
            mine = {}
            for _, i in slots:
                mine[i.get('encountDataId')] = mine.get(i.get('encountDataId'), 0) + 1
            shared = {k: mt.enc_users.get(k, 0) - n for k, n in mine.items() if mt.enc_users.get(k, 0) > n}
            if shared:
                ans = messagebox.askyesnocancel('Encounter overview', (
                    '%d of these encounters are also used by %d other spawn point(s) not in your selection.\n\n'
                    'Yes: change them there too\nNo: give the selected spawn points their own copy (only they change)')
                    % (len(shared), sum(shared.values())), parent=self.w)
                if ans is None:
                    return
                if ans is False:
                    copies = {}
                    for _, i in slots:
                        k = i.get('encountDataId')
                        if k in shared:
                            if k not in copies:
                                copies[k] = _own_copy(mt, k)
                            i['encountDataId'] = copies[k]['id']
                    mt.tables['spawner'].dirty = True
        changed = 0
        done = set()
        for s, i in slots:
            if weight is not None and i.get('weight') != weight:
                i['weight'] = weight
                mt.tables['spawner'].dirty = True
                changed += 1
            e = mt.enc_by_id.get(i.get('encountDataId'))
            if not enc_change or e is None or id(e) in done:
                continue
            done.add(id(e))
            if lo is not None and (e.get('minLevel'), e.get('maxLevel')) != (lo, hi):
                e['minLevel'], e['maxLevel'] = lo, hi
                mt.tables['enc'].dirty = True
                changed += 1
            if alpha is not None and float(e.get('oyabunProbability') or 0) != alpha:
                e['oyabunProbability'] = alpha
                if alpha and not e.get('oyabunAdditionalLevel'):
                    e['oyabunAdditionalLevel'] = 10       # as the game's own alpha encounters
                mt.tables['enc'].dirty = True
                changed += 1
        keep = {id(i) for _, i in slots}
        mt.index_spawn_tables()
        mt._after_encounter_edit(None)               # the Map tab's texts, its selected point and "not saved yet"
        self.refresh(keep)
        self.note.set('Changed %d value(s) at %d spawn point(s) - not saved yet.' % (
            changed, len({p for r in rows for p in r['points']})) if changed else 'Nothing changed.')

    def close(self):
        self.close_editor()
        self.mt.__dict__.pop('_overview', None)
        self.w.destroy()


def _matcher(query):
    """A test for rows: every word must be in the row; a number only matches a whole number, and a number right
    after a word goes with it ("wild zone 1" is Wild Zone 1, not 15 - and not a 1% alpha chance)."""
    words = query.lower().split()
    tests = []
    for k, x in enumerate(words):
        if not x.isdigit():
            tests.append(lambda r, x=x: x in r['search'])
        elif k and not words[k - 1].isdigit():
            pat = re.compile(r'(?<!\w)%s %s(?!\d)' % (re.escape(words[k - 1]), x))
            tests.append(lambda r, pat=pat: pat.search(r['search']) is not None)
        else:
            tests.append(lambda r, x=x: x in r['nums'])
    return lambda r: all(t(r) for t in tests)


def _natural(s):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r'(\d+)', s)]


def _int(text, what):
    try:
        v = int(text.strip())
    except ValueError:
        raise ValueError('%s must be a whole number.' % what) from None
    if v < 0:
        raise ValueError('%s can\'t be negative.' % what)
    return v


def _pct(text):
    try:
        v = float(text.strip().rstrip('%'))
    except ValueError:
        raise ValueError('Alpha chance is a number from 0 to 100.') from None
    if not 0 <= v <= 100:
        raise ValueError('Alpha chance is a number from 0 to 100.')
    return v


def _own_copy(mt, enc_id):
    """A copy of encounter `enc_id` with a new id, next to it in the encounter table."""
    e0 = mt.enc_by_id[enc_id]
    e = copy.deepcopy(e0)
    base = (enc_id or 'ect_custom') + '_mod'
    k = 1
    while '%s%d' % (base, k) in mt.enc_by_id:
        k += 1
    e['id'] = '%s%d' % (base, k)
    groups = mt.tables['enc'].data['values']
    g = next((g for g in groups if e0 in (g.get('root') or [])), groups[0])
    g.setdefault('root', []).append(e)
    mt.enc_by_id[e['id']] = e
    mt.tables['enc'].dirty = True
    return e


def open_window(mt):
    """The encounter overview of the Map tab's loaded tables (one window at a time)."""
    if not mt.tables.get('spawner') or not mt.tables.get('enc'):
        messagebox.showinfo('Encounter overview', 'The spawner and encounter tables could not be read.')
        return None
    ov = mt.__dict__.get('_overview')
    if ov is not None and ov.w.winfo_exists():
        ov.w.deiconify()
        ov.w.lift()
        ov.w.focus_force()
        return ov
    ov = mt.__dict__['_overview'] = Overview(mt)
    return ov
