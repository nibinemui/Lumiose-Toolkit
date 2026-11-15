"""Clothing colour editor: change which palette colour each in-game colour option of a clothing item uses.

A clothing model's .trmmt lists, per pattern (normal, tex_00…) and colour group (main = color_*_rep,
second = color_*_con, skin = color_skin), the palette cell (BaseColorIndex1-4, 1-based) every colour option
picks for each material layer. The palette is the material's ColorTableMap texture: a strip of
ColorTableDivideNumber cells. Edits patch those numbers in place (same file size and layout) and are saved
into the current mod; the 3D viewer shows them live.
"""
import os
import struct
import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np

import za_formats as zf
import za_names as zn
import za_trinity as zt
from za_trinity import Table

GROUP_LABELS = {'_rep': 'Main colour', '_con': 'Second colour', 'color_skin': 'Skin tone'}


class NoColours(Exception):
    pass


def group_label(g):
    if g == 'color_skin':
        return 'Skin tone'
    for suf, lab in GROUP_LABELS.items():
        if g.endswith(suf):
            return lab
    return g


def trmmt_slots(data, base_dir):
    """Every colour list in a .trmmt with its byte position:
    [{'set', 'trmtr': [paths], 'group', 'material', 'param', 'start', 'count'}]."""
    root = Table.root(data)
    out = []
    for st in root.tables(2):
        files = [os.path.normpath(os.path.join(base_dir, f)) for f in st.strings(1)]
        for g in st.tables(3):
            gname = g.string(0)
            if not gname.startswith('color_'):
                continue
            d = g.table(10)
            if not d or not d.has(1):
                continue
            try:
                mats = d.tables(1)
            except (struct.error, IndexError):
                continue
            for me in mats:
                if not me.has(4):
                    continue
                for pa in me.tables(4):
                    start, count = pa.vector(1)
                    if count:
                        out.append({'set': st.string(0), 'trmtr': files, 'group': gname, 'material': me.string(0),
                                    'param': pa.string(0), 'start': start, 'count': count})
    return out


def palette_for(trmtr_paths, material):
    """(cell colours as '#rrggbb' list, divide number) for a material's colour table, or (None, 0)."""
    for p in trmtr_paths:
        try:
            mats = zt.read_materials(p)
        except Exception:  # noqa: BLE001
            continue
        m = mats.get(material)
        if m is None:
            continue
        tex = m.texture('ColorTableMap')
        n = m.ints.get('ColorTableDivideNumber', 0)
        if not tex or not n or n < 1 or not os.path.isfile(tex):
            return None, 0
        img = np.asarray(zf.Bntx(zt.read_file(tex)).textures[0].decode().convert('RGB'), np.float32)
        h, w = img.shape[:2]
        cells = []
        for i in range(n):
            x0, x1 = int(i * w / n), max(int(i * w / n) + 1, int((i + 1) * w / n))
            c = img[0:max(1, h // 2), x0:x1].reshape(-1, 3).mean(0)
            cells.append('#%02x%02x%02x' % tuple(int(v + 0.5) for v in c))
        return cells, n
    return None, 0


class ClothingColourEditor(tk.Toplevel):
    def __init__(self, app, model_path, variant=None):
        self.app = app
        self.model = model_path
        self.folder = os.path.basename(os.path.dirname(model_path))
        self.tp = os.path.splitext(model_path)[0] + '.trmmt'
        self.rel = app.rel_game_path(self.tp)
        if not os.path.isfile(self.tp) or not self.rel:
            raise NoColours('This item has no colour options (no material set file).')
        with open(self.tp, 'rb') as f:
            self.original = f.read()
        mod = app.current_mod(ask=False)
        src = mod.path_for(self.rel) if mod and os.path.isfile(mod.path_for(self.rel)) else self.tp
        with open(src, 'rb') as f:
            self.data = bytearray(f.read())
        self.from_mod = src != self.tp
        self.slots = trmmt_slots(bytes(self.data), os.path.dirname(self.tp))
        if not self.slots:
            raise NoColours('This item has no colour options to edit.')
        self.names = zn.clothing_colour_names(app.root_var.get(), self.folder)
        self.sets = []
        for s in self.slots:
            if s['set'] not in self.sets:
                self.sets.append(s['set'])
        self.palettes = {}
        self.dirty = False
        super().__init__(app)
        self.title('Clothing colours - %s' % self.folder)
        self.geometry('960x620')
        self.build()
        if variant and variant.get('set') in self.sets:
            self.set_var.set(self.set_label(variant['set']))
        self.on_set()
        if variant:
            want = '_rep' if variant.get('main') is not None else '_con'
            for g in self.groups():
                if g.endswith(want):
                    self.group_var.set(group_label(g))
                    self.on_group()
                    idx = variant['main'] if want == '_rep' else variant['second']
                    if idx is not None and idx < self.opt_list.size():
                        self.opt_list.selection_clear(0, 'end')
                        self.opt_list.selection_set(idx)
                        self.opt_list.see(idx)
                        self.show_option()
                    break
        self.protocol('WM_DELETE_WINDOW', self.close)

    # ---------------- helpers ----------------
    @staticmethod
    def set_label(s):
        return 'Pattern %s' % s[4:] if s.startswith('tex_') else s.title()

    def set_name(self):
        lab = self.set_var.get()
        return next((s for s in self.sets if self.set_label(s) == lab), self.sets[0])

    def groups(self):
        out = []
        for s in self.slots:
            if s['set'] == self.set_name() and s['group'] not in out:
                out.append(s['group'])
        return out

    def group(self):
        lab = self.group_var.get()
        return next((g for g in self.groups() if group_label(g) == lab), None)

    def group_slots(self):
        g = self.group()
        return [s for s in self.slots if s['set'] == self.set_name() and s['group'] == g]

    def value(self, slot, choice):
        return struct.unpack_from('<i', self.data, slot['start'] + 4 * choice)[0]

    def original_value(self, slot, choice):
        return struct.unpack_from('<i', self.original, slot['start'] + 4 * choice)[0]

    def option_name(self, g, i):
        kind = 'main' if g.endswith('_rep') else 'second' if g.endswith('_con') else None
        nm = self.names.get((self.set_name(), kind), {}).get(i) if kind else None
        if g == 'color_skin':
            nm = 'Tone %d' % (i + 1)
        return nm or 'Colour %d' % (i + 1)

    def palette(self, slot):
        key = (tuple(slot['trmtr']), slot['material'])
        if key not in self.palettes:
            self.palettes[key] = palette_for(slot['trmtr'], slot['material'])
        return self.palettes[key]

    # ---------------- UI ----------------
    def build(self):
        P = ttk.Frame(self, padding=8)
        P.pack(fill='both', expand=True)
        top = ttk.Frame(P)
        top.pack(fill='x')
        ttk.Label(top, text='Pattern:').pack(side='left')
        self.set_var = tk.StringVar(value=self.set_label(self.sets[0]))
        cb = ttk.Combobox(top, textvariable=self.set_var, state='readonly', width=14,
                          values=[self.set_label(s) for s in self.sets])
        cb.pack(side='left', padx=4)
        cb.bind('<<ComboboxSelected>>', lambda e: self.on_set())
        ttk.Label(top, text='Colour:').pack(side='left', padx=(12, 0))
        self.group_var = tk.StringVar()
        self.group_box = ttk.Combobox(top, textvariable=self.group_var, state='readonly', width=16)
        self.group_box.pack(side='left', padx=4)
        self.group_box.bind('<<ComboboxSelected>>', lambda e: self.on_group())
        ttk.Button(top, text='Save to current mod', command=self.save).pack(side='right')
        ttk.Button(top, text='Open live 3D preview', command=self.preview).pack(side='right', padx=4)
        self.state_var = tk.StringVar(value='(loaded from your current mod)' if self.from_mod else '')
        ttk.Label(P, textvariable=self.state_var, style='Dim.TLabel').pack(anchor='w', pady=(4, 0))
        body = ttk.PanedWindow(P, orient='horizontal')
        body.pack(fill='both', expand=True, pady=6)
        left = ttk.Frame(body)
        body.add(left, weight=1)
        ttk.Label(left, text='Colour options (as in the boutique)').pack(anchor='w')
        lf = ttk.Frame(left)
        lf.pack(fill='both', expand=True)
        st = self.app.style
        self.opt_list = tk.Listbox(lf, exportselection=False, activestyle='none', borderwidth=0, highlightthickness=0,
                                   background=st.lookup('Treeview', 'fieldbackground') or 'white',
                                   foreground=st.lookup('Treeview', 'foreground') or 'black',
                                   selectbackground=st.lookup('Treeview', 'selectbackground', ['selected']) or '#0078d7')
        sb = ttk.Scrollbar(lf, orient='vertical', command=self.opt_list.yview)
        self.opt_list.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.opt_list.pack(side='left', fill='both', expand=True)
        self.opt_list.bind('<<ListboxSelect>>', lambda e: self.show_option())
        ttk.Button(left, text='Revert to game colours', command=self.revert).pack(fill='x', pady=4)
        self.right = ttk.Frame(body)
        body.add(self.right, weight=3)
        self.note = ttk.Label(P, style='Dim.TLabel', wraplength=900, justify='left', text=(
            'Each option picks one palette cell per material layer (the swatches). Click a swatch to change it. '
            'Save writes the item\'s material set (.trmmt) into your current mod; an open 3D preview refreshes by '
            'itself. Option names are the game\'s own and do not change.'))
        self.note.pack(anchor='w')

    def on_set(self):
        gs = self.groups()
        order = sorted(gs, key=lambda g: (g == 'color_skin', g))
        self.group_box.configure(values=[group_label(g) for g in order])
        if self.group_var.get() not in [group_label(g) for g in order]:
            self.group_var.set(group_label(order[0]))
        self.on_group()

    def on_group(self):
        slots = self.group_slots()
        n = max([s['count'] for s in slots] or [0])
        g = self.group()
        sel = self.opt_list.curselection()
        self.opt_list.delete(0, 'end')
        for i in range(n):
            changed = any(i < s['count'] and self.value(s, i) != self.original_value(s, i) for s in slots)
            self.opt_list.insert('end', '%d  %s%s' % (i + 1, self.option_name(g, i), '   (changed)' if changed else ''))
        if n:
            i = sel[0] if sel and sel[0] < n else 0
            self.opt_list.selection_set(i)
        self.show_option()

    def choice(self):
        s = self.opt_list.curselection()
        return s[0] if s else None

    def show_option(self):
        for w in self.right.winfo_children():
            w.destroy()
        c = self.choice()
        if c is None:
            return
        ttk.Label(self.right, text='%s - %s' % (group_label(self.group()), self.option_name(self.group(), c)),
                  style='Head.TLabel').pack(anchor='w', pady=(0, 6))
        bg = self.app.style.lookup('TFrame', 'background') or '#f0f0f0'
        for slot in self.group_slots():
            if c >= slot['count']:
                continue
            cells, n = self.palette(slot)
            val = self.value(slot, c)
            orig = self.original_value(slot, c)
            layer = slot['param'].replace('BaseColorIndex', 'layer ')
            row = ttk.Frame(self.right)
            row.pack(fill='x', pady=4, anchor='w')
            ttk.Label(row, text='%s · %s:  palette cell %d%s' % (slot['material'], layer, val,
                                                                 '' if val == orig else '   (game: %d)' % orig)).pack(anchor='w')
            if not cells:
                ttk.Label(row, text='(no colour table for this material)', style='Dim.TLabel').pack(anchor='w')
                continue
            size, per = 22, 32
            rows_n = (n + per - 1) // per
            cv = tk.Canvas(row, width=min(n, per) * (size + 2) + 2, height=rows_n * (size + 2) + 2,
                           highlightthickness=0, background=bg)
            cv.pack(anchor='w', pady=2)
            for i, col in enumerate(cells):
                x, y = 1 + (i % per) * (size + 2), 1 + (i // per) * (size + 2)
                chosen = (i + 1) == val
                cv.create_rectangle(x, y, x + size, y + size, fill=col,
                                    outline=self.app.style.lookup('.', 'foreground') if chosen else col,
                                    width=3 if chosen else 1, tags=('cell%d' % i,))
                cv.tag_bind('cell%d' % i, '<Button-1>', lambda e, s=slot, k=i + 1: self.set_value(s, k))

    def set_value(self, slot, k):
        c = self.choice()
        if c is None:
            return
        struct.pack_into('<i', self.data, slot['start'] + 4 * c, k)
        self.dirty = True
        self.state_var.set('Unsaved changes - press Save to current mod.')
        self.refresh_list_keep()

    def refresh_list_keep(self):
        c = self.choice()
        self.on_group()
        if c is not None and c < self.opt_list.size():
            self.opt_list.selection_clear(0, 'end')
            self.opt_list.selection_set(c)
            self.show_option()

    def revert(self):
        c = self.choice()
        if c is None:
            return
        for slot in self.group_slots():
            if c < slot['count']:
                struct.pack_into('<i', self.data, slot['start'] + 4 * c, self.original_value(slot, c))
        self.dirty = True
        self.state_var.set('Unsaved changes - press Save to current mod.')
        self.refresh_list_keep()

    def save(self):
        m = self.app.current_mod()
        if not m:
            return
        if bytes(self.data) == self.original:
            if os.path.isfile(m.path_for(self.rel)):
                m.remove(self.rel)
                self.app.mod_changed(m, self.rel, 'back to the game\'s colours - removed from the mod')
            else:
                self.app.status_var.set('No colour changes to save.')
        else:
            m.add_bytes(self.rel, bytes(self.data))
            self.app.mod_changed(m, self.rel, 'clothing colours saved')
        self.dirty = False
        self.from_mod = True
        self.state_var.set('Saved into "%s".' % m.name)

    def preview(self):
        g, c = self.group(), self.choice()
        extra = ['--outfit', '--pattern', self.set_name()]
        if g and c is not None:
            if g.endswith('_rep'):
                extra += ['--main', str(c)]
            elif g.endswith('_con'):
                extra += ['--second', str(c)]
        if self.dirty:
            if messagebox.askyesno('Clothing colours', 'Save your changes first so the preview shows them?', parent=self):
                self.save()
        self.app.open_viewer(self.model, extra=extra)

    def close(self):
        if self.dirty and not messagebox.askyesno('Clothing colours', 'Close without saving your changes?', parent=self):
            return
        self.destroy()
