"""Material colour editor (Trinity .trmtr) with live 3D preview.

Edits colour (vec4) and number parameters of every material in a .trmtr file and saves the
result into the current mod. A 3D viewer opened with the mod reloads automatically on save.
Colours in the game are linear; the colour picker works in normal (sRGB) colours.
"""
import json
import os
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk

import za_modding as zm

COLOR_HINTS = ('Color', 'Colour')
NUMBER_HINTS = ('LayerMaskScale', 'Saturation', 'Darkness', 'Intensity', 'Metallic', 'Roughness',
                'Emission', 'Rim', 'Specular', 'Alpha', 'Discard', 'NormalHeight')


def lin_to_srgb(c):
    c = max(0.0, min(1.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def srgb_to_lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


class MaterialEditor(tk.Toplevel):
    def __init__(self, app, trmtr_path, model_path=None):
        super().__init__(app)
        self.app = app
        self.path = trmtr_path
        self.model_path = model_path
        self.rel = app.rel_game_path(trmtr_path)
        self.title('Material colours - ' + os.path.basename(trmtr_path))
        self.geometry('980x640')
        self.configure(background=app.style.lookup('.', 'background'))
        mod = app.current_mod(ask=False)
        src = mod.path_for(self.rel) if (mod and self.rel and os.path.isfile(mod.path_for(self.rel))) else trmtr_path
        self.source_note = 'editing the version in mod "%s"' % mod.name if src != trmtr_path else 'editing the game\'s original'
        self.data = json.loads(zm.to_json(src, zm.schema_for(trmtr_path)))
        self.mats = self.data.get('Materials', [])
        self.rows = []
        self.build()
        if self.mats:
            self.mlist.selection_set(0)
            self.show_material()

    def build(self):
        top = ttk.Frame(self)
        top.pack(fill='x', padx=8, pady=6)
        ttk.Label(top, text='%s  (%s)' % (os.path.basename(self.path), self.source_note), style='Head.TLabel').pack(side='left')
        ttk.Button(top, text='Save to current mod', command=self.save).pack(side='right')
        if self.model_path:
            ttk.Button(top, text='Open live 3D preview', command=self.preview).pack(side='right', padx=4)
        body = ttk.PanedWindow(self, orient='horizontal')
        body.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        left = ttk.Frame(body)
        body.add(left, weight=1)
        self.mlist = tk.Listbox(left, exportselection=False, activestyle='none', borderwidth=0, highlightthickness=0)
        self.mlist.pack(fill='both', expand=True)
        for m in self.mats:
            self.mlist.insert('end', m.get('Name', '?'))
        self.mlist.bind('<<ListboxSelect>>', lambda e: self.show_material())
        right = ttk.Frame(body)
        body.add(right, weight=4)
        self.canvas = tk.Canvas(right, highlightthickness=0, background=self.app.style.lookup('.', 'background'))
        sb = ttk.Scrollbar(right, orient='vertical', command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.canvas.bind_all('<MouseWheel>', self._wheel)
        self.status = tk.StringVar(value='Colours are stored as linear RGB (0-1, can go above 1 for glow). '
                                         'Click a swatch to pick a colour.')
        ttk.Label(self, textvariable=self.status, style='Dim.TLabel').pack(fill='x', padx=8, pady=(0, 6))
        theme = self.app.style
        self.mlist.configure(background=theme.lookup('Treeview', 'fieldbackground') or '#252526',
                             foreground=theme.lookup('.', 'foreground') or '#e3e3e3',
                             selectbackground=theme.lookup('Treeview', 'background', ['selected']) or '#264f78')

    def _wheel(self, e):
        if self.winfo_exists() and str(self.focus_get() or '').startswith(str(self)):
            self.canvas.yview_scroll(-int(e.delta / 120) * 3, 'units')

    def current(self):
        sel = self.mlist.curselection()
        return self.mats[sel[0]] if sel else None

    def show_material(self):
        for w in self.inner.winfo_children():
            w.destroy()
        m = self.current()
        if not m:
            return
        r = 0
        ttk.Label(self.inner, text='Colours', style='Head.TLabel').grid(row=r, column=0, sticky='w', pady=(4, 2))
        r += 1
        for p in m.get('Vec4fParams', []):
            name = p.get('Name', '')
            if not any(h in name for h in COLOR_HINTS):
                continue
            v = p.setdefault('Value', {})
            sw = tk.Canvas(self.inner, width=46, height=18, highlightthickness=1, highlightbackground='#555', cursor='hand2')
            sw.grid(row=r, column=0, sticky='w', padx=(0, 6), pady=1)
            ttk.Label(self.inner, text=name, width=26).grid(row=r, column=1, sticky='w')
            vars_ = []
            for c, key in enumerate('XYZW'):
                var = tk.StringVar(value='%.4g' % v.get(key, 0.0))
                e = ttk.Entry(self.inner, textvariable=var, width=8)
                e.grid(row=r, column=2 + c, padx=1)
                e.bind('<FocusOut>', lambda ev, p=p, vs=vars_, s=sw: self.from_entries(p, vs, s))
                e.bind('<Return>', lambda ev, p=p, vs=vars_, s=sw: self.from_entries(p, vs, s))
                vars_.append(var)
            sw.bind('<Button-1>', lambda ev, p=p, vs=vars_, s=sw: self.pick(p, vs, s))
            self.paint(sw, v)
            r += 1
        ttk.Label(self.inner, text='Numbers', style='Head.TLabel').grid(row=r, column=0, sticky='w', pady=(10, 2))
        r += 1
        for p in m.get('FloatParams', []):
            name = p.get('Name', '')
            if not any(h in name for h in NUMBER_HINTS):
                continue
            ttk.Label(self.inner, text=name, width=26).grid(row=r, column=1, sticky='w')
            var = tk.StringVar(value='%.4g' % p.get('Value', 0.0))
            e = ttk.Entry(self.inner, textvariable=var, width=10)
            e.grid(row=r, column=2, columnspan=2, sticky='w', padx=1)

            def commit(ev, p=p, var=var):
                try:
                    p['Value'] = float(var.get())
                except ValueError:
                    var.set('%.4g' % p.get('Value', 0.0))
            e.bind('<FocusOut>', commit)
            e.bind('<Return>', commit)
            r += 1
        ttk.Label(self.inner, text='\nTip: in Pokémon materials BaseColorLayer1-4 are the colours of the 4 regions\n'
                                   'painted in the layer mask (the _lym texture); BaseColor tints the rest.',
                  style='Dim.TLabel', justify='left').grid(row=r, column=0, columnspan=6, sticky='w')

    def paint(self, sw, v):
        rgb = tuple(int(lin_to_srgb(v.get(k, 0.0)) * 255 + 0.5) for k in 'XYZ')
        sw.configure(background='#%02x%02x%02x' % rgb)

    def from_entries(self, p, vars_, sw):
        try:
            p['Value'] = {k: float(var.get()) for k, var in zip('XYZW', vars_)}
        except ValueError:
            for k, var in zip('XYZW', vars_):
                var.set('%.4g' % p['Value'].get(k, 0.0))
            return
        self.paint(sw, p['Value'])

    def pick(self, p, vars_, sw):
        v = p.get('Value', {})
        cur = '#%02x%02x%02x' % tuple(int(lin_to_srgb(v.get(k, 0.0)) * 255 + 0.5) for k in 'XYZ')
        res = colorchooser.askcolor(color=cur, parent=self, title=p.get('Name', 'Colour'))
        if not res or not res[0]:
            return
        r, g, b = (srgb_to_lin(c / 255.0) for c in res[0])
        v.update({'X': round(r, 5), 'Y': round(g, 5), 'Z': round(b, 5)})
        v.setdefault('W', 1.0)
        p['Value'] = v
        for k, var in zip('XYZW', vars_):
            var.set('%.4g' % v.get(k, 0.0))
        self.paint(sw, v)

    def save(self):
        mod = self.app.current_mod()
        if not mod or not self.rel:
            return
        self.focus_set()          # commit an entry that is being typed in
        try:
            zm.from_json(json.dumps(self.data), zm.schema_for(self.path), mod.path_for(self.rel))
        except zm.ModError as e:
            messagebox.showerror('Material editor', str(e), parent=self)
            return
        self.status.set('Saved into mod "%s" - an open live preview updates in a moment.' % mod.name)
        self.app.mod_changed(mod, self.rel, 'materials saved')

    def preview(self):
        self.app.open_viewer(self.model_path, with_mod=True)
