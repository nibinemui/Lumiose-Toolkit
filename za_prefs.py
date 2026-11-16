"""Preferences window: appearance, image editor for live edits, 3D view, mods, updates."""
import os
import tkinter as tk
from tkinter import filedialog, ttk

EDITOR_MODES = [
    ('minipaint', 'Built-in editor: miniPaint',
     'Opens in a Lumiose Toolkit window. Works offline. Layers, brushes, selections, filters.'),
    ('photopea', 'Photopea (online)',
     'Opens photopea.com in a Lumiose Toolkit window - a Photoshop-like editor by a third party. Needs an '
     'internet connection; the free version shows ads.'),
    ('edit', 'Windows\' "Edit" program',
     'Whatever Windows uses to edit PNG images - Paint unless you changed it.'),
    ('program', 'Another program',
     'GIMP, Paint.NET, Krita, Photoshop… pick its .exe below.'),
]
DEFAULT_MODE = 'minipaint'
UI_SIZES = [(0.9, '90%'), (1.0, '100%'), (1.15, '115%'), (1.3, '130%'), (1.5, '150%')]
TEX_SIZES = [(1024, 'Normal (1024 px, faster)'), (2048, 'High (2048 px)'), (4096, 'Full (up to 4096 px, slow)')]
HISTORY_SIZES = [10, 30, 100]


def editor_mode(settings):
    """The chosen editor; older settings with only a program path mean "Another program"."""
    m = settings.get('image_editor_mode')
    if m in [k for k, _l, _d in EDITOR_MODES]:
        return m
    return 'program' if settings.get('image_editor') else DEFAULT_MODE


class PreferencesWindow(tk.Toplevel):
    def __init__(self, app, tab=None):
        super().__init__(app)
        self.app = app
        s = app.settings
        self.title('Preferences')
        self.transient(app)
        self.resizable(False, False)
        bg = app.style.lookup('TFrame', 'background') or None
        if bg:
            self.configure(background=bg)
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill='both', expand=True)
        nb = ttk.Notebook(outer)
        nb.pack(fill='both', expand=True)
        self.tabs = {}

        def page(key, title):
            f = ttk.Frame(nb, padding=14)
            nb.add(f, text='  %s  ' % title)
            self.tabs[key] = f
            return f

        def head(f, text, first=False):
            ttk.Label(f, text=text, style='Head.TLabel').pack(anchor='w', pady=(0 if first else 12, 2))

        def note(f, text, pad=22):
            ttk.Label(f, text=text, style='Dim.TLabel', wraplength=520, justify='left').pack(
                anchor='w', padx=(pad, 0), pady=(0, 6))

        # --- General
        f = page('general', 'General')
        head(f, 'Theme', True)
        self.theme = tk.StringVar(value='dark' if app.dark_var.get() else 'light')
        for v, label in (('dark', 'Dark'), ('light', 'Light')):
            ttk.Radiobutton(f, text=label, value=v, variable=self.theme, command=self.set_theme).pack(anchor='w')
        head(f, 'Interface size')
        row = ttk.Frame(f)
        row.pack(anchor='w')
        cur = s.get('ui_scale', 1.0)
        self.ui = tk.StringVar(value=next((lab for v, lab in UI_SIZES if abs(v - cur) < 0.01), '100%'))
        cb = ttk.Combobox(row, textvariable=self.ui, state='readonly', width=8, values=[lab for _v, lab in UI_SIZES])
        cb.pack(side='left')
        cb.bind('<<ComboboxSelected>>', lambda e: self.changed())
        note(f, 'Text and controls. Takes effect the next time Lumiose Toolkit starts.', pad=0)
        head(f, 'Updates')
        self.updates = tk.BooleanVar(value=s.get('check_updates', True))
        ttk.Checkbutton(f, text='Check GitHub for a new version when the program starts', variable=self.updates,
                        command=self.changed).pack(anchor='w')

        # --- Image editor
        f = page('editor', 'Image editor')
        head(f, 'Image editor for live texture editing', True)
        note(f, 'Used by "Live edit in image editor" and in the pattern textures window. Every save goes into your '
                'current mod by itself.', pad=0)
        self.mode = tk.StringVar(value=editor_mode(s))
        for key, label, desc in EDITOR_MODES:
            ttk.Radiobutton(f, text=label, value=key, variable=self.mode, command=self.changed).pack(anchor='w')
            note(f, desc)
        row = ttk.Frame(f)
        row.pack(fill='x', padx=(22, 0))
        self.prog = tk.StringVar(value=s.get('image_editor', ''))
        self.prog_entry = ttk.Entry(row, textvariable=self.prog, width=52)
        self.prog_entry.pack(side='left', fill='x', expand=True)
        self.prog_entry.bind('<FocusOut>', lambda e: self.changed())
        self.browse = ttk.Button(row, text='Browse…', command=self.pick)
        self.browse.pack(side='left', padx=(4, 0))

        # --- 3D view
        f = page('3d', '3D view')
        head(f, 'Models', True)
        self.auto3d = tk.BooleanVar(value=s.get('auto_3d', True))
        ttk.Checkbutton(f, text='Show a model in 3D when you click it in the file list', variable=self.auto3d,
                        command=self.changed).pack(anchor='w')
        note(f, 'Off: clicking a model shows its parts, materials and bones as text; "Show in 3D" still works.')
        head(f, "Try on, Play in 3D, colour variants and a texture's model open")
        self.place = tk.StringVar(value=s.get('viewer_place', 'pane'))
        ttk.Radiobutton(f, text='In the preview pane (next to the file list)', value='pane', variable=self.place,
                        command=self.changed).pack(anchor='w')
        ttk.Radiobutton(f, text='In their own window', value='window', variable=self.place,
                        command=self.changed).pack(anchor='w')
        head(f, 'Texture quality')
        cur = s.get('viewer_texsize', 1024)
        self.tex = tk.StringVar(value=next((lab for v, lab in TEX_SIZES if v == cur), TEX_SIZES[0][1]))
        cb = ttk.Combobox(f, textvariable=self.tex, state='readonly', width=28, values=[lab for _v, lab in TEX_SIZES])
        cb.pack(anchor='w')
        cb.bind('<<ComboboxSelected>>', lambda e: self.changed())
        note(f, 'Higher shows sharper textures but loads slower. Applies to 3D views opened from now on.', pad=0)

        # --- Mods
        f = page('mods', 'Mods')
        head(f, 'History', True)
        row = ttk.Frame(f)
        row.pack(anchor='w')
        ttk.Label(row, text='Versions kept per mod file:').pack(side='left')
        self.hist = tk.StringVar(value=str(s.get('history_max', 30)))
        cb = ttk.Combobox(row, textvariable=self.hist, state='readonly', width=6, values=[str(n) for n in HISTORY_SIZES])
        cb.pack(side='left', padx=6)
        cb.bind('<<ComboboxSelected>>', lambda e: self.changed())
        note(f, 'Every change to a mod file is kept as a version you can compare and restore (Mods tab > History). '
                'Older versions beyond this number are deleted.', pad=0)

        bar = ttk.Frame(outer)
        bar.pack(fill='x', pady=(10, 0))
        ttk.Button(bar, text='Close', command=self.destroy).pack(side='right')
        self.msg = tk.StringVar()
        ttk.Label(bar, textvariable=self.msg, style='Dim.TLabel').pack(side='left')
        self.bind('<Escape>', lambda e: self.destroy())
        if tab in self.tabs:
            nb.select(self.tabs[tab])
        self.sync()

    def sync(self):
        st = ['!disabled'] if self.mode.get() == 'program' else ['disabled']
        self.prog_entry.state(st)
        self.browse.state(st)

    def pick(self):
        cur = self.prog.get()
        p = filedialog.askopenfilename(parent=self, title='Image editor program',
                                       initialdir=os.path.dirname(cur) if cur else os.environ.get('ProgramFiles', 'C:\\'),
                                       filetypes=[('Programs', '*.exe')])
        if p:
            self.prog.set(os.path.normpath(p))
            self.changed()

    def set_theme(self):
        self.app.dark_var.set(self.theme.get() == 'dark')
        self.app.apply_theme()
        self.changed()

    def changed(self):
        self.sync()
        s = self.app.settings
        s['image_editor_mode'] = self.mode.get()
        s['image_editor'] = self.prog.get().strip()
        s['check_updates'] = self.updates.get()
        s['auto_3d'] = self.auto3d.get()
        s['viewer_place'] = self.place.get()
        s['viewer_texsize'] = next((v for v, lab in TEX_SIZES if lab == self.tex.get()), 1024)
        new_ui = next((v for v, lab in UI_SIZES if lab == self.ui.get()), 1.0)
        restart = abs(new_ui - s.get('ui_scale', 1.0)) > 0.01
        s['ui_scale'] = new_ui
        try:
            s['history_max'] = int(self.hist.get())
            import za_modding
            za_modding.HISTORY_MAX = s['history_max']
        except ValueError:
            pass
        self.app.save_settings()
        if self.mode.get() == 'program' and not os.path.isfile(s['image_editor']):
            self.msg.set('Pick the editor\'s .exe - until then, Windows\' Edit program is used.')
        elif restart:
            self.msg.set('Saved. The new interface size applies after a restart.')
        else:
            self.msg.set('Saved.')
