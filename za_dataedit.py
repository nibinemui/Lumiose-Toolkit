"""Game data tab: a table editor for FlatBuffer data files.

Presets cover moves, items, item/clothing shops, clothing prices, trainers and item balls; "Open
other data file…" (or "Open in table editor" in Browse) edits any data file that has a schema.
The form is built from the schema itself (.bfbs next to the file, or our own .fbs), so every
field of a row can be edited, including nested lists. Names are shown for moves, items, species
and clothing.
"""
import json
import os
import shutil
import tempfile
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

import za_formats as zf
import za_modding as zm
from za_trinity import Table

ZA = os.path.join(zm.SCHEMAS, 'za')

PRESETS = [
    dict(name='Moves', rel='avalon/data/waza_array.bin', schema=os.path.join(ZA, 'za_waza.fbs'), rows='Table',
         label=lambda r, N: '%d %s' % (r.get('MoveID', 0), N.move(r.get('MoveID', 0))),
         only=('Only moves usable in Z-A', lambda r: r.get('CanUseMove')),
         lookups={'MoveID': 'move'},
         note='Power, accuracy, PP, type, category, priority, status effects, stat changes and flags. '
              'Field layout matches Scarlet/Violet (checked on known moves); Unknown*/Flag_ZA* are new in Z-A.'),
    dict(name='Items', rel='world/exl/item_data/item_data/item_data.bin', schema=os.path.join(ZA, 'za_item.fbs'),
         rows='Table', label=lambda r, N: '%d %s' % (r.get('Id', 0), N.item(r.get('Id', 0))),
         lookups={'Id': 'item', 'TMMove': 'move'},
         note='Price is what shops charge. TMMove = the move a TM teaches. Unknown* fields are not identified yet.'),
    dict(name='Item shops', rel='world/exl/shop/shop_item_lineup/shop_item_lineup.bin',
         schema=os.path.join(ZA, 'za_shop_item_lineup.fbs'), rows='Table', label=lambda r, N: r.get('id', '?'),
         lookups={'itemId': 'item'},
         note='What each shop sells. "items": add a copy of a row and change the item to sell something new; '
              'conditions (e.g. phase_condition = story progress) decide when it appears. Prices are in Items.'),
    dict(name='Clothing items (prices)', rel='world/exl/dress_up_data/dress_up_data/dress_up_data.bin',
         schema=os.path.join(zm.SCHEMAS, 'pokedocs', 'dress_up_data.fbs'), rows='items',
         label=lambda r, N: '%d %s%s' % (r.get('id', 0), N.clothing(r.get('id', 0)),
                                         '  $%d' % r['price'] if r.get('price') else ''),
         lookups={'id': 'clothing'}, note='Every clothing item and colour variant: price, colours, icon.'),
    dict(name='Clothing shops', rel='world/exl/shop/shop_dress_up_lineup/shop_dress_up_lineup.bin',
         schema=os.path.join(ZA, 'za_shop_dressup_lineup.fbs'), rows='Table', label=lambda r, N: r.get('id', '?'),
         lookups={'itemId': 'clothing'}, note='Which clothes each boutique sells (prices are in "Clothing items").'),
    dict(name='Hair & make-up', rel='world/exl/shop/shop_hair_make_lineup/shop_hair_make_lineup.bin',
         schema=os.path.join(ZA, 'za_shop_dressup_lineup.fbs'), rows='Table', label=lambda r, N: r.get('id', '?'),
         note='Hairstyles / make-up offered by the salons (ids are hair_make_data entries).'),
    dict(name='Trainers', rel='world/ik_data/trainer/trdata/trdata_array.bin', schema=None, rows='values',
         label=lambda r, N: '%s  (%s)' % (r.get('trid', '?'), ', '.join(
             '%s %s' % (N.species_enum(r[k].get('devId')), r[k].get('level', ''))
             for k in ('poke1', 'poke2', 'poke3', 'poke4', 'poke5', 'poke6')
             if isinstance(r.get(k), dict) and r[k].get('devId'))),
         note='Trainer teams: species, level, moves (waza1-4; wazaType MANUAL = use these moves), item, '
              'nature (seikaku), ability, IVs (talentValue), EVs, shiny (rareType), ball.'),
    dict(name='Item balls (contents)',
         rel='world/ik_data/field/item_ball/item_ball_item_data/item_ball_item_data/item_ball_item_data_array.bin',
         schema=None, rows='values/0/root',
         label=lambda r, N: '%s  %s' % (r.get('id', '?'), ', '.join(
             N.item(x.get('itemId')) for x in (r.get('itemLotteryDataList') or [])[:3])),
         lookups={'itemId': 'item'},
         note='What each item ball on the map gives (weight = chance when there are several). '
              'The Map tab shows these on the item balls too.'),
]

BT_INT = {3: (-128, 127), 4: (0, 255), 5: (-32768, 32767), 6: (0, 65535), 7: (-2 ** 31, 2 ** 31 - 1),
          8: (0, 2 ** 32 - 1), 9: (-2 ** 63, 2 ** 63 - 1), 10: (0, 2 ** 64 - 1)}


# ---------------------------------------------------------------------------
# schema (read from .bfbs; .fbs files are compiled to .bfbs with flatc)
# ---------------------------------------------------------------------------

class Field:
    __slots__ = ('name', 'kind', 'base', 'ref', 'default', 'elem')

    def __repr__(self):
        return '<%s %s %s>' % (self.name, self.kind, self.ref)


class Schema:
    def __init__(self, path):
        self.path = path
        if path.lower().endswith('.fbs'):
            data = self._compile(path)
        else:
            with open(path, 'rb') as f:
                data = f.read()
        r = Table.root(data)
        objs, enums = r.tables(0), r.tables(1)
        self.enums = {}
        for e in enums:
            vals = [(v.string(0), v.scalar(1, 'q')) for v in e.tables(1)]
            self.enums[e.string(0)] = {'values': vals, 'union': bool(e.scalar(2, 'b'))}
        enames = [e.string(0) for e in enums]
        onames = [o.string(0) for o in objs]
        self.objects = {}
        for o in objs:
            fields = []
            for fl in sorted(o.tables(1), key=lambda x: x.scalar(2, 'H')):
                t = fl.table(1)
                bt, el, idx = t.scalar(0, 'b'), t.scalar(1, 'b'), t.scalar(2, 'i', -1)
                f = Field()
                f.name, f.base, f.elem, f.ref = fl.string(0), bt, el, None
                f.default = fl.scalar(5, 'd', 0.0) if bt in (11, 12) else fl.scalar(4, 'q', 0)
                if bt == 1:
                    f.kind = 'utype'
                elif bt == 2:
                    f.kind = 'bool'
                elif 3 <= bt <= 10:
                    f.kind = 'enum' if idx >= 0 else 'int'
                    f.ref = enames[idx] if idx >= 0 else None
                elif bt in (11, 12):
                    f.kind = 'float'
                elif bt == 13:
                    f.kind = 'string'
                elif bt == 15:
                    f.kind, f.ref = 'obj', onames[idx]
                elif bt == 16:
                    f.kind, f.ref = 'union', enames[idx]
                elif bt in (14, 17, 18):
                    if el == 15:
                        f.kind, f.ref = 'vec_obj', onames[idx]
                    elif el == 13:
                        f.kind = 'vec_string'
                    else:
                        f.kind = 'vec_scalar'
                        f.ref = enames[idx] if idx >= 0 and el <= 10 else None
                else:
                    f.kind = 'unknown'
                fields.append(f)
            self.objects[o.string(0)] = {'fields': fields, 'struct': bool(o.scalar(2, 'b'))}
        self.root = r.table(4).string(0) if r.table(4) else onames[-1]

    @staticmethod
    def _compile(path):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, 's.fbs')
            shutil.copyfile(path, src)
            zm._run([zm.FLATC, '-b', '--schema', '-o', td, src])
            with open(os.path.join(td, 's.bfbs'), 'rb') as f:
                return f.read()

    def fields(self, tname):
        return self.objects[tname]['fields']

    def is_struct(self, tname):
        return self.objects[tname]['struct']

    def blank(self, tname):
        """A new object of type tname (structs need every field)."""
        if not self.is_struct(tname):
            return {}
        out = {}
        for f in self.fields(tname):
            if f.kind == 'obj':
                out[f.name] = self.blank(f.ref)
            elif f.kind == 'bool':
                out[f.name] = False
            elif f.kind == 'enum':
                out[f.name] = self.enum_name(f.ref, f.default)
            else:
                out[f.name] = f.default
        return out

    def enum_name(self, ename, value):
        for n, v in self.enums[ename]['values']:
            if v == value:
                return n
        return value

    def enum_value(self, ename, name):
        if isinstance(name, int):
            return name
        for n, v in self.enums[ename]['values']:
            if n == name:
                return v
        return None

    def resolve(self, data, path):
        """Follow 'a/0/b' through data and types -> (list, element type)."""
        node, t = data, self.root
        for part in [p for p in path.split('/') if p]:
            if part.isdigit():
                node = node[int(part)]
                continue
            f = next(f for f in self.fields(t) if f.name == part)
            node = node.setdefault(part, [])
            t = f.ref
        return node, t

    def find_rows(self, data):
        """Longest list of objects anywhere in the file -> path string."""
        best = [(-1, '')]

        def walk(node, t, path, depth):
            if depth > 4 or not isinstance(node, dict):
                return
            for f in self.fields(t):
                v = node.get(f.name)
                if f.kind == 'vec_obj' and isinstance(v, list):
                    if len(v) > best[0][0]:
                        best[0] = (len(v), path + f.name)
                    if v and len(v) <= 4:
                        for i, x in enumerate(v):
                            walk(x, f.ref, '%s%s/%d/' % (path, f.name, i), depth + 1)
                elif f.kind == 'obj' and isinstance(v, dict):
                    walk(v, f.ref, path + f.name + '/', depth + 1)
        walk(data, self.root, '', 0)
        return best[0][1]


_SCHEMAS = {}


def load_schema(path):
    key = (os.path.normcase(os.path.abspath(path)), os.path.getmtime(path))
    if key not in _SCHEMAS:
        _SCHEMAS[key] = Schema(path)
    return _SCHEMAS[key]


# ---------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------

class Names:
    def __init__(self, root):
        self.moves, self.items, self.species, self.clothes = {}, {}, {}, {}
        base = os.path.join(root, 'ik_message', 'dat', 'English', 'common')

        def lines(name):
            try:
                with open(os.path.join(base, name + '.dat'), 'rb') as f:
                    return zf.decode_msg(f.read())
            except (OSError, ValueError):
                return []
        self.moves = dict(enumerate(lines('wazaname')))
        self.items = dict(enumerate(lines('itemname')))
        try:
            with open(os.path.join(base, 'monsname.tbl'), 'rb') as f:
                labels = zf.parse_tbl(f.read())
            self.species = {int(l[9:]): t for l, t in zip(labels, lines('monsname')) if l.startswith('MONSNAME_')}
        except (OSError, ValueError):
            pass
        try:
            import za_names
            self.clothes = za_names.clothing_by_id(root)
        except Exception:  # noqa: BLE001
            self.clothes = {}
        self.dev_enum = {}

    @staticmethod
    def _num(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def move(self, v):
        n = self._num(v)
        return self.moves.get(n, '?') if n is not None else '?'

    def item(self, v):
        n = self._num(v)
        return self.items.get(n, '?') if n is not None else '?'

    def clothing(self, v):
        n = self._num(v)
        return self.clothes.get(n, '?') if n is not None else '?'

    def species_dev(self, dev):
        """DevID number -> name. Dev numbers equal the National Dex up to 905 (Gen 1-8)."""
        n = self._num(dev)
        if n is None:
            return '?'
        return self.species.get(n, '') if 0 < n <= 905 else ''

    def species_enum(self, name):
        n = self.dev_enum.get(name)
        return self.species_dev(n) or str(name).replace('DEV_', '').title()

    def lookup(self, kind, v):
        return {'move': self.move, 'item': self.item, 'clothing': self.clothing}[kind](v)

    def table(self, kind):
        src = {'move': self.moves, 'item': self.items, 'clothing': self.clothes}[kind]
        return ['%d %s' % (k, n) for k, n in sorted(src.items()) if n and n.strip()]


NATURES = ['Hardy', 'Lonely', 'Brave', 'Adamant', 'Naughty', 'Bold', 'Docile', 'Relaxed', 'Impish', 'Lax', 'Timid',
           'Hasty', 'Serious', 'Jolly', 'Naive', 'Modest', 'Mild', 'Quiet', 'Bashful', 'Rash', 'Calm', 'Gentle',
           'Sassy', 'Careful', 'Quirky']


def enum_lookup(ename):
    e = ename.split('.')[-1]
    return {'ItemID': 'item', 'WazaID': 'move', 'DevID': 'species', 'SeikakuType': 'nature'}.get(e)


# ---------------------------------------------------------------------------
# the tab
# ---------------------------------------------------------------------------

class DataTab:
    def __init__(self, app, parent):
        self.app = app
        self.parent = parent
        self.names = None
        self.schema = None
        self.data = None
        self.rows = []
        self.row_type = None
        self.preset = None
        self.src = None
        self.rel = None
        self.dirty = False
        self.cur = None
        self.build()
        app.nb.bind('<<NotebookTabChanged>>', self.on_tab, add='+')
        self._started = False

    # ---------------- UI ----------------
    def build(self):
        P = self.parent
        top = ttk.Frame(P)
        top.pack(fill='x', padx=4, pady=(4, 0))
        ttk.Label(top, text='Table:').pack(side='left')
        self.preset_var = tk.StringVar()
        self.preset_box = ttk.Combobox(top, textvariable=self.preset_var, state='readonly', width=26,
                                       values=[p['name'] for p in PRESETS])
        self.preset_box.pack(side='left', padx=4)
        self.preset_box.bind('<<ComboboxSelected>>', lambda e: self.open_preset(self.preset_var.get()))
        ttk.Button(top, text='Open other data file…', command=self.open_other).pack(side='left', padx=4)
        ttk.Button(top, text='Save changes to current mod', command=self.save).pack(side='right')
        self.dirty_var = tk.StringVar()
        ttk.Label(top, textvariable=self.dirty_var, style='Dim.TLabel').pack(side='right', padx=8)
        self.info = tk.StringVar(value='Pick a table to edit.')
        ttk.Label(P, textvariable=self.info, style='Dim.TLabel', wraplength=1200, justify='left').pack(
            fill='x', padx=6, pady=2)

        paned = ttk.PanedWindow(P, orient='horizontal')
        paned.pack(fill='both', expand=True, padx=4, pady=4)
        left = ttk.Frame(paned)
        paned.add(left, weight=1)
        sf = ttk.Frame(left)
        sf.pack(fill='x')
        ttk.Label(sf, text='Find:').pack(side='left')
        self.q = tk.StringVar()
        ttk.Entry(sf, textvariable=self.q).pack(side='left', fill='x', expand=True, padx=3)
        self.q.trace_add('write', lambda *a: self.fill_rows())
        self.only_var = tk.BooleanVar(value=True)
        self.only_chk = ttk.Checkbutton(left, variable=self.only_var, command=self.fill_rows)
        self.list = ttk.Treeview(left, show='tree', selectmode='browse')
        self.list.pack(fill='both', expand=True, pady=(4, 0))
        self.list.bind('<<TreeviewSelect>>', lambda e: self.select_row())
        rb = ttk.Frame(left)
        rb.pack(fill='x', pady=2)
        ttk.Button(rb, text='Duplicate row', command=self.dup_row).pack(side='left')
        ttk.Button(rb, text='Delete row', command=self.del_row).pack(side='left', padx=3)
        ttk.Button(rb, text='Edit row as JSON…', command=self.row_json).pack(side='left')

        right = ttk.Frame(paned)
        paned.add(right, weight=3)
        self.row_title = tk.StringVar()
        ttk.Label(right, textvariable=self.row_title, style='Head.TLabel').pack(anchor='w')
        bg = self.app.style.lookup('TFrame', 'background') or None
        self.canvas = tk.Canvas(right, highlightthickness=0, borderwidth=0, background=bg)
        sb = ttk.Scrollbar(right, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.canvas.pack(fill='both', expand=True)
        self.form = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window(0, 0, window=self.form, anchor='nw')
        self.form.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind('<Enter>', lambda e: self.canvas.bind_all('<MouseWheel>', self._wheel))
        self.canvas.bind('<Leave>', lambda e: self.canvas.unbind_all('<MouseWheel>'))

    def _wheel(self, e):
        w = self.canvas.winfo_containing(e.x_root, e.y_root)
        if isinstance(w, (tk.Listbox, ttk.Combobox, tk.Text)):
            return
        self.canvas.yview_scroll(int(-e.delta / 120), 'units')

    def on_tab(self, event=None):
        if self.app.nb.select() == str(self.parent) and not self._started:
            self._started = True
            self.preset_var.set(PRESETS[0]['name'])
            self.open_preset(PRESETS[0]['name'])

    # ---------------- loading ----------------
    def confirm_discard(self):
        return not self.dirty or messagebox.askyesno(
            'Lumiose Toolkit', 'You have unsaved changes in this table. Discard them?')

    def open_preset(self, name):
        p = next(p for p in PRESETS if p['name'] == name)
        root = self.app.root_var.get()
        self.load(p, os.path.join(root, *p['rel'].split('/')), p['rel'])

    def open_other(self):
        path = filedialog.askopenfilename(initialdir=self.app.root_var.get(), title='Data file to edit',
                                          filetypes=[('Data', '*.bin *.trmtr *.trscn *.trpmcatalog'), ('All', '*')])
        if path:
            self.open_path(path)

    def open_path(self, path):
        rel = self.app.rel_game_path(path)
        if rel is None:
            # a file inside a mod folder: map back to its game path
            norm = path.replace('\\', '/')
            if '/romfs/' in norm:
                rel = norm.split('/romfs/', 1)[1]
        if rel is None:
            messagebox.showerror('Lumiose Toolkit', 'Pick a file inside the game folder.')
            return
        for p in PRESETS:
            if p['rel'] == rel:
                self.preset_var.set(p['name'])
                return self.load(p, os.path.join(self.app.root_var.get(), *rel.split('/')), rel)
        if not zm.schema_for(os.path.join(self.app.root_var.get(), *rel.split('/'))):
            messagebox.showerror('Lumiose Toolkit', 'No schema is known for %s, so it cannot be edited as a table.'
                                 % os.path.basename(path))
            return
        self.preset_var.set('')
        self.load(dict(name=os.path.basename(rel), rel=rel, schema=None, rows=None, label=None,
                       note='Generic table view of ' + rel),
                  os.path.join(self.app.root_var.get(), *rel.split('/')), rel)

    def load(self, preset, game_path, rel):
        if not self.confirm_discard():
            return
        m = self.app.current_mod(ask=False)
        src = game_path
        if m and os.path.isfile(m.path_for(rel)):
            src = m.path_for(rel)
        schema_path = preset.get('schema') or zm.schema_for(game_path)
        root = self.app.root_var.get()
        self.info.set('Loading %s…' % rel)
        self.clear_form()
        self.list.delete(*self.list.get_children())

        def work():
            try:
                if self.names is None:
                    self.names = Names(root)
                sch = load_schema(schema_path)
                data = json.loads(zm.to_json(src, schema_path))
                self.app.q.put(lambda: self.loaded(preset, data, sch, schema_path, src, rel))
            except Exception as e:  # noqa: BLE001
                msg = '%s\n%s' % (e, traceback.format_exc()[-600:])
                self.app.q.put(lambda: self.info.set('Could not load %s: %s' % (rel, msg)))
        threading.Thread(target=work, daemon=True).start()

    def loaded(self, preset, data, sch, schema_path, src, rel):
        self.preset, self.data, self.schema, self.schema_path, self.src, self.rel = \
            preset, data, sch, schema_path, src, rel
        self.dirty = False
        self.dirty_var.set('')
        path = preset.get('rows') or sch.find_rows(data)
        try:
            self.rows, self.row_type = sch.resolve(data, path) if path else ([data], sch.root)
        except (StopIteration, IndexError, KeyError, TypeError):
            self.rows, self.row_type = [data], sch.root
        for en, e in sch.enums.items():
            if en.endswith('DevID'):
                self.names.dev_enum = {n: v for n, v in e['values']}
        only = preset.get('only')
        if only:
            self.only_chk.configure(text=only[0])
            self.only_chk.pack(anchor='w', before=self.list)
        else:
            self.only_chk.pack_forget()
        self.info.set('%s — %d rows%s.  %s' % (rel, len(self.rows), ' (from the current mod)' if 'romfs' in src else '',
                                              preset.get('note', '')))
        self.cur = None
        if self.q.get():
            self.q.set('')       # refills the list
        else:
            self.fill_rows()

    # ---------------- rows ----------------
    def row_label(self, i, r):
        f = self.preset.get('label')
        if f:
            try:
                return f(r, self.names)
            except Exception:  # noqa: BLE001
                pass
        return '%d  %s' % (i, self.summary(r, self.row_type))

    def fill_rows(self, keep=None):
        if self.data is None:
            return
        q = self.q.get().strip().lower()
        only = self.preset.get('only')
        self.list.delete(*self.list.get_children())
        for i, r in enumerate(self.rows):
            if only and self.only_var.get() and not only[1](r):
                continue
            lab = self.row_label(i, r)
            if q and q not in lab.lower():
                continue
            self.list.insert('', 'end', str(i), text=lab)
        if keep is not None and self.list.exists(str(keep)):
            self.list.selection_set(str(keep))
            self.list.see(str(keep))

    def select_row(self):
        sel = self.list.selection()
        if not sel:
            return
        self.cur = int(sel[0])
        self.show_row()

    def show_row(self):
        self.clear_form()
        r = self.rows[self.cur]
        self.row_title.set(self.row_label(self.cur, r))
        self.build_obj(self.form, r, self.row_type, 0)
        self.canvas.yview_moveto(0)

    def clear_form(self):
        for w in self.form.winfo_children():
            w.destroy()
        self.row_title.set('')

    def changed(self):
        self.dirty = True
        self.dirty_var.set('unsaved changes')
        if self.cur is not None and self.list.exists(str(self.cur)):
            lab = self.row_label(self.cur, self.rows[self.cur])
            self.list.item(str(self.cur), text=lab)
            self.row_title.set(lab)

    def dup_row(self):
        if self.cur is None:
            return
        self.rows.insert(self.cur + 1, json.loads(json.dumps(self.rows[self.cur])))
        self.changed()
        self.fill_rows(keep=self.cur + 1)

    def del_row(self):
        if self.cur is None or len(self.rows) <= 1:
            return
        if not messagebox.askyesno('Lumiose Toolkit', 'Delete row "%s"?' % self.row_label(self.cur, self.rows[self.cur])):
            return
        del self.rows[self.cur]
        self.cur = None
        self.clear_form()
        self.changed()
        self.fill_rows()

    def row_json(self):
        if self.cur is None:
            return
        win = tk.Toplevel(self.app)
        win.title('Row %d as JSON' % self.cur)
        win.geometry('700x600')
        t = tk.Text(win, font=('Consolas', 10), undo=True)
        t.pack(fill='both', expand=True)
        t.insert('1.0', json.dumps(self.rows[self.cur], indent=2, ensure_ascii=False))
        self.app.style_text(t) if hasattr(self.app, 'style_text') else None

        def apply():
            try:
                v = json.loads(t.get('1.0', 'end'))
            except ValueError as e:
                messagebox.showerror('Lumiose Toolkit', 'Not valid JSON:\n%s' % e, parent=win)
                return
            self.rows[self.cur] = v
            self.changed()
            self.show_row()
            win.destroy()
        ttk.Button(win, text='Apply', command=apply).pack(side='right', padx=6, pady=6)

    # ---------------- generic form ----------------
    def lookup_kind(self, f):
        if f.kind == 'enum' or (f.kind == 'vec_scalar' and f.ref):
            k = enum_lookup(f.ref)
            if k:
                return k
        return (self.preset.get('lookups') or {}).get(f.name) or {
            'wazaid': 'move', 'tmmove': 'move', 'itemid': 'item', 'dropitem': 'item'}.get(f.name.lower())

    def show_value(self, f, v):
        k = self.lookup_kind(f)
        if f.kind == 'enum':
            if k == 'species':
                nm = self.names.species_dev(self.schema.enum_value(f.ref, v))
                return '%s  %s' % (v, nm) if nm else str(v)
            if k == 'nature':
                num = self.schema.enum_value(f.ref, v)
                return '%s  %s' % (v, NATURES[num - 1]) if num and 0 < num <= 25 else str(v)
            if k in ('move', 'item'):
                num = self.schema.enum_value(f.ref, v)
                return '%s  %s' % (v, self.names.lookup(k, num)) if num else str(v)
            return str(v)
        if k and k not in ('species', 'nature'):
            return '%s %s' % (v, self.names.lookup(k, v))
        return str(v)

    def summary(self, d, tname):
        if not isinstance(d, dict):
            return str(d)
        parts = []
        for f in self.schema.fields(tname):
            if f.name in d and f.kind in ('int', 'enum', 'string', 'float', 'bool'):
                parts.append('%s=%s' % (f.name, self.show_value(f, d[f.name])))
            elif f.name in d and f.kind in ('vec_obj',) and isinstance(d[f.name], list) and d[f.name]:
                parts.append('%s[%d]: %s' % (f.name, len(d[f.name]), self.summary(d[f.name][0], f.ref)))
            if len(parts) >= 4:
                break
        return '  '.join(parts) or '{}'

    def build_obj(self, parent, d, tname, depth, notify=None):
        notify = notify or self.changed
        fields = [f for f in self.schema.fields(tname) if f.kind != 'utype']
        simple = [f for f in fields if f.kind in ('int', 'float', 'string', 'enum')]
        flags = [f for f in fields if f.kind == 'bool']
        rest = [f for f in fields if f not in simple and f not in flags]
        cols = 2 if depth == 0 else 1
        if simple:
            g = ttk.Frame(parent)
            g.pack(fill='x', anchor='w')
            for i, f in enumerate(simple):
                r, c = divmod(i, cols)
                ttk.Label(g, text=f.name).grid(row=r, column=c * 2, sticky='w', padx=(4, 2), pady=1)
                self.field_widget(g, d, f, notify).grid(row=r, column=c * 2 + 1, sticky='w', padx=(0, 14), pady=1)
        if flags:
            g = ttk.Frame(parent)
            g.pack(fill='x', anchor='w', pady=(4, 0))
            per = 4 if depth == 0 else 3
            for i, f in enumerate(flags):
                var = tk.BooleanVar(value=bool(d.get(f.name, bool(f.default))))

                def setb(var=var, f=f):
                    d[f.name] = var.get()
                    notify()
                ttk.Checkbutton(g, text=f.name, variable=var, command=setb).grid(
                    row=i // per, column=i % per, sticky='w', padx=4)
        for f in rest:
            lf = ttk.LabelFrame(parent, text=f.name)
            lf.pack(fill='x', anchor='w', padx=(4 + 8 * min(depth, 3), 2), pady=3)
            self.complex_widget(lf, d, f, depth, notify)

    def field_widget(self, parent, d, f, notify):
        v = d.get(f.name)
        if v is None:
            v = self.schema.enum_name(f.ref, f.default) if f.kind == 'enum' else ('' if f.kind == 'string' else f.default)
        k = self.lookup_kind(f)
        if f.kind == 'enum':
            vals = self.schema.enums[f.ref]['values']
            disp = [self.show_value(f, n) for n, _ in vals]
            names = [n for n, _ in vals]
            return self.combo(parent, d, f, notify, disp, self.show_value(f, v),
                              lambda s: names[disp.index(s)] if s in disp else
                              next((n for n in names if n.lower() == s.split()[0].lower()), None) if s.split() else None,
                              width=34 if k else 22)
        if k and k not in ('species', 'nature') and f.kind in ('int', 'string'):
            disp = self.names.table(k)

            def parse(s, f=f):
                try:
                    n = int(s.split()[0])
                except (ValueError, IndexError):
                    return None
                return str(n) if f.kind == 'string' else n
            return self.combo(parent, d, f, notify, disp, self.show_value(f, v) if v not in ('', None) else '', parse, width=34)
        var = tk.StringVar(value=str(v) if not isinstance(v, float) else repr(v))
        e = ttk.Entry(parent, textvariable=var, width=40 if f.kind == 'string' else 12)

        def commit(*_):
            s = var.get()
            if f.kind == 'string':
                val = s
            else:
                try:
                    val = float(s) if f.kind == 'float' else int(s)
                except ValueError:
                    self.app.status_var.set('%s: "%s" is not a number' % (f.name, s))
                    return
                lo_hi = BT_INT.get(f.base)
                if lo_hi and not lo_hi[0] <= val <= lo_hi[1]:
                    self.app.status_var.set('%s must be between %d and %d' % (f.name, lo_hi[0], lo_hi[1]))
                    return
            if d.get(f.name) != val:
                d[f.name] = val
                notify()
        var.trace_add('write', commit)
        return e

    def combo(self, parent, d, f, notify, disp, current, parse, width=22):
        c = ttk.Combobox(parent, values=disp, width=width)
        c.set(current)

        def commit(*_):
            val = parse(c.get().strip())
            if val is None:
                return
            if d.get(f.name) != val:
                d[f.name] = val
                notify()

        def filt(e):
            if e.keysym in ('Return', 'Tab', 'Up', 'Down', 'Escape'):
                return
            s = c.get().lower()
            c.configure(values=[x for x in disp if s in x.lower()][:400] if s else disp)
        c.bind('<<ComboboxSelected>>', commit)
        c.bind('<Return>', commit)
        c.bind('<FocusOut>', commit)
        c.bind('<KeyRelease>', filt)
        return c

    def complex_widget(self, lf, d, f, depth, notify):
        if f.kind == 'obj':
            if not isinstance(d.get(f.name), dict):
                def add():
                    d[f.name] = self.schema.blank(f.ref)
                    notify()
                    self.show_row()
                ttk.Button(lf, text='(not set)  Add', command=add).pack(anchor='w', padx=4, pady=2)
                return
            self.build_obj(lf, d[f.name], f.ref, depth + 1, notify)
        elif f.kind == 'vec_obj':
            ListEditor(self, lf, d, f, depth, notify)
        elif f.kind in ('vec_scalar', 'vec_string'):
            cur = d.get(f.name) or []
            var = tk.StringVar(value=', '.join(str(x) for x in cur))
            ttk.Entry(lf, textvariable=var, width=80).pack(fill='x', padx=4, pady=2)
            hint = 'comma separated'
            k = self.lookup_kind(f)
            if k and k not in ('species', 'nature') and cur:
                hint += ':  ' + ', '.join(self.names.lookup(k, x) for x in cur[:12])
            ttk.Label(lf, text=hint, style='Dim.TLabel').pack(anchor='w', padx=4)

            def commit(*_):
                parts = [p.strip() for p in var.get().split(',') if p.strip()]
                if f.kind == 'vec_string':
                    val = parts
                elif f.ref:
                    val = parts
                else:
                    try:
                        val = [float(p) if (f.elem in (11, 12)) else int(p) for p in parts]
                    except ValueError:
                        return
                if d.get(f.name) != val:
                    d[f.name] = val
                    notify()
            var.trace_add('write', commit)
        elif f.kind == 'union':
            v = d.get(f.name)
            ttk.Label(lf, text='%s: %s   (use "Edit row as JSON…" to change)' % (
                d.get(f.name + '_type', '-'), json.dumps(v)[:200] if v is not None else '-'),
                style='Dim.TLabel').pack(anchor='w', padx=4)

    # ---------------- save ----------------
    def save(self):
        if self.data is None:
            return
        self.parent.focus_set()
        self.app.update_idletasks()
        m = self.app.current_mod()
        if not m:
            return
        rel, schema, text = self.rel, self.schema_path, json.dumps(self.data, ensure_ascii=False)

        def job():
            zm.from_json(text, schema, m.path_for(rel))
            return rel

        def done(r):
            self.dirty = False
            self.dirty_var.set('saved into "%s"' % m.name)
            self.app.refresh_mods()
            if self.rel and 'item_ball_item_data' in self.rel and hasattr(self.app, 'map_ui'):
                getattr(self.app.map_ui, 'reload_item_balls', lambda: None)()
        self.app.run_bg('Saving %s' % os.path.basename(rel), job, done)


class ListEditor:
    """A vector of objects: list of entries + Add copy / Remove / move, and a form for the selected one."""

    def __init__(self, tab, lf, d, f, depth, notify):
        self.tab, self.d, self.f, self.depth, self.notify = tab, d, f, depth, notify
        top = ttk.Frame(lf)
        top.pack(fill='x', padx=4, pady=2)
        self.lb = tk.Listbox(top, height=6, exportselection=False, activestyle='none',
                             font=('Segoe UI', 9))
        try:
            st = tab.app.style
            self.lb.configure(background=st.lookup('Treeview', 'fieldbackground') or 'white',
                              foreground=st.lookup('Treeview', 'foreground') or 'black',
                              selectbackground=st.lookup('Treeview', 'selectbackground', ['selected']) or '#0078d7',
                              borderwidth=0, highlightthickness=0)
        except tk.TclError:
            pass
        sb = ttk.Scrollbar(top, orient='vertical', command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        self.lb.pack(side='left', fill='x', expand=True)
        sb.pack(side='left', fill='y')
        bf = ttk.Frame(top)
        bf.pack(side='left', padx=4)
        for text, cmd in (('Add copy', self.add_copy), ('Add new', self.add_new), ('Remove', self.remove),
                          ('Up', lambda: self.move(-1)), ('Down', lambda: self.move(1))):
            ttk.Button(bf, text=text, command=cmd, width=9).pack(fill='x')
        self.sub = ttk.Frame(lf)
        self.sub.pack(fill='x', padx=4, pady=(0, 3))
        self.lb.bind('<<ListboxSelect>>', lambda e: self.show())
        self.refresh()

    @property
    def items(self):
        v = self.d.get(self.f.name)
        if not isinstance(v, list):
            v = self.d[self.f.name] = []
        return v

    def refresh(self, sel=None):
        self.lb.delete(0, 'end')
        for i, x in enumerate(self.items):
            self.lb.insert('end', '%d: %s' % (i, self.tab.summary(x, self.f.ref)))
        self.lb.configure(height=max(2, min(8, len(self.items))))
        if sel is not None and 0 <= sel < len(self.items):
            self.lb.selection_set(sel)
            self.lb.see(sel)
            self.show()
        else:
            for w in self.sub.winfo_children():
                w.destroy()

    def index(self):
        s = self.lb.curselection()
        return s[0] if s else None

    def show(self):
        for w in self.sub.winfo_children():
            w.destroy()
        i = self.index()
        if i is None:
            return
        x = self.items[i]

        def changed_hook(i=i):
            self.notify()
            if 0 <= i < self.lb.size() and i < len(self.items):
                self.lb.delete(i)
                self.lb.insert(i, '%d: %s' % (i, self.tab.summary(self.items[i], self.f.ref)))
                self.lb.selection_set(i)
        self.tab.build_obj(self.sub, x, self.f.ref, self.depth + 1, changed_hook)

    def add_copy(self):
        i = self.index()
        src = self.items[i] if i is not None else (self.items[-1] if self.items else self.tab.schema.blank(self.f.ref))
        self.items.insert((i if i is not None else len(self.items) - 1) + 1, json.loads(json.dumps(src)))
        self.notify()
        self.refresh((i if i is not None else len(self.items) - 2) + 1)

    def add_new(self):
        self.items.append(self.tab.schema.blank(self.f.ref))
        self.notify()
        self.refresh(len(self.items) - 1)

    def remove(self):
        i = self.index()
        if i is None:
            return
        del self.items[i]
        self.notify()
        self.refresh(min(i, len(self.items) - 1))

    def move(self, step):
        i = self.index()
        if i is None or not 0 <= i + step < len(self.items):
            return
        it = self.items
        it[i], it[i + step] = it[i + step], it[i]
        self.notify()
        self.refresh(i + step)

