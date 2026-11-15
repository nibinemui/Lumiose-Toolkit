"""Clothing colour editor: change which palette colour each in-game colour option of a clothing item uses.

A clothing model's .trmmt lists, per pattern (normal, tex_00…) and colour group (main = color_*_rep,
second = color_*_con, skin = color_skin), the palette cell (BaseColorIndex1-4, 1-based) every colour option
picks for each material layer. The palette is the material's ColorTableMap texture: a strip of
ColorTableDivideNumber cells. Edits patch those numbers in place (same file size and layout) and are saved
into the current mod; the 3D viewer shows them live.
"""
import colorsys
import json
import os
import struct
import tempfile
import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np

import za_formats as zf
import za_names as zn
import za_trinity as zt
from za_trinity import Table

# Custom colours: a palette strip of n cells is widened to FACTOR*n cells. Old cell i becomes cell
# FACTOR*i-1 (exactly where items still using n cells sample it), so every other item looks the same;
# cells FACTOR*i-2 and FACTOR*i are free "private" cells for custom colours of items switched to the
# wide layout (their ColorTableDivideNumber and colour indices are converted in their own files).
FACTOR = 3
ALLOC_FILE = 'custom_colours.json'          # in the mod folder (not packaged): private cells in use

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


def read_current(app, path):
    """Bytes of a game file - the current mod's copy when it has one."""
    m = app.current_mod(ask=False)
    rel = app.rel_game_path(path)
    if m and rel and os.path.isfile(m.path_for(rel)):
        path = m.path_for(rel)
    with open(path, 'rb') as f:
        return f.read()


def materials_in(data, path):
    """{material name: (flatbuffer table, Material)} of .trmtr bytes."""
    base = os.path.dirname(path)
    return {t.string(0): (t, zt.Material(t, base)) for t in Table.root(data).tables(1)}


def palette_state(app, trmtr_paths, material):
    """Palette of a material as the current mod has it: {'path', 'n', 'n_game', 'wide', 'cells' ['#rrggbb'],
    'img' RGBA array} - or None when the material has no colour table."""
    for p in trmtr_paths:
        try:
            mats = materials_in(read_current(app, p), p)
        except Exception:  # noqa: BLE001
            continue
        if material not in mats:
            continue
        m = mats[material][1]
        tex = m.texture('ColorTableMap')
        n = m.ints.get('ColorTableDivideNumber', 0)
        if not tex or not n or n < 1 or not os.path.isfile(tex):
            return None
        try:
            with open(p, 'rb') as f:
                n_game = materials_in(f.read(), p)[material][1].ints.get('ColorTableDivideNumber', n)
        except Exception:  # noqa: BLE001
            n_game = n
        img = np.asarray(zf.Bntx(read_current(app, tex)).textures[0].decode().convert('RGBA'))
        h, w = img.shape[:2]
        cells = []
        for i in range(n):
            x0, x1 = int(i * w / n), max(int(i * w / n) + 1, int((i + 1) * w / n))
            c = img[0:max(1, h // 2), x0:x1, :3].reshape(-1, 3).astype(np.float32).mean(0)
            cells.append('#%02x%02x%02x' % tuple(int(v + 0.5) for v in c))
        return {'path': tex, 'n': n, 'n_game': n_game, 'wide': n == FACTOR * n_game and n != n_game,
                'cells': cells, 'img': img}
    return None


def is_private(v):
    """In the wide layout, cells FACTOR*i-1 are the game's colours, the others are private cells."""
    return v > 0 and v % FACTOR != FACTOR - 1


def shade_for(rgb, ref_top, ref_bottom):
    """Shadow colour for a custom colour, following how the game's own cell darkens (ref top -> bottom)."""
    h1, s1, v1 = colorsys.rgb_to_hsv(*(c / 255.0 for c in ref_top))
    h2, s2, v2 = colorsys.rgb_to_hsv(*(c / 255.0 for c in ref_bottom))
    sf = min(1.6, max(0.8, s2 / s1)) if s1 > 0.05 else 1.1
    vf = min(0.95, max(0.4, v2 / v1)) if v1 > 0.05 else 0.7
    h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))
    return tuple(int(round(c * 255)) for c in colorsys.hsv_to_rgb(h, min(1.0, s * sf), v * vf))


def write_texture(mod, rel, old_bytes, rgba, keep_size=True):
    """Encode an RGBA array into the texture file `rel` of the mod (same format as old_bytes)."""
    import za_modding as zm
    from PIL import Image
    fd, png = tempfile.mkstemp(suffix='.png')
    os.close(fd)
    try:
        Image.fromarray(np.ascontiguousarray(rgba.astype(np.uint8)), 'RGBA').save(png)
        new, _note = zm.import_png(png, old_bytes, 0, 0, keep_size=keep_size)
    finally:
        os.remove(png)
    mod.add_bytes(rel, new)
    return new


class ColourPicker(tk.Toplevel):
    """Colour wheel + brightness + hex code. ColourPicker(parent, '#rrggbb').result -> '#rrggbb' or None."""
    SIZE = 220

    def __init__(self, parent, initial='#808080', title='Custom colour'):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        r, g, b = (int(initial[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
        self.h, self.s, self.v = colorsys.rgb_to_hsv(r, g, b)
        self.initial = initial
        bg = ttk.Style(self).lookup('TFrame', 'background') or '#f0f0f0'
        self.configure(background=bg)
        f = ttk.Frame(self, padding=10)
        f.pack(fill='both', expand=True)
        self.wheel = tk.Canvas(f, width=self.SIZE, height=self.SIZE, highlightthickness=0, background=bg,
                               cursor='crosshair')
        self.wheel.grid(row=0, column=0, rowspan=6, padx=(0, 10))
        for ev in ('<Button-1>', '<B1-Motion>'):
            self.wheel.bind(ev, self.on_wheel)
        ttk.Label(f, text='Brightness').grid(row=0, column=1, sticky='w')
        self.vvar = tk.DoubleVar(value=self.v * 100)
        ttk.Scale(f, from_=100, to=0, orient='vertical', variable=self.vvar, length=150,
                  command=lambda e: self.on_value()).grid(row=1, column=1, rowspan=3, sticky='ns')
        ttk.Label(f, text='Hex code').grid(row=0, column=2, sticky='w', padx=(10, 0))
        self.hexvar = tk.StringVar(value=initial)
        e = ttk.Entry(f, textvariable=self.hexvar, width=10)
        e.grid(row=1, column=2, sticky='nw', padx=(10, 0))
        e.bind('<Return>', lambda ev: self.on_hex())
        e.bind('<FocusOut>', lambda ev: self.on_hex())
        ttk.Label(f, text='Old / new').grid(row=2, column=2, sticky='sw', padx=(10, 0))
        self.swatch = tk.Canvas(f, width=90, height=40, highlightthickness=0, background=bg)
        self.swatch.grid(row=3, column=2, sticky='nw', padx=(10, 0))
        bar = ttk.Frame(f)
        bar.grid(row=6, column=0, columnspan=3, sticky='e', pady=(10, 0))
        ttk.Button(bar, text='OK', command=self.ok).pack(side='right')
        ttk.Button(bar, text='Cancel', command=self.destroy).pack(side='right', padx=4)
        self._base = self._wheel_array()
        self.redraw()
        self.bind('<Escape>', lambda ev: self.destroy())
        self.grab_set()

    def _wheel_array(self):
        n = self.SIZE
        y, x = np.mgrid[0:n, 0:n].astype(np.float32)
        c = (n - 1) / 2.0
        dx, dy = x - c, c - y
        r = np.sqrt(dx * dx + dy * dy) / c
        hue = (np.arctan2(dy, dx) / (2 * np.pi)) % 1.0
        sat = np.clip(r, 0, 1)
        i = np.floor(hue * 6).astype(int) % 6
        f = hue * 6 - np.floor(hue * 6)
        p, q, t = 1 - sat, 1 - sat * f, 1 - sat * (1 - f)
        one = np.ones_like(sat)
        out = np.zeros((n, n, 3), np.float32)
        for k, chans in enumerate(((one, t, p), (q, one, p), (p, one, t), (p, q, one), (t, p, one), (one, p, q))):
            msk = i == k
            for ch in range(3):
                out[..., ch][msk] = chans[ch][msk]
        alpha = np.clip((1.0 - r) * c + 0.5, 0, 1)            # smooth edge
        return out, alpha

    def redraw(self):
        from PIL import Image, ImageTk
        rgb, alpha = self._base
        bg = self.wheel.cget('background')
        try:
            br = np.array([int(bg[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255.0
        except ValueError:
            br = np.array([0.94, 0.94, 0.94], np.float32)
        img = rgb * self.v
        img = img * alpha[..., None] + br * (1 - alpha[..., None])
        self._photo = ImageTk.PhotoImage(Image.fromarray((img * 255 + 0.5).astype(np.uint8), 'RGB'))
        self.wheel.delete('all')
        self.wheel.create_image(0, 0, anchor='nw', image=self._photo)
        c = (self.SIZE - 1) / 2.0
        import math
        x = c + math.cos(self.h * 2 * math.pi) * self.s * c
        y = c - math.sin(self.h * 2 * math.pi) * self.s * c
        ring = '#000000' if self.v > 0.5 else '#ffffff'
        self.wheel.create_oval(x - 6, y - 6, x + 6, y + 6, outline=ring, width=2)
        cur = self.hex()
        if self.hexvar.get().lower() != cur:
            self.hexvar.set(cur)
        self.swatch.delete('all')
        self.swatch.create_rectangle(0, 0, 44, 40, fill=self.initial, outline='')
        self.swatch.create_rectangle(46, 0, 90, 40, fill=cur, outline='')

    def hex(self):
        r, g, b = colorsys.hsv_to_rgb(self.h, self.s, self.v)
        return '#%02x%02x%02x' % (int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5))

    def on_wheel(self, ev):
        import math
        c = (self.SIZE - 1) / 2.0
        dx, dy = ev.x - c, c - ev.y
        self.s = min(1.0, math.hypot(dx, dy) / c)
        self.h = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
        self.redraw()

    def on_value(self):
        self.v = max(0.0, min(1.0, self.vvar.get() / 100.0))
        self.redraw()

    def on_hex(self):
        t = self.hexvar.get().strip().lstrip('#')
        if len(t) == 3:
            t = ''.join(ch * 2 for ch in t)
        try:
            r, g, b = (int(t[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        except ValueError:
            self.hexvar.set(self.hex())
            return
        if len(t) != 6:
            self.hexvar.set(self.hex())
            return
        self.h, self.s, self.v = colorsys.rgb_to_hsv(r, g, b)
        self.vvar.set(self.v * 100)
        self.redraw()

    def ok(self):
        self.on_hex()
        self.result = self.hex()
        self.destroy()


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
        """palette_state() of the slot's material (as in the current mod), cached until a custom colour."""
        key = (tuple(slot['trmtr']), slot['material'])
        if key not in self.palettes:
            try:
                self.palettes[key] = palette_state(self.app, slot['trmtr'], slot['material'])
            except Exception:  # noqa: BLE001
                self.palettes[key] = None
        return self.palettes[key]

    def orig_conv(self, slot, choice):
        """The game's value for this option, in the current layout (wide palettes move cell i to FACTOR*i-1)."""
        g = self.original_value(slot, choice)
        st = self.palette(slot)
        return FACTOR * g - 1 if st and st['wide'] and g > 0 else g

    def cell_text(self, slot, v):
        st = self.palette(slot)
        if st and st['wide']:
            if is_private(v):
                return 'custom colour %s' % (st['cells'][v - 1] if 0 < v <= len(st['cells']) else '?')
            return 'palette cell %d' % ((v + 1) // FACTOR)
        return 'palette cell %d' % v

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
        ttk.Button(top, text='Pattern textures…', command=lambda: self.app.edit_pattern_textures(
            self.model, {'set': self.set_name()})).pack(side='right')
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
            changed = any(i < s['count'] and self.value(s, i) != self.orig_conv(s, i) for s in slots)
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
        fg = self.app.style.lookup('.', 'foreground') or 'black'
        for slot in self.group_slots():
            if c >= slot['count']:
                continue
            st = self.palette(slot)
            val = self.value(slot, c)
            orig = self.orig_conv(slot, c)
            layer = slot['param'].replace('BaseColorIndex', 'layer ')
            row = ttk.Frame(self.right)
            row.pack(fill='x', pady=4, anchor='w')
            ttk.Label(row, text='%s · %s:  %s%s' % (
                slot['material'], layer, self.cell_text(slot, val),
                '' if val == orig else '   (game: %s)' % self.cell_text(slot, orig))).pack(anchor='w')
            if not st:
                ttk.Label(row, text='(no colour table for this material)', style='Dim.TLabel').pack(anchor='w')
                continue
            # the game's cells (in a wide palette: every FACTOR-th cell), then this option's custom colour
            if st['wide']:
                game_cells = [(FACTOR * i + FACTOR - 1, st['cells'][FACTOR * i + FACTOR - 2]) for i in range(st['n_game'])]
            else:
                game_cells = [(i + 1, col) for i, col in enumerate(st['cells'])]
            size, per = 22, 32
            n = len(game_cells)
            rows_n = (n + per - 1) // per
            per = max(8, min(per, (self.right.winfo_width() - 10) // (size + 2))) if self.right.winfo_width() > 50 else 20
            rows_n = (n + per - 1) // per
            cv = tk.Canvas(row, width=min(n, per) * (size + 2) + 2, height=rows_n * (size + 2) + 2,
                           highlightthickness=0, background=bg)
            cv.pack(anchor='w', pady=2)
            for i, (k, col) in enumerate(game_cells):
                x, y = 1 + (i % per) * (size + 2), 1 + (i // per) * (size + 2)
                chosen = k == val
                cv.create_rectangle(x, y, x + size, y + size, fill=col, outline=fg if chosen else col,
                                    width=3 if chosen else 1, tags=('cell%d' % i,))
                cv.tag_bind('cell%d' % i, '<Button-1>', lambda e, s=slot, k=k: self.set_value(s, k))
            side = ttk.Frame(row)
            side.pack(anchor='w', pady=(2, 0))
            if st['wide'] and is_private(val) and val <= len(st['cells']):
                sw = tk.Canvas(side, width=size, height=size, highlightthickness=0, background=bg)
                sw.create_rectangle(1, 1, size - 1, size - 1, fill=st['cells'][val - 1], outline=fg, width=2)
                sw.pack(side='left', padx=(0, 4))
            ttk.Button(side, text='Custom colour…', command=lambda s=slot: self.custom_colour(s)).pack(side='left')

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
                struct.pack_into('<i', self.data, slot['start'] + 4 * c, self.orig_conv(slot, c))
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
        self.update_allocations(m)
        self.dirty = False
        self.from_mod = True
        self.state_var.set('Saved into "%s".' % m.name)

    # ---------------- custom colours ----------------
    def load_alloc(self, m):
        try:
            with open(os.path.join(m.folder, ALLOC_FILE), encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def update_allocations(self, m):
        """Record which private palette cells this item uses (so other items in the mod pick other ones)."""
        alloc = self.load_alloc(m)
        prefix = self.folder + '|'
        for cells in alloc.values():
            for k in [k for k, who in cells.items() if who.startswith(prefix)]:
                del cells[k]
        for s in self.slots:
            st = self.palette(s)
            if not st or not st['wide']:
                continue
            rel = self.app.rel_game_path(st['path'])
            for i in range(s['count']):
                v = self.value(s, i)
                if is_private(v):
                    alloc.setdefault(rel, {})[str(v)] = '%s|%s|%s|%s|%s|%d' % (
                        self.folder, s['set'], s['group'], s['material'], s['param'], i)
        alloc = {k: v for k, v in alloc.items() if v}
        p = os.path.join(m.folder, ALLOC_FILE)
        if alloc:
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(alloc, f, indent=1, sort_keys=True)
        elif os.path.isfile(p):
            os.remove(p)

    def custom_colour(self, slot):
        c = self.choice()
        st = self.palette(slot)
        if c is None or not st:
            return
        m = self.app.current_mod()
        if not m:
            return
        v = self.value(slot, c)
        cur = st['cells'][v - 1] if 0 < v <= len(st['cells']) else '#808080'
        pk = ColourPicker(self, cur, 'Custom colour - %s, %s' % (self.option_name(self.group(), c),
                                                                  slot['param'].replace('BaseColorIndex', 'layer ')))
        self.wait_window(pk)
        if not pk.result:
            return
        rgb = tuple(int(pk.result[i:i + 2], 16) for i in (1, 3, 5))
        try:
            if not st['wide']:
                if not messagebox.askokcancel('Custom colour', (
                        'Custom colours go into extra "private" cells of the palette, so other clothing that shares '
                        'the palette keeps its exact colours: the palette is widened to %d× its cells (the game\'s '
                        'colours stay where other items read them) and this item is switched to the wide layout.\n\n'
                        'This changes the palette texture and this item\'s material files in your mod. Custom colours '
                        'are new: check them in-game before sharing the mod.') % FACTOR, parent=self):
                    return
                self.widen(m, st)
                self.palettes.clear()
                st = self.palette(slot)
                if not st or not st['wide']:
                    raise ValueError('the palette could not be switched to the wide layout')
            k = self.alloc_cell(m, slot, c, st)
            self.paint_cell(m, st, k, rgb, self.orig_conv(slot, c))
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            messagebox.showerror('Custom colour', 'Could not set the custom colour: %s' % e, parent=self)
            self.palettes.clear()
            return
        struct.pack_into('<i', self.data, slot['start'] + 4 * c, k)
        self.palettes.clear()
        self.save()
        self.refresh_list_keep()
        self.app.status_var.set('Custom colour %s saved into "%s" (private palette cell %d).' % (pk.result, m.name, k))

    def widen(self, m, st):
        """Switch palette st['path'] to the wide layout for this item (palette, material files, .trmmt values)."""
        pal, ng = st['path'], st['n_game']
        pal_rel = self.app.rel_game_path(pal)
        if not pal_rel:
            raise ValueError('the palette is outside the game folder')
        # colour options that use this palette in the narrow layout - found before any file changes
        narrow = [s for s in self.slots if (lambda ps: ps and not ps['wide'] and
                                            os.path.normcase(ps['path']) == os.path.normcase(pal))(self.palette(s))]
        raw = read_current(self.app, pal)
        t = zf.Bntx(raw).textures[0]
        with open(pal, 'rb') as f:
            game_w = zf.Bntx(f.read()).textures[0].width
        if t.width != FACTOR * game_w:                     # not widened yet (by another item of this mod)
            if game_w % ng or t.width != game_w:
                raise ValueError('unexpected palette size %dx%d for %d cells' % (t.width, t.height, ng))
            img = np.asarray(t.decode().convert('RGBA'))
            h, w = img.shape[:2]
            wide = np.repeat(img.reshape(h, ng, w // ng, 4), FACTOR, axis=1).reshape(h, FACTOR * w, 4)
            write_texture(m, pal_rel, raw, wide, keep_size=False)
        # this item's material files: ColorTableDivideNumber n -> FACTOR*n, colour index i -> FACTOR*i-1
        files = [p for s in self.slots for p in s['trmtr']]
        base = os.path.splitext(self.model)[0] + '.trmtr'
        if os.path.isfile(base):
            files.append(base)
        pending = []
        for p in dict.fromkeys(os.path.normpath(x) for x in files):
            rel = self.app.rel_game_path(p)
            if not rel or not os.path.isfile(p):
                continue
            data = bytearray(read_current(self.app, p))
            edits = []
            for tb in Table.root(bytes(data)).tables(1):
                tex = zt.Material(tb, os.path.dirname(p)).texture('ColorTableMap')
                if not tex or os.path.normcase(tex) != os.path.normcase(pal):
                    continue
                ints = [(pp.string(0), pp.struct_pos(1)) for pp in tb.tables(9)]
                n_pos = next((pos for name, pos in ints if name == 'ColorTableDivideNumber'), None)
                if n_pos is None:
                    continue
                n_now = struct.unpack_from('<i', data, n_pos)[0]
                if n_now == FACTOR * ng:
                    continue
                if n_now != ng:
                    raise ValueError('%s: unexpected cell count %d' % (os.path.basename(p), n_now))
                edits.append((n_pos, FACTOR * ng))
                for name, pos in ints:
                    if name.startswith('BaseColorIndex'):
                        if pos is None:
                            raise ValueError('%s: %s cannot be changed in place' % (os.path.basename(p), name))
                        val = struct.unpack_from('<i', data, pos)[0]
                        if val > 0:
                            edits.append((pos, FACTOR * val - 1))
            if edits:
                for pos, val in edits:
                    struct.pack_into('<i', data, pos, val)
                pending.append((rel, bytes(data)))
        for rel, data in pending:                          # only write once every file could be converted
            m.add_bytes(rel, data)
        for s in narrow:                                   # the colour options (.trmmt)
            for i in range(s['count']):
                val = self.value(s, i)
                if val > 0:
                    struct.pack_into('<i', self.data, s['start'] + 4 * i, FACTOR * val - 1)
        m.add_bytes(self.rel, bytes(self.data))

    def alloc_cell(self, m, slot, c, st):
        """A private cell for option c of this slot: its current one if nobody else uses it, else the free
        private cell closest to the option's game colour."""
        prefix = self.folder + '|'
        rel = self.app.rel_game_path(st['path'])
        used = {int(k) for k, who in self.load_alloc(m).get(rel, {}).items() if not who.startswith(prefix)}
        for s in self.slots:
            ps = self.palette(s)
            if not ps or os.path.normcase(ps['path']) != os.path.normcase(st['path']):
                continue
            for i in range(s['count']):
                if not (s is slot and i == c) and is_private(self.value(s, i)):
                    used.add(self.value(s, i))
        v = self.value(slot, c)
        if is_private(v) and v not in used:
            return v
        ref = self.orig_conv(slot, c)
        free = [k for k in range(1, st['n'] + 1) if is_private(k) and k not in used]
        if not free:
            raise ValueError('no free private cells left in this palette (%d in use)' % len(used))
        return min(free, key=lambda k: (abs(k - ref), k))

    def paint_cell(self, m, st, k, rgb, ref):
        """Write rgb into palette cell k (top half) with a matching shade (bottom half)."""
        pal = st['path']
        raw = read_current(self.app, pal)
        img = np.array(zf.Bntx(raw).textures[0].decode().convert('RGBA'))
        h, w = img.shape[:2]
        cw = w // st['n']
        top_rows = slice(0, max(1, h // 2))
        bottom_rows = slice(max(1, h // 2), h)
        r = ref if 0 < ref <= st['n'] else k
        ref_top = img[top_rows, (r - 1) * cw:r * cw, :3].reshape(-1, 3).mean(0)
        ref_bottom = img[bottom_rows, (r - 1) * cw:r * cw, :3].reshape(-1, 3).mean(0) if h > 1 else ref_top * 0.7
        img[top_rows, (k - 1) * cw:k * cw, :3] = rgb
        if h > 1:
            img[bottom_rows, (k - 1) * cw:k * cw, :3] = shade_for(rgb, ref_top, ref_bottom)
        img[:, (k - 1) * cw:k * cw, 3] = 255
        write_texture(m, self.app.rel_game_path(pal), raw, img, keep_size=True)

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


# ---------------------------------------------------------------------------------------------------------
# pattern textures
# ---------------------------------------------------------------------------------------------------------

TEXTURE_ROLES = [
    # (texture name or file suffix, what it is)
    (('BaseColorMap', 'BaseColorMap0', 'AlbedoMap', '_alb'),
     'Base texture: the pattern and shading. Usually grey, because the colour options tint it'),
    (('LayerMaskMap', '_lym'),
     'Layer mask: which areas take which colour (red, green, blue and alpha channels = layers 1-4)'),
    (('ColorTableMap', '_col'),
     'Colour palette: the strip of cells the colour options pick from (changes every option using a cell)'),
    (('NormalMap', 'NormalMap0', '_nrm'), 'Normal map: fine surface bumps and folds'),
    (('RoughnessMap', '_rgh'), 'Roughness: how shiny or matte each area is'),
    (('MetallicMap', '_mtl'), 'Metallic: which areas look like metal'),
    (('OcclusionMap', 'AOMap', 'AmbientOcclusionMap', '_ao'), 'Ambient occlusion: baked shadows in creases'),
    (('SpecularMaskMap',), 'Specular mask: where highlights show'),
    (('EmissionColorMap', 'EmissionMap', '_emi'), 'Emission: areas that glow'),
    (('RimLightMaskMap',), 'Rim light mask: where the edge highlight shows'),
    (('ShadowingColorMap', 'ShadowingColorMaskMap'), 'Shadow colour'),
    (('LocalReflectionMap',), 'Reflection'),
]


def describe_texture(role, path):
    for names, text in TEXTURE_ROLES:              # the material's slot name decides first
        if role in names:
            return text
    low = os.path.basename(path).lower()
    for names, text in TEXTURE_ROLES:              # then the file name (…_alb, …_nrm)
        if any(n.startswith('_') and n + '.' in low for n in names):
            return text
    return role


def pattern_textures(sets, set_name, model_dir=None):
    """Textures used by one pattern of a clothing model: [{'path', 'roles', 'materials', 'shared', 'common'}],
    the item's own textures first (base, mask, palette…), then shared game textures. 'shared' = other
    patterns using the same file; 'common' = the file is outside the item's folder (used by other items)."""
    users = {}                                      # path -> set of pattern names using it
    per_set = {}
    for st in sets:
        texs = {}
        for tf in st['trmtr']:
            try:
                mats = zt.read_materials(tf)
            except Exception:  # noqa: BLE001
                continue
            for mname, m in mats.items():
                for role, p in m.textures.items():
                    e = texs.setdefault(p, {'path': p, 'roles': [], 'materials': []})
                    if role not in e['roles']:
                        e['roles'].append(role)
                    if mname not in e['materials']:
                        e['materials'].append(mname)
        per_set[st['name']] = texs
        for p in texs:
            users.setdefault(p, set()).add(st['name'])
    out = list(per_set.get(set_name, {}).values())
    order = {n: i for i, (names, _t) in enumerate(TEXTURE_ROLES) for n in names}
    md = os.path.normcase(os.path.normpath(model_dir)) if model_dir else None
    for e in out:
        e['shared'] = sorted(users.get(e['path'], set()) - {set_name})
        e['common'] = bool(md) and os.path.normcase(os.path.dirname(e['path'])) != md
    out.sort(key=lambda e: (e['common'], min([order.get(r, 99) for r in e['roles']] or [99]),
                            os.path.basename(e['path'])))
    return out


class PatternTextureWindow(tk.Toplevel):
    """Lists the textures of one clothing pattern (tex_00…) and opens them for live editing."""

    def __init__(self, app, model_path, variant=None):
        self.app = app
        self.model = model_path
        self.folder = os.path.basename(os.path.dirname(model_path))
        tp = os.path.splitext(model_path)[0] + '.trmmt'
        try:
            self.sets = zt.read_trmmt(tp) if os.path.isfile(zt.resolve(tp)) else []
        except Exception:  # noqa: BLE001
            self.sets = []
        if not self.sets:                           # no material sets: the model's own materials
            self.sets = [{'name': 'normal', 'trmtr': [os.path.splitext(model_path)[0] + '.trmtr'], 'groups': {}}]
        self.variant = variant
        self.rows = []
        self._photo = None
        super().__init__(app)
        self.title('Pattern textures - %s' % self.folder)
        self.geometry('1060x600')
        self.minsize(760, 420)
        self.build()
        names = [s['name'] for s in self.sets]
        if variant and variant.get('set') in names:
            self.set_var.set(ClothingColourEditor.set_label(variant['set']))
        self.on_set()
        self._stamp = self.mod_stamp()
        self._poll = self.after(1500, self.poll)
        self.bind('<Destroy>', lambda e: e.widget is self and self.after_cancel(self._poll))

    def mod_stamp(self):
        """Modification times of the mod copies of this pattern's textures (to notice live edits)."""
        m = self.app.current_mod(ask=False)
        out = []
        for e in self.rows:
            rel = self.app.rel_game_path(e['path'])
            p = m.path_for(rel) if m and rel else None
            out.append(os.path.getmtime(p) if p and os.path.isfile(p) else 0)
        return tuple(out)

    def poll(self):
        s = self.mod_stamp()
        if s != self._stamp:
            self._stamp = s
            self.refresh_state()
        self._poll = self.after(1500, self.poll)

    def build(self):
        P = ttk.Frame(self, padding=8)
        P.pack(fill='both', expand=True)
        top = ttk.Frame(P)
        top.pack(fill='x')
        ttk.Label(top, text='Pattern:').pack(side='left')
        labels = [ClothingColourEditor.set_label(s['name']) for s in self.sets]
        self.set_var = tk.StringVar(value=labels[0])
        cb = ttk.Combobox(top, textvariable=self.set_var, state='readonly', width=14, values=labels)
        cb.pack(side='left', padx=4)
        cb.bind('<<ComboboxSelected>>', lambda e: self.on_set())
        ttk.Button(top, text='Open live 3D preview', command=self.preview).pack(side='right')
        ttk.Button(top, text='Edit colours…', command=self.colours).pack(side='right', padx=4)
        body = ttk.PanedWindow(P, orient='horizontal')
        body.pack(fill='both', expand=True, pady=6)
        left = ttk.Frame(body)
        body.add(left, weight=3)
        cols = ('what', 'shared', 'mod')
        self.tree = ttk.Treeview(left, columns=cols, show='tree headings', selectmode='browse')
        self.tree.heading('#0', text='Texture')
        self.tree.heading('what', text='What it is')
        self.tree.heading('shared', text='Also used by')
        self.tree.heading('mod', text='In your mod')
        self.tree.column('#0', width=250, minwidth=120, stretch=False)
        self.tree.column('what', width=200, minwidth=120, stretch=True)
        self.tree.column('shared', width=110, minwidth=60, stretch=False)
        self.tree.column('mod', width=80, minwidth=60, stretch=False)
        sb = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.show())
        self.tree.bind('<Double-1>', lambda e: self.live_edit())
        right = ttk.Frame(body, padding=(8, 0, 0, 0))
        body.add(right, weight=2)
        bg = self.app.style.lookup('TFrame', 'background') or '#f0f0f0'
        self.canvas = tk.Canvas(right, width=300, height=300, highlightthickness=0, background=bg)
        self.canvas.pack(anchor='n')
        self.info = tk.StringVar()
        ttk.Label(right, textvariable=self.info, wraplength=330, justify='left').pack(anchor='w', pady=(6, 6))
        self.edit_btn = ttk.Button(right, text='Live edit in image editor', command=self.live_edit)
        self.edit_btn.pack(fill='x')
        ttk.Button(right, text='Export PNG…', command=self.export_png).pack(fill='x', pady=3)
        ttk.Button(right, text='Remove from mod (back to the game\'s texture)', command=self.revert).pack(fill='x')
        ttk.Label(P, style='Dim.TLabel', wraplength=1000, justify='left', text=(
            'Live edit opens the texture as a PNG in your image editor; every save goes into your current mod and an '
            'open 3D preview refreshes by itself. A texture that is "also used by" other patterns changes those '
            'patterns too. Keep the base texture grey where the colour options should tint it.')).pack(anchor='w')

    # ---- data ----
    def set_name(self):
        lab = self.set_var.get()
        return next((s['name'] for s in self.sets if ClothingColourEditor.set_label(s['name']) == lab),
                    self.sets[0]['name'])

    def mod_has(self, path):
        m = self.app.current_mod(ask=False)
        rel = self.app.rel_game_path(path)
        return bool(m and rel and os.path.isfile(m.path_for(rel)))

    def on_set(self):
        self.rows = pattern_textures(self.sets, self.set_name(), os.path.dirname(self.model))
        self.tree.delete(*self.tree.get_children())
        own = ''
        if any(e['common'] for e in self.rows):
            own = self.tree.insert('', 'end', 'own', text='This item', open=True)
            common = self.tree.insert('', 'end', 'common', open=False,
                                      text='Shared game textures (other items use them too)')
        for i, e in enumerate(self.rows):
            if e['common']:
                shared = 'Other items'
            else:
                shared = ', '.join(ClothingColourEditor.set_label(s) for s in e['shared'][:3])
                if len(e['shared']) > 3:
                    shared += ' +%d' % (len(e['shared']) - 3)
            parent = (common if e['common'] else own) if own else ''
            self.tree.insert(parent, 'end', str(i), text=os.path.basename(e['path']),
                             values=(describe_texture(e['roles'][0], e['path']), shared or '-',
                                     'edited' if self.mod_has(e['path']) else ''))
        if self.rows:
            self.tree.selection_set('0')
            self.tree.focus('0')
        else:
            self.info.set('No textures found for this pattern.')

    def refresh_state(self):
        for i, e in enumerate(self.rows):
            if self.tree.exists(str(i)):
                self.tree.set(str(i), 'mod', 'edited' if self.mod_has(e['path']) else '')
        self.show()

    def cur(self):
        sel = self.tree.selection()
        return self.rows[int(sel[0])] if sel and sel[0].isdigit() else None

    def source(self, e):
        m = self.app.current_mod(ask=False)
        rel = self.app.rel_game_path(e['path'])
        if m and rel and os.path.isfile(m.path_for(rel)):
            return m.path_for(rel)
        return e['path']

    def show(self):
        e = self.cur()
        self.canvas.delete('all')
        if not e:
            return
        lines = ['%s' % os.path.basename(e['path']),
                 describe_texture(e['roles'][0], e['path']),
                 'Slot: %s' % ', '.join(e['roles']),
                 'Materials: %s' % ', '.join(e['materials'])]
        try:
            from PIL import ImageTk
            t = zf.Bntx(open(self.source(e), 'rb').read()).textures[0]
            img = t.decode().convert('RGBA')
            lines.insert(1, '%d × %d, %s' % (t.width, t.height, getattr(t, 'format_name', '') or 'BNTX'))
            img.thumbnail((300, 300))
            self._photo = ImageTk.PhotoImage(img)
            self.canvas.create_image(150, 150, image=self._photo)
        except Exception as ex:  # noqa: BLE001
            lines.append('(preview failed: %s)' % ex)
        if e['common']:
            lines.append('Shared game texture: other items use it too.')
        elif e['shared']:
            lines.append('Also used by: %s' % ', '.join(ClothingColourEditor.set_label(s) for s in e['shared']))
        if self.mod_has(e['path']):
            lines.append('Edited in your current mod.')
        self.info.set('\n'.join(lines))

    # ---- actions ----
    def live_edit(self):
        e = self.cur()
        if not e:
            return
        if not self.app.rel_game_path(e['path']):
            messagebox.showerror('Pattern textures', 'This texture is outside the game folder.', parent=self)
            return
        if e['common']:
            if not messagebox.askokcancel(
                    'Pattern textures', '%s is a shared game texture: other clothing items (and possibly the '
                    'player\'s body) use it too.\n\nYour edit will change all of them. Continue?'
                    % os.path.basename(e['path']), icon='warning', parent=self):
                return
        elif e['shared'] and not messagebox.askokcancel(
                'Pattern textures', '%s is also used by %s.\n\nYour edit will change those patterns too.'
                % (os.path.basename(e['path']), ', '.join(ClothingColourEditor.set_label(s) for s in e['shared'])),
                parent=self):
            return
        self.app.live_edit_texture(e['path'], ti=0, layer=0)

    def export_png(self):
        e = self.cur()
        if not e:
            return
        from tkinter import filedialog
        out = filedialog.asksaveasfilename(parent=self, title='Export texture as PNG', defaultextension='.png',
                                           initialfile=os.path.splitext(os.path.basename(e['path']))[0] + '.png',
                                           initialdir=self.app.out_var.get(), filetypes=[('PNG image', '*.png')])
        if not out:
            return
        try:
            zf.Bntx(open(self.source(e), 'rb').read()).textures[0].decode().save(out)
            self.app.status_var.set('Exported %s' % out)
        except Exception as ex:  # noqa: BLE001
            messagebox.showerror('Pattern textures', 'Could not export: %s' % ex, parent=self)

    def revert(self):
        e = self.cur()
        m = self.app.current_mod(ask=False)
        rel = self.app.rel_game_path(e['path']) if e else None
        if not (e and m and rel and os.path.isfile(m.path_for(rel))):
            self.app.status_var.set('That texture is not in your current mod.')
            return
        if not messagebox.askyesno('Pattern textures', 'Remove your edited %s from "%s"? (History keeps a copy.)'
                                   % (os.path.basename(e['path']), m.name), parent=self):
            return
        for png, le in list(self.app.live.items()):    # stop a running live edit of this texture
            if le.get('rel') == rel:
                del self.app.live[png]
        m.remove(rel)
        self.app.mod_changed(m, rel, 'texture removed from the mod')
        self.refresh_state()

    def preview(self):
        self.app.open_viewer(self.model, extra=['--outfit', '--pattern', self.set_name()])

    def colours(self):
        v = {'main': None, 'second': None}
        v.update(self.variant or {})
        v['set'] = self.set_name()
        self.app.edit_clothing_colours(self.model, v)
