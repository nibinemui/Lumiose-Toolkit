"""Hyperspace spawns: which Pokémon appear in Hyperspace Lumiose, by the zone's type and the player's rank.

The game picks a Hyperspace zone's type (one of the 18) and uses the player's rank (1-5). For each type and rank,
dimension_wild_pokemon_array lists spawn patterns (one is picked by weight); each pattern names Pokémon spawners
(rows of pokemon_spawner_data_array, 'floating' or 'on the ground' spots), and each spawner names encounters
(species, levels, alpha chance) in encount_data_array. This window edits those encounters through the Map tab's
tables, so they are saved with the Map tab's "Save changes to current mod".
"""
import copy
import re
import tkinter as tk
from tkinter import ttk, messagebox

import za_modding as zm

TYPES = ['Normal', 'Fighting', 'Flying', 'Poison', 'Ground', 'Rock', 'Bug', 'Ghost', 'Steel', 'Fire', 'Water',
         'Grass', 'Electric', 'Psychic', 'Ice', 'Dragon', 'Dark', 'Fairy']
SPOT = {'異次元野生_浮遊': 'floating', '異次元野生_接地': 'on the ground'}


def _rows(table):
    for g in table.data.get('values') or []:
        for r in (g.get('root') if isinstance(g, dict) and 'root' in g else [g]):
            yield r


def type_rows(mt):
    """{type number: its dimension_wild_pokemon row}"""
    t = mt.tables.get('dim_wild')
    return {r.get('pokeType', 0): r for r in _rows(t)} if t else {}


def patterns(mt, ptype, rank):
    """[(pattern no, weight, [(spawner row, spawner weight)])] for a type and rank (0-4)."""
    row = type_rows(mt).get(ptype)
    if not row:
        return []
    sp = {r.get('id'): r for r in _rows(mt.tables['spawner'])}
    out = []
    for ri in row.get('rankItems') or []:
        if ri.get('rank', 0) != rank:
            continue
        for p in ri.get('patternItems') or []:
            out.append((p.get('patternNo', 0), p.get('lotteryWeight', 0),
                        [(sp[s['spawnerDataId']], s.get('lotteryWeight', 0)) for s in p.get('spawnerItems') or []
                         if s.get('spawnerDataId') in sp]))
    return out


def users(mt):
    """{encounter id: how many spawners use it}"""
    out = {}
    for r in _rows(mt.tables['spawner']):
        for i in r.get('encountDataInfoList') or []:
            out[i.get('encountDataId')] = out.get(i.get('encountDataId'), 0) + 1
    return out


def spot_kind(info):
    for t in info.get('tagList') or []:
        if t in SPOT:
            return SPOT[t]
    return ''


def _new_enc_id(mt):
    taken = {e.get('id') for e in _rows(mt.tables['enc'])}
    n = 1
    while 'ect_zdm_mod_%04d' % n in taken:
        n += 1
    return 'ect_zdm_mod_%04d' % n


def own_copy(mt, spawner, info):
    """The encounter of `info` copied for this spawner only (when other spawners use it too). Returns the copy."""
    e0 = mt.enc_by_id[info['encountDataId']]
    e = copy.deepcopy(e0)
    e['id'] = _new_enc_id(mt)
    groups = mt.tables['enc'].data['values']
    g = next((g for g in groups if e0 in (g.get('root') or [])), groups[0])
    g.setdefault('root', []).append(e)
    info['encountDataId'] = e['id']
    mt.enc_by_id[e['id']] = e
    return e


def open_window(mt):
    """The Hyperspace spawns editor (needs the Map tab loaded)."""
    if not mt.tables.get('dim_wild') or not mt.tables.get('spawner') or not mt.tables.get('enc'):
        messagebox.showinfo('Hyperspace spawns', 'The Hyperspace spawn tables could not be read.')
        return None
    app = mt.app
    w = tk.Toplevel(app)
    w.title('Hyperspace spawns')
    w.transient(app)
    w.geometry('780x700')
    f = ttk.Frame(w, padding=10)
    f.pack(fill='both', expand=True)
    ttk.Label(f, text=zm.EXPERIMENTAL, style='Warn.TLabel', wraplength=720, justify='left').pack(anchor='w')
    ttk.Label(f, style='Dim.TLabel', wraplength=720, justify='left', text=(
        'Hyperspace picks a zone type and uses your rank; then one of that type and rank\'s spawn patterns, and '
        'each pattern\'s spawners. Edit which Pokémon each spawner brings, their levels and alpha chance, or add '
        'and remove Pokémon. Changes are saved with the Map tab\'s "Save changes to current mod".')).pack(
        anchor='w', pady=(2, 8))
    top = ttk.Frame(f)
    top.pack(fill='x')
    ttk.Label(top, text='Zone type:').pack(side='left')
    tvar = tk.StringVar(value=TYPES[0])
    ttk.Combobox(top, textvariable=tvar, values=TYPES, state='readonly', width=12).pack(side='left', padx=(4, 12))
    ttk.Label(top, text='Rank:').pack(side='left')
    rvar = tk.StringVar(value='Rank 1')
    rbox = ttk.Combobox(top, textvariable=rvar, values=['Rank %d' % k for k in range(1, 6)], state='readonly',
                        width=14)
    rbox.pack(side='left', padx=4)
    summary = tk.StringVar()
    ttk.Label(f, textvariable=summary, style='Dim.TLabel').pack(anchor='w', pady=(6, 2))
    tf = ttk.Frame(f)
    tf.pack(fill='both', expand=True)
    tree = ttk.Treeview(tf, columns=('lv', 'alpha', 'spot', 'w'), height=12)
    for c, t, wd in (('#0', 'Pattern / Pokémon', 260), ('lv', 'Levels', 110), ('alpha', 'Alpha chance', 90),
                     ('spot', 'Spot', 100), ('w', 'How often', 90)):
        tree.heading(c, text=t)
        tree.column(c, width=wd, stretch=(c == '#0'))
    sb_ = ttk.Scrollbar(tf, orient='vertical', command=tree.yview)
    tree.configure(yscrollcommand=sb_.set)
    sb_.pack(side='right', fill='y')
    tree.pack(side='left', fill='both', expand=True)
    ed = ttk.LabelFrame(f, text='Selected Pokémon', padding=6)
    ed.pack(fill='x', pady=(8, 0))
    sp_var, form_v = tk.StringVar(), tk.IntVar(value=0)
    lo_v, hi_v, al_v = tk.IntVar(value=1), tk.IntVar(value=1), tk.DoubleVar(value=0.0)
    ttk.Label(ed, text='Pokémon:').grid(row=0, column=0, sticky='w')
    spb = ttk.Combobox(ed, textvariable=sp_var, values=mt.species_names, width=28)
    spb.grid(row=0, column=1, sticky='w', padx=4)

    def filt(_e=None):
        q = sp_var.get().lower()
        spb.configure(values=[v for v in mt.species_names if q in v.lower()][:500] if q else mt.species_names)
    spb.bind('<KeyRelease>', filt)
    ttk.Label(ed, text='Form:').grid(row=0, column=2, sticky='e')
    ttk.Spinbox(ed, from_=0, to=30, textvariable=form_v, width=4).grid(row=0, column=3, sticky='w', padx=4)
    ttk.Label(ed, text='Levels:').grid(row=1, column=0, sticky='w', pady=(4, 0))
    lv = ttk.Frame(ed)
    lv.grid(row=1, column=1, sticky='w', padx=4, pady=(4, 0))
    ttk.Spinbox(lv, from_=1, to=100, textvariable=lo_v, width=5).pack(side='left')
    ttk.Label(lv, text='to').pack(side='left', padx=4)
    ttk.Spinbox(lv, from_=1, to=100, textvariable=hi_v, width=5).pack(side='left')
    ttk.Label(ed, text='Alpha chance %:').grid(row=1, column=2, sticky='e', pady=(4, 0))
    ttk.Spinbox(ed, from_=0, to=100, increment=1, textvariable=al_v, width=6).grid(row=1, column=3, sticky='w',
                                                                                   padx=4, pady=(4, 0))
    note = tk.StringVar()
    ttk.Label(ed, textvariable=note, style='Dim.TLabel', wraplength=700, justify='left').grid(
        row=2, column=0, columnspan=6, sticky='w', pady=(4, 0))
    bar = ttk.Frame(ed)
    bar.grid(row=3, column=0, columnspan=6, sticky='w', pady=(6, 0))
    item = {}                      # tree iid -> (spawner row, encounter info) for Pokémon rows

    def ptype():
        return TYPES.index(tvar.get())

    def rank():
        return int(re.search(r'\d', rvar.get()).group()) - 1

    def fill(*_):
        tree.delete(*tree.get_children())
        item.clear()
        pats = patterns(mt, ptype(), rank())
        kinds, spawners = set(), set()
        total = sum(wt for _, wt, _ in pats) or 1
        for no, wt, sps in pats:
            pid = tree.insert('', 'end', text='Pattern %d' % no, values=('', '', '', '%d%%' % round(100 * wt / total)),
                              open=True)
            for s, sw in sps:
                spawners.add(s.get('id'))
                for info in s.get('encountDataInfoList') or []:
                    e = mt.enc_by_id.get(info.get('encountDataId'))
                    if not e:
                        continue
                    kinds.add(e.get('devNo'))
                    add = info.get('additionalLevel') or 0
                    lvs = '%d-%d' % (e.get('minLevel', 0), e.get('maxLevel', 0)) + (' (+%d)' % add if add else '')
                    name = mt.species_label(e.get('devNo', 0)) + (' form %d' % e['formNo'] if e.get('formNo') else '')
                    cnt = info.get('maxCount') or 1
                    iid = tree.insert(pid, 'end', text='  %s  ×%d' % (name, cnt),
                                      values=(lvs, '%g%%' % (e.get('oyabunProbability') or 0), spot_kind(info),
                                              'weight %s' % info.get('weight', 0)))
                    item[iid] = (s, info)
        have = [k + 1 for k in range(5) if patterns(mt, ptype(), k)]
        if pats:
            summary.set('%s, rank %d: %d pattern(s), %d spawner(s), %d kind(s) of Pokémon' % (
                tvar.get(), rank() + 1, len(pats), len(spawners), len(kinds)))
        else:
            summary.set('The game has no %s zones at rank %d - %s has zones at rank %s.' % (
                tvar.get(), rank() + 1, tvar.get(), ', '.join(map(str, have)) or 'none'))
        rbox.configure(values=['Rank %d%s' % (k, '' if k in have else ' (none)') for k in range(1, 6)])
        note.set('')

    def selected():
        sel = tree.selection()
        return item.get(sel[0]) if sel else None

    def on_select(_e=None):
        it = selected()
        if not it:
            return
        s, info = it
        e = mt.enc_by_id.get(info['encountDataId'])
        sp_var.set(mt.species_label(e.get('devNo', 0)))
        form_v.set(e.get('formNo') or 0)
        lo_v.set(e.get('minLevel', 1))
        hi_v.set(e.get('maxLevel', 1))
        al_v.set(e.get('oyabunProbability') or 0)
        n = users(mt).get(info['encountDataId'], 1)
        note.set('A spawner can be in several patterns; a change shows in each. ' +
                 ('Floating spots suit Pokémon that fly or float; on-the-ground spots the others. ' if spot_kind(info)
                  else '') + ('These numbers are shared by %d Hyperspace spawners - you\'ll be asked whether to '
                              'change all of them.' % n if n > 1 else ''))

    def mark():
        mt.tables['enc'].dirty = mt.tables['spawner'].dirty = True
        mt.index_spawn_tables()
        n = sum(1 for x_ in mt.layers if x_.dirty) + sum(1 for t in mt.tables.values() if t.dirty)
        mt.dirty_var.set('%d table(s) changed - not saved yet' % n)

    def species_no():
        m = re.search(r'#(\d+)\)?\s*$', sp_var.get())
        if not m:
            raise ValueError('Pick a Pokémon from the list.')
        return int(m.group(1))

    def apply():
        it = selected()
        if not it:
            return
        s, info = it
        try:
            dev, form, lo, hi, al = species_no(), int(form_v.get()), int(lo_v.get()), int(hi_v.get()), \
                float(al_v.get())
        except (ValueError, tk.TclError) as ex:
            messagebox.showerror('Hyperspace spawns', str(ex) or 'Check the numbers.', parent=w)
            return
        n = users(mt).get(info['encountDataId'], 1)
        e = mt.enc_by_id[info['encountDataId']]
        if n > 1:
            ans = messagebox.askyesnocancel(
                'Hyperspace spawns', 'These numbers are used by %d Hyperspace spawners.\n\nYes: change all of them\n'
                'No: change only this one' % n, parent=w)
            if ans is None:
                return
            if ans is False:
                e = own_copy(mt, s, info)
        e['devNo'] = dev
        if form:
            e['formNo'] = form
        else:
            e.pop('formNo', None)
        e['minLevel'], e['maxLevel'] = min(lo, hi), max(lo, hi)
        e['oyabunProbability'] = max(0.0, min(100.0, al))
        if form:
            _wild_behaviour_for(mt, dev, form)
        mark()
        keep = tree.selection()[0]
        fill()
        if tree.exists(keep):
            tree.selection_set(keep)

    def add():
        it = selected()
        if not it:
            messagebox.showinfo('Hyperspace spawns', 'Select a Pokémon of the spawner to add one to.', parent=w)
            return
        s, info = it
        try:
            dev = species_no()
        except ValueError as ex:
            messagebox.showerror('Hyperspace spawns', str(ex), parent=w)
            return
        new_info = copy.deepcopy(info)
        s.setdefault('encountDataInfoList', []).append(new_info)
        e = own_copy(mt, s, new_info)
        e['devNo'] = dev
        e.pop('formNo', None)
        if int(form_v.get() or 0):
            e['formNo'] = int(form_v.get())
            _wild_behaviour_for(mt, dev, e['formNo'])
        e['minLevel'], e['maxLevel'] = min(lo_v.get(), hi_v.get()), max(lo_v.get(), hi_v.get())
        mark()
        fill()

    def remove():
        it = selected()
        if not it:
            return
        s, info = it
        lst = s.get('encountDataInfoList') or []
        if len(lst) <= 1:
            messagebox.showinfo('Hyperspace spawns', 'A spawner needs at least one Pokémon - change it instead.',
                                parent=w)
            return
        lst.remove(info)
        mark()
        fill()

    ttk.Button(bar, text='Apply', command=apply).pack(side='left')
    ttk.Button(bar, text='Add as another Pokémon of this spawner', command=add).pack(side='left', padx=6)
    ttk.Button(bar, text='Remove from this spawner', command=remove).pack(side='left')
    ttk.Button(f, text='Close', command=w.destroy).pack(side='bottom', anchor='e', pady=(8, 0))
    tree.bind('<<TreeviewSelect>>', on_select)
    tvar.trace_add('write', fill)
    rvar.trace_add('write', fill)
    fill()
    w.bind('<Escape>', lambda e: w.destroy())
    return w


def _wild_behaviour_for(mt, dev, form):
    """A form without wild behaviour (a Mega) gets its normal form's, as for new spawn points."""
    try:
        import za_mapcreate
        za_mapcreate._wild_behaviour(mt, dev, form)
    except Exception:  # noqa: BLE001
        pass
