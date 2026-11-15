"""Mod history window: every saved version of a mod file, compare with the game original or the
previous version (text lines, data fields, textures side by side), and restore any version."""
import datetime
import json
import os
import tempfile
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

import za_formats as zf
import za_modding as zm

MAX_LINES = 3000


def _size(n):
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or u == 'GB':
            return ('%d %s' % (n, u)) if u == 'B' else ('%.1f %s' % (n, u))
        n /= 1024.0


def _flatten(o, pre='', out=None):
    out = {} if out is None else out
    if isinstance(o, dict):
        for k, v in o.items():
            _flatten(v, '%s.%s' % (pre, k) if pre else k, out)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            _flatten(v, '%s[%d]' % (pre, i), out)
    else:
        out[pre] = o
    return out


def _json_of(data, rel, game_path):
    schema = zm.schema_for(game_path)
    if not schema:
        return None
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'f' + os.path.splitext(rel)[1])
        with open(p, 'wb') as f:
            f.write(data)
        return json.loads(zm.to_json(p, schema))


def _texture(data, name=''):
    try:
        t = zf.Bntx(data).textures[0]
        return t.decode(0, 0)
    except Exception:  # noqa: BLE001
        return None


def compare(old, new, rel, game_path):
    """-> (text, [(caption, PIL image)]) describing the differences between two versions."""
    if old is None:
        return 'This file is new (the game has no file at this path).', []
    if old == new:
        return 'No difference - this version is identical.', []
    ext = os.path.splitext(rel)[1].lower()
    lines = ['%s:  %s  ->  %s' % (rel, _size(len(old)), _size(len(new))), '']
    if ext == '.dat':
        a, b = zf.decode_msg(old), zf.decode_msg(new)
        tp = os.path.splitext(game_path)[0] + '.tbl'
        try:
            labels = zf.parse_tbl(open(tp, 'rb').read())
        except (OSError, ValueError):
            labels = []
        n = 0
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                n += 1
                if n <= MAX_LINES // 3:
                    lines += [labels[i] if i < len(labels) else '#%d' % i, '   - ' + x, '   + ' + y]
        lines.insert(1, '%d line(s) changed' % n)
        return '\n'.join(lines), []
    if ext == '.bntx':
        a, b = _texture(old), _texture(new)
        return '\n'.join(lines + ['Texture: left = before, right = this version.']), \
            [c for c in (('before', a), ('this version', b)) if c[1] is not None]
    if ext == '.arc':
        try:
            fa, fb = dict(zf.parse_sarc(old)), dict(zf.parse_sarc(new))
        except zf.FormatError:
            fa = fb = {}
        changed = sorted(k for k in set(fa) | set(fb) if fa.get(k) != fb.get(k))
        lines += ['Changed inside the archive:'] + ['  ' + k for k in changed]
        imgs = []
        for k in changed:
            if k.lower().endswith('.bntx') and k in fa and k in fb:
                imgs = [c for c in (('before: ' + k, _texture(fa[k])), ('this version', _texture(fb[k])))
                        if c[1] is not None]
                break
        return '\n'.join(lines), imgs
    try:
        ja, jb = _json_of(old, rel, game_path), _json_of(new, rel, game_path)
    except Exception as e:  # noqa: BLE001
        ja = jb = None
        lines.append('(could not decode as data: %s)' % e)
    if ja is not None and jb is not None:
        fa, fb = _flatten(ja), _flatten(jb)
        keys = sorted(set(fa) | set(fb))
        diff = [k for k in keys if fa.get(k, '∅') != fb.get(k, '∅')]
        lines.append('%d field(s) differ:' % len(diff))
        for k in diff[:MAX_LINES]:
            lines.append('  %s:  %s  ->  %s' % (k, json.dumps(fa.get(k, '(none)'), ensure_ascii=False),
                                                json.dumps(fb.get(k, '(none)'), ensure_ascii=False)))
        if len(diff) > MAX_LINES:
            lines.append('  … %d more' % (len(diff) - MAX_LINES))
        return '\n'.join(lines), []
    n = sum(1 for x, y in zip(old, new) if x != y) + abs(len(old) - len(new))
    lines.append('%d byte(s) differ.' % n)
    return '\n'.join(lines), []


class HistoryWindow(tk.Toplevel):
    def __init__(self, app, mod, rel=None):
        super().__init__(app)
        self.app, self.mod = app, mod
        self.title('History - %s' % mod.name)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry('%dx%d' % (min(1150, sw - 80), min(720, sh - 120)))
        self.photos = []
        top = ttk.Frame(self)
        top.pack(fill='x', padx=6, pady=6)
        ttk.Label(top, text='File:').pack(side='left')
        files = sorted(set(mod.files()) | set(mod.history_files()))
        self.file_var = tk.StringVar(value=rel or (files[0] if files else ''))
        cb = ttk.Combobox(top, textvariable=self.file_var, values=files, state='readonly', width=110)
        cb.pack(side='left', padx=4, fill='x', expand=True)
        cb.bind('<<ComboboxSelected>>', lambda e: self.fill())

        paned = ttk.PanedWindow(self, orient='horizontal')
        paned.pack(fill='both', expand=True, padx=6, pady=(0, 6))
        left = ttk.Frame(paned)
        paned.add(left, weight=1)
        self.tree = ttk.Treeview(left, columns=('size',), selectmode='browse', height=12)
        self.tree.heading('#0', text='Saved version')
        self.tree.heading('size', text='Size')
        self.tree.column('#0', width=230)
        self.tree.column('size', width=80, stretch=False)
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.compare('original'))
        for text, cmd in (('Compare with game original', lambda: self.compare('original')),
                          ('Compare with previous version', lambda: self.compare('previous')),
                          ('Compare with current file', lambda: self.compare('current')),
                          ('Restore this version', self.restore),
                          ('Remove file from mod (use original)', self.remove)):
            ttk.Button(left, text=text, command=cmd).pack(fill='x', pady=1)
        ttk.Label(left, text='A version is saved every time a file in the mod changes\n(up to %d per file).'
                  % zm.HISTORY_MAX, style='Dim.TLabel').pack(anchor='w', pady=4)

        right = ttk.Frame(paned)
        paned.add(right, weight=3)
        self.imgs = ttk.Frame(right)
        self.imgs.pack(fill='x')
        st = app.style
        bg = st.lookup('Treeview', 'fieldbackground') or 'white'
        fg = st.lookup('Treeview', 'foreground') or 'black'
        self.text = tk.Text(right, wrap='none', font=('Consolas', 9), background=bg, foreground=fg, borderwidth=0)
        ys = ttk.Scrollbar(right, orient='vertical', command=self.text.yview)
        self.text.configure(yscrollcommand=ys.set)
        ys.pack(side='right', fill='y')
        self.text.pack(fill='both', expand=True)
        mod.snapshot()
        self.fill()

    @property
    def rel(self):
        return self.file_var.get()

    def game_path(self):
        return os.path.join(self.app.root_var.get(), *self.rel.split('/'))

    def versions(self):
        return self.mod.history(self.rel)

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        vers = self.versions()
        cur = None
        p = self.mod.path_for(self.rel)
        if os.path.isfile(p):
            import hashlib
            cur = hashlib.sha1(open(p, 'rb').read()).hexdigest()
        for i in range(len(vers) - 1, -1, -1):
            v = vers[i]
            t = datetime.datetime.fromtimestamp(v['time']).strftime('%Y-%m-%d %H:%M:%S')
            self.tree.insert('', 'end', str(i), text='%s%s' % (t, '   (current)' if v['sha1'] == cur else ''),
                             values=(_size(v['size']),))
        if not os.path.isfile(p):
            self.text.delete('1.0', 'end')
            self.text.insert('1.0', 'This file is not in the mod right now (the game original is used).\n'
                                    'Pick a version and "Restore this version" to put it back.')
        kids = self.tree.get_children()
        if kids:
            self.tree.selection_set(kids[0])

    def sel(self):
        s = self.tree.selection()
        return self.versions()[int(s[0])] if s else None

    def compare(self, against):
        v = self.sel()
        if not v:
            return
        new = open(v['path'], 'rb').read()
        if against == 'original':
            gp = self.game_path()
            old = open(gp, 'rb').read() if os.path.isfile(gp) else None
        elif against == 'previous':
            vers = self.versions()
            i = int(self.tree.selection()[0])
            if i == 0:
                gp = self.game_path()
                old = open(gp, 'rb').read() if os.path.isfile(gp) else None
            else:
                old = open(vers[i - 1]['path'], 'rb').read()
        else:
            p = self.mod.path_for(self.rel)
            if not os.path.isfile(p):
                messagebox.showinfo('History', 'The file is not in the mod right now.', parent=self)
                return
            old = open(p, 'rb').read()
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', 'Comparing…')
        self.update_idletasks()
        try:
            txt, imgs = compare(old, new, self.rel, self.game_path())
        except Exception as e:  # noqa: BLE001
            txt, imgs = 'Could not compare: %s' % e, []
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', {'original': 'Game original  ->  this version\n',
                                 'previous': 'Previous version  ->  this version\n',
                                 'current': 'Current mod file  ->  this version\n'}[against] + txt)
        for w in self.imgs.winfo_children():
            w.destroy()
        self.photos = []
        for cap, im in imgs:
            im = im.copy()
            im.thumbnail((360, 360))
            ph = ImageTk.PhotoImage(im)
            self.photos.append(ph)
            f = ttk.Frame(self.imgs)
            f.pack(side='left', padx=4, pady=4)
            ttk.Label(f, image=ph).pack()
            ttk.Label(f, text=cap, style='Dim.TLabel').pack()

    def restore(self):
        v = self.sel()
        if not v:
            return
        if not messagebox.askyesno('History', 'Put this version back into the mod?\n(The current file is kept in '
                                   'the history too.)', parent=self):
            return
        self.mod.snapshot()
        self.mod.restore(self.rel, v['path'])
        self.mod.snapshot('restored')
        self.app.refresh_mods()
        self.app.status_var.set('Restored %s in "%s"' % (self.rel, self.mod.name))
        self.fill()

    def remove(self):
        if not os.path.isfile(self.mod.path_for(self.rel)):
            return
        if not messagebox.askyesno('History', 'Remove %s from the mod? The game original is used again; the '
                                   'saved versions stay here.' % self.rel, parent=self):
            return
        self.mod.snapshot()
        self.mod.remove(self.rel)
        self.app.refresh_mods()
        self.fill()
