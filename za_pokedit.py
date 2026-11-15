"""Pokémon data editor tab: avalon/data/personal_array.bin (stats, types, abilities, moves...).

Schema: PokeDocs' Scarlet/Violet personal.fbs with evo_table corrected to a list
(tools/schemas/pokedocs/za_personal.fbs); verified on Z-A data (Bulbasaur, Pikachu...) and
re-building gives identical data.
"""
import json
import os
import threading
import tkinter as tk
import traceback
from tkinter import messagebox, ttk

import za_formats as zf
import za_modding as zm

STATS = ('HP', 'ATK', 'DEF', 'SPA', 'SPD', 'SPE')
STAT_LABELS = ('HP', 'Attack', 'Defense', 'Sp. Atk', 'Sp. Def', 'Speed')
GROWTH = ['Medium Fast', 'Erratic', 'Fluctuating', 'Medium Slow', 'Fast', 'Slow']
GENDER_GROUPS = ['Mixed', 'Male only', 'Female only', 'Genderless']   # verified: Tauros 1, Chansey 2, Magnemite 3
EVO_METHODS = {4: 'Level up', 1: 'Friendship', 8: 'Use item', 6: 'Trade', 2: 'Friendship (day)',
               3: 'Friendship (night)', 7: 'Trade with item'}


class PokemonTab:
    def __init__(self, app, parent):
        self.app = app
        self.parent = parent
        self.loaded = False
        self.data = None
        self.entries = []
        self.cur = None
        self.dirty = False
        self.names = {}
        self.build()
        app.nb.bind('<<NotebookTabChanged>>', self.on_tab, add='+')

    def paths(self):
        root = self.app.root_var.get()
        return (os.path.join(root, 'avalon', 'data', 'personal_array.bin'),
                os.path.join(zm.SCHEMAS, 'pokedocs', 'za_personal.fbs'))

    # ---------------- UI ----------------
    def build(self):
        P = self.parent
        paned = ttk.PanedWindow(P, orient='horizontal')
        paned.pack(fill='both', expand=True, padx=4, pady=4)
        left = ttk.Frame(paned)
        paned.add(left, weight=1)
        sf = ttk.Frame(left)
        sf.pack(fill='x')
        ttk.Label(sf, text='Find:').pack(side='left')
        self.q = tk.StringVar()
        e = ttk.Entry(sf, textvariable=self.q)
        e.pack(side='left', fill='x', expand=True, padx=3)
        self.q.trace_add('write', lambda *a: self.fill_list())
        self.only_za = tk.BooleanVar(value=True)
        ttk.Checkbutton(left, text='Only Pokémon in Legends: Z-A', variable=self.only_za,
                        command=self.fill_list).pack(anchor='w')
        lf = ttk.Frame(left)
        lf.pack(fill='both', expand=True, pady=(4, 0))
        self.lst = ttk.Treeview(lf, show='tree', selectmode='browse')
        sb = ttk.Scrollbar(lf, orient='vertical', command=self.lst.yview)
        self.lst.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.lst.pack(side='left', fill='both', expand=True)
        self.lst.bind('<<TreeviewSelect>>', lambda e: self.select())

        right = ttk.Frame(paned)
        paned.add(right, weight=4)
        head = ttk.Frame(right)
        head.pack(fill='x')
        self.title = tk.StringVar(value='Open this tab to load the Pokémon data…')
        ttk.Label(head, textvariable=self.title, style='Head.TLabel').pack(side='left')
        ttk.Button(head, text='Save changes to current mod', command=self.save).pack(side='right')
        self.dirty_var = tk.StringVar(value='')
        ttk.Label(head, textvariable=self.dirty_var, style='Dim.TLabel').pack(side='right', padx=8)

        body = ttk.Frame(right)
        body.pack(fill='both', expand=True, pady=4)
        g = ttk.LabelFrame(body, text='Basics')
        g.grid(row=0, column=0, sticky='nsew', padx=(0, 6))
        self.v = {}

        def combo(parent, r, label, key, width=18):
            ttk.Label(parent, text=label).grid(row=r, column=0, sticky='w', padx=4, pady=1)
            c = ttk.Combobox(parent, state='readonly', width=width)
            c.grid(row=r, column=1, sticky='w', padx=4, pady=1)
            c.bind('<<ComboboxSelected>>', lambda e: self.commit())
            self.v[key] = c
            return c

        def entry(parent, r, c, label, key, width=6):
            ttk.Label(parent, text=label).grid(row=r, column=c, sticky='w', padx=4, pady=1)
            var = tk.StringVar()
            en = ttk.Entry(parent, textvariable=var, width=width)
            en.grid(row=r, column=c + 1, sticky='w', padx=4, pady=1)
            en.bind('<FocusOut>', lambda e: self.commit())
            en.bind('<Return>', lambda e: self.commit())
            self.v[key] = var
        combo(g, 0, 'Type 1', 'type1')
        combo(g, 1, 'Type 2', 'type2')
        combo(g, 2, 'Ability 1', 'ability1')
        combo(g, 3, 'Ability 2', 'ability2')
        combo(g, 4, 'Hidden ability', 'ability3')
        combo(g, 5, 'Growth rate', 'growth')
        combo(g, 6, 'Gender', 'gender_group').configure(values=['%d %s' % x for x in enumerate(GENDER_GROUPS)])
        r = 7
        for key, label in (('rate', 'Catch rate'), ('base_exp', 'Base EXP'), ('base_friendship', 'Friendship'),
                           ('hatch_steps', 'Egg cycles'), ('gender_ratio', '% female (if mixed)'),
                           ('group_1', 'Egg group 1'), ('group_2', 'Egg group 2'), ('height', 'Height (dm)'),
                           ('weight', 'Weight (hg)')):
            entry(g, r, 0, label, key)
            r += 1
        self.v['is_present'] = tk.BooleanVar()
        ttk.Checkbutton(g, text='In Legends: Z-A', variable=self.v['is_present'],
                        command=self.commit).grid(row=r, column=0, columnspan=2, sticky='w', padx=4)

        st = ttk.LabelFrame(body, text='Base stats / EV yield')
        st.grid(row=0, column=1, sticky='nsew')
        ttk.Label(st, text='Base').grid(row=0, column=1)
        ttk.Label(st, text='EV').grid(row=0, column=3)
        for i, (k, lab) in enumerate(zip(STATS, STAT_LABELS), start=1):
            entry(st, i, 0, lab, 'st_' + k)
            var = tk.StringVar()
            en = ttk.Entry(st, textvariable=var, width=4)
            en.grid(row=i, column=3, padx=4)
            en.bind('<FocusOut>', lambda e: self.commit())
            self.v['ev_' + k] = var
        self.total = tk.StringVar()
        ttk.Label(st, textvariable=self.total, style='Dim.TLabel').grid(row=7, column=0, columnspan=4, sticky='w', padx=4)

        lf = ttk.LabelFrame(body, text='Level-up moves')
        lf.grid(row=1, column=0, sticky='nsew', pady=(6, 0), padx=(0, 6))
        self.moves = ttk.Treeview(lf, columns=('lv',), height=10, selectmode='browse')
        self.moves.heading('#0', text='Move')
        self.moves.heading('lv', text='Level')
        self.moves.column('lv', width=50, stretch=False)
        self.moves.pack(fill='both', expand=True)
        self.moves.bind('<<TreeviewSelect>>', lambda e: self.move_selected())
        mr = ttk.Frame(lf)
        mr.pack(fill='x', pady=2)
        self.move_box = ttk.Combobox(mr, width=22)
        self.move_box.pack(side='left')
        self.move_box.bind('<KeyRelease>', self._filter_moves)
        self.move_lv = tk.StringVar()
        ttk.Label(mr, text='Lv').pack(side='left', padx=(4, 1))
        ttk.Entry(mr, textvariable=self.move_lv, width=4).pack(side='left')
        ttk.Button(mr, text='Set', command=self.move_set).pack(side='left', padx=3)
        ttk.Button(mr, text='Add', command=self.move_add).pack(side='left')
        ttk.Button(mr, text='Remove', command=self.move_remove).pack(side='left', padx=3)

        of = ttk.LabelFrame(body, text='Evolutions · TM moves · Egg moves')
        of.grid(row=1, column=1, sticky='nsew', pady=(6, 0))
        self.evo_text = tk.Text(of, height=5, width=46, font=('Consolas', 9), borderwidth=0)
        self.evo_text.pack(fill='x')
        ttk.Label(of, text='One evolution per line:  method level arg species form   (method 4 = level up)',
                  style='Dim.TLabel').pack(anchor='w')
        ttk.Label(of, text='TM moves (move names or numbers, comma separated):').pack(anchor='w', pady=(6, 0))
        self.tm_text = tk.Text(of, height=4, width=46, wrap='word', font=('Consolas', 9), borderwidth=0)
        self.tm_text.pack(fill='x')
        ttk.Label(of, text='Egg moves:').pack(anchor='w', pady=(6, 0))
        self.egg_text = tk.Text(of, height=3, width=46, wrap='word', font=('Consolas', 9), borderwidth=0)
        self.egg_text.pack(fill='x')
        ttk.Button(of, text='Apply evolutions / TM / egg moves', command=self.apply_lists).pack(anchor='w', pady=4)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(1, weight=1)
        for w in (self.evo_text, self.tm_text, self.egg_text):
            w.configure(background=self.app.style.lookup('Treeview', 'fieldbackground') or '#252526',
                        foreground=self.app.style.lookup('.', 'foreground') or '#e3e3e3',
                        insertbackground=self.app.style.lookup('.', 'foreground') or '#e3e3e3')

    def on_tab(self, event=None):
        if self.app.nb.select() == str(self.parent) and not self.loaded:
            self.loaded = True
            self.load()

    # ---------------- data ----------------
    def load(self):
        src, schema = self.paths()
        m = self.app.current_mod(ask=False)
        rel = 'avalon/data/personal_array.bin'
        if m and os.path.isfile(m.path_for(rel)):
            src = m.path_for(rel)
        root = self.app.root_var.get()

        def work():
            try:
                if not os.path.isfile(schema):
                    import za_deps
                    probs = za_deps.ensure(log=lambda m: None)
                    if probs:
                        raise zm.ModError('The Pokémon data schema comes from PokeDocs and could not be '
                                          'downloaded (%s). Connect to the internet and reopen this tab, or run '
                                          '"python za_deps.py".' % '; '.join(probs))
                data = json.loads(zm.to_json(src, schema))
                names = {}
                for key, f in (('species', 'monsname'), ('type', 'typename'), ('ability', 'tokusei'), ('move', 'wazaname')):
                    p = os.path.join(root, 'ik_message', 'dat', 'English', 'common', f)
                    try:
                        lines = zf.decode_msg(open(p + '.dat', 'rb').read())
                        if key == 'species':
                            labels = zf.parse_tbl(open(p + '.tbl', 'rb').read())
                            names[key] = {int(l[9:]): t for l, t in zip(labels, lines) if l.startswith('MONSNAME_')}
                        else:
                            names[key] = dict(enumerate(lines))
                    except (OSError, ValueError):
                        names[key] = {}
                self.app.q.put(lambda: self.loaded_ok(data, names, src))
            except Exception as e:  # noqa: BLE001
                msg = '%s\n%s' % (e, traceback.format_exc()[-500:])
                self.app.q.put(lambda: self.title.set('Could not load personal_array.bin: ' + msg))
        threading.Thread(target=work, daemon=True).start()

    def loaded_ok(self, data, names, src):
        self.data, self.names = data, names
        self.entries = data.get('entries', [])
        self.title.set('%d entries loaded%s' % (len(self.entries),
                                                ' (from the current mod)' if 'romfs' in src else ''))
        self.v['type1'].configure(values=self.list_of('type'))
        self.v['type2'].configure(values=self.list_of('type'))
        for k in ('ability1', 'ability2', 'ability3'):
            self.v[k].configure(values=self.list_of('ability'))
        self.v['growth'].configure(values=['%d %s' % (i, g) for i, g in enumerate(GROWTH)])
        self.move_names = self.list_of('move')
        self.move_box.configure(values=self.move_names)
        self.fill_list()

    def list_of(self, kind):
        return ['%d %s' % (i, n) for i, n in sorted(self.names.get(kind, {}).items())]

    @staticmethod
    def num(s):
        try:
            return int(str(s).split()[0])
        except (ValueError, IndexError):
            return None

    def name_of(self, kind, i):
        return '%d %s' % (i, self.names.get(kind, {}).get(i, '?'))

    def fill_list(self):
        if not self.entries:
            return
        q = self.q.get().strip().lower()
        self.lst.delete(*self.lst.get_children())
        for i, e in enumerate(self.entries):
            sp = e.get('species', {})
            idx, form = sp.get('species_index', 0), sp.get('species_form', 0)
            if not idx or (self.only_za.get() and not e.get('is_present')):
                continue
            label = '%04d %s%s' % (idx, self.names.get('species', {}).get(idx, '?'), ' (form %d)' % form if form else '')
            if q and q not in label.lower():
                continue
            self.lst.insert('', 'end', str(i), text=label)

    def select(self):
        sel = self.lst.selection()
        if not sel:
            return
        self.commit()
        self.cur = self.entries[int(sel[0])]
        e = self.cur
        sp = e.get('species', {})
        self.title.set('%s  ·  species %d, form %d, model pm%04d' % (
            self.names.get('species', {}).get(sp.get('species_index', 0), '?'), sp.get('species_index', 0),
            sp.get('species_form', 0), sp.get('species_model', 0)))
        self._loading = True
        self.v['type1'].set(self.name_of('type', e.get('type1', 0)))
        self.v['type2'].set(self.name_of('type', e.get('type2', 0)))
        for k in ('ability1', 'ability2', 'ability3'):
            self.v[k].set(self.name_of('ability', e.get(k, 0)))
        g = e.get('growth', 0)
        self.v['growth'].set('%d %s' % (g, GROWTH[g] if g < len(GROWTH) else '?'))
        for k in ('rate', 'base_exp', 'base_friendship', 'hatch_steps', 'group_1', 'group_2'):
            self.v[k].set(e.get(k, 0))
        self.v['gender_ratio'].set(e.get('gender', {}).get('ratio', 0))
        gg = e.get('gender', {}).get('group', 0)
        self.v['gender_group'].set('%d %s' % (gg, GENDER_GROUPS[gg] if gg < len(GENDER_GROUPS) else '?'))
        self.v['height'].set(sp.get('height', 0))
        self.v['weight'].set(sp.get('weight', 0))
        self.v['is_present'].set(bool(e.get('is_present')))
        for k in STATS:
            self.v['st_' + k].set(e.get('stats', {}).get(k, 0))
            self.v['ev_' + k].set(e.get('yield_info', {}).get(k, 0))
        self.fill_moves()
        self.evo_text.delete('1.0', 'end')
        for ev in e.get('evo_table', []) or []:
            self.evo_text.insert('end', '%d %d %d %d %d    # %s → %s\n' % (
                ev.get('method', 0), ev.get('level', 0), ev.get('arg', 0), ev.get('species', 0), ev.get('form', 0),
                EVO_METHODS.get(ev.get('method', 0), 'method %d' % ev.get('method', 0)),
                self.names.get('species', {}).get(ev.get('species', 0), '?')))
        self.tm_text.delete('1.0', 'end')
        self.tm_text.insert('1.0', ', '.join(self.names.get('move', {}).get(m, str(m)) for m in e.get('technical_machines', []) or []))
        self.egg_text.delete('1.0', 'end')
        self.egg_text.insert('1.0', ', '.join(self.names.get('move', {}).get(m, str(m)) for m in e.get('egg_moves', []) or []))
        self._loading = False
        self.update_total()

    def update_total(self):
        if self.cur:
            self.total.set('Total: %d' % sum(self.cur.get('stats', {}).get(k, 0) for k in STATS))

    def mark(self):
        self.dirty = True
        self.dirty_var.set('unsaved changes')

    def commit(self):
        """Write the form back into the current entry."""
        if not self.cur or getattr(self, '_loading', False):
            return
        e = self.cur
        before = json.dumps(e, sort_keys=True)
        for k in ('type1', 'type2', 'ability1', 'ability2', 'ability3', 'growth'):
            n = self.num(self.v[k].get())
            if n is not None:
                e[k] = n
        for k in ('rate', 'base_exp', 'base_friendship', 'hatch_steps', 'group_1', 'group_2'):
            n = self.num(self.v[k].get())
            if n is not None:
                e[k] = n
        n = self.num(self.v['gender_ratio'].get())
        if n is not None:
            e.setdefault('gender', {})['ratio'] = max(0, min(100, n))
        n = self.num(self.v['gender_group'].get())
        if n is not None:
            e.setdefault('gender', {})['group'] = n
        sp = e.setdefault('species', {})
        for k in ('height', 'weight'):
            n = self.num(self.v[k].get())
            if n is not None:
                sp[k] = n
        e['is_present'] = bool(self.v['is_present'].get())
        for k in STATS:
            n = self.num(self.v['st_' + k].get())
            if n is not None:
                e.setdefault('stats', {})[k] = max(0, min(255, n))
            n = self.num(self.v['ev_' + k].get())
            if n is not None:
                e.setdefault('yield_info', {})[k] = max(0, min(3, n))
        if json.dumps(e, sort_keys=True) != before:
            self.mark()
        self.update_total()

    # moves
    def fill_moves(self):
        self.moves.delete(*self.moves.get_children())
        for k, mv in enumerate(self.cur.get('learnset', []) or []):
            self.moves.insert('', 'end', str(k), text=self.name_of('move', mv.get('move', 0)), values=(mv.get('level', 0),))

    def _filter_moves(self, e):
        q = self.move_box.get().lower()
        self.move_box.configure(values=[n for n in self.move_names if q in n.lower()][:400] if q else self.move_names)

    def move_selected(self):
        sel = self.moves.selection()
        if sel and self.cur:
            mv = self.cur['learnset'][int(sel[0])]
            self.move_box.set(self.name_of('move', mv.get('move', 0)))
            self.move_lv.set(mv.get('level', 0))

    def _move_from_box(self):
        n = self.num(self.move_box.get())
        if n is not None:
            return n
        t = self.move_box.get().strip().lower()
        return next((i for i, nm in self.names.get('move', {}).items() if nm.lower() == t), None)

    def move_set(self):
        sel = self.moves.selection()
        mv, lv = self._move_from_box(), self.num(self.move_lv.get())
        if not (self.cur and sel and mv is not None and lv is not None):
            return
        self.cur['learnset'][int(sel[0])] = {'move': mv, 'level': lv}
        self._sort_moves()

    def move_add(self):
        mv, lv = self._move_from_box(), self.num(self.move_lv.get())
        if not self.cur or mv is None or lv is None:
            messagebox.showinfo('Pokémon data', 'Pick a move and a level first.')
            return
        self.cur.setdefault('learnset', []).append({'move': mv, 'level': lv})
        self._sort_moves()

    def move_remove(self):
        sel = self.moves.selection()
        if self.cur and sel:
            del self.cur['learnset'][int(sel[0])]
            self._sort_moves()

    def _sort_moves(self):
        self.cur['learnset'].sort(key=lambda m: m.get('level', 0))
        self.fill_moves()
        self.mark()

    def _parse_moves(self, text):
        out = []
        lookup = {n.lower(): i for i, n in self.names.get('move', {}).items()}
        for part in text.replace('\n', ',').split(','):
            p = part.strip()
            if not p:
                continue
            if p.isdigit():
                out.append(int(p))
            elif p.lower() in lookup:
                out.append(lookup[p.lower()])
            else:
                raise ValueError('Unknown move "%s"' % p)
        return out

    def apply_lists(self):
        if not self.cur:
            return
        try:
            evos = []
            for line in self.evo_text.get('1.0', 'end').splitlines():
                line = line.split('#')[0].strip()
                if not line:
                    continue
                a = [int(x) for x in line.split()]
                if len(a) != 5:
                    raise ValueError('Evolution lines need 5 numbers: method level arg species form')
                evos.append({'method': a[0], 'level': a[1], 'arg': a[2], 'species': a[3], 'form': a[4]})
            tms = self._parse_moves(self.tm_text.get('1.0', 'end'))
            eggs = self._parse_moves(self.egg_text.get('1.0', 'end'))
        except ValueError as e:
            messagebox.showerror('Pokémon data', str(e))
            return
        self.cur['evo_table'] = evos
        self.cur['technical_machines'] = tms
        self.cur['egg_moves'] = eggs
        self.mark()
        self.app.status_var.set('Applied evolutions, %d TM moves and %d egg moves' % (len(tms), len(eggs)))

    def save(self):
        if self.data is None:
            return
        self.commit()
        m = self.app.current_mod()
        if not m:
            return
        src, schema = self.paths()
        rel = 'avalon/data/personal_array.bin'

        def job():
            zm.from_json(json.dumps(self.data), schema, m.path_for(rel))
            return rel

        def done(r):
            self.dirty = False
            self.dirty_var.set('saved into "%s"' % m.name)
            self.app.refresh_mods()
        self.app.run_bg('Saving Pokémon data', job, done)
