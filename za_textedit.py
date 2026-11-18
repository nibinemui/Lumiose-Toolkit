"""In-app editor for game text (.dat message files): search, edit lines, save into the current mod."""
import os
import re
import tkinter as tk
from tkinter import messagebox, ttk

import za_formats as zf
import za_modding as zm

VAR_RE = re.compile(r'\[VAR [^\]]*\]|\\[rc]')


def _show(t):
    return t.replace('\\n', '⏎ ')


class TextEditor(tk.Toplevel):
    def __init__(self, app, path, line=None):
        super().__init__(app)
        self.app = app
        self.path = path
        self.rel = app.rel_game_path(path)
        with open(path, 'rb') as f:
            self.template = f.read()
        self.original = zf.decode_msg(self.template)
        tp = os.path.splitext(path)[0] + '.tbl'
        try:
            self.labels = zf.parse_tbl(open(tp, 'rb').read()) if os.path.isfile(tp) else []
        except (OSError, ValueError):
            self.labels = []
        self.texts = list(self.original)
        m = app.current_mod(ask=False)
        self.from_mod = False
        if m and self.rel and os.path.isfile(m.path_for(self.rel)):
            try:
                with open(m.path_for(self.rel), 'rb') as f:
                    mod_data = f.read()
                mod_lines = zf.decode_msg(mod_data)
                if len(mod_lines) != len(self.texts):
                    # the mod added lines (new item or hairstyle names): edit the mod's file, with its labels
                    self.template, self.original = mod_data, list(mod_lines)
                    mtp = m.path_for(self.rel[:-4] + '.tbl')
                    if os.path.isfile(mtp):
                        try:
                            with open(mtp, 'rb') as f:
                                self.labels = zf.parse_tbl(f.read())
                        except (OSError, ValueError):
                            pass
                self.texts, self.from_mod = mod_lines, True
            except (OSError, ValueError):
                pass
        self.cur = None
        self.dirty = False
        lang = self.rel.split('/')[3] if self.rel and self.rel.startswith('ik_message/dat/') else ''
        self.title('Edit text - %s %s' % (os.path.basename(path), '(%s)' % lang if lang else ''))
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry('%dx%d' % (min(1100, sw - 80), min(760, sh - 120)))
        self.build()
        self.fill()
        if line is not None:
            self.goto(line)
        self.protocol('WM_DELETE_WINDOW', self.close)

    def build(self):
        top = ttk.Frame(self)
        top.pack(fill='x', padx=6, pady=(6, 2))
        ttk.Label(top, text='Find:').pack(side='left')
        self.q = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.q, width=40)
        e.pack(side='left', padx=4)
        e.focus_set()
        self.q.trace_add('write', lambda *a: self.fill())
        self.only_changed = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text='Only changed lines', variable=self.only_changed, command=self.fill).pack(side='left')
        ttk.Button(top, text='Save to current mod', command=self.save).pack(side='right')
        self.count = tk.StringVar()
        ttk.Label(top, textvariable=self.count, style='Dim.TLabel').pack(side='right', padx=8)

        paned = ttk.PanedWindow(self, orient='vertical')
        paned.pack(fill='both', expand=True, padx=6, pady=4)
        tf = ttk.Frame(paned)
        paned.add(tf, weight=3)
        self.tree = ttk.Treeview(tf, columns=('text',), selectmode='browse')
        self.tree.heading('#0', text='Label')
        self.tree.heading('text', text='Text')
        self.tree.column('#0', width=260, stretch=False)
        self.tree.column('text', width=700)
        sb = ttk.Scrollbar(tf, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        self.tree.tag_configure('changed', foreground='#e8a33c')
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.select())

        ef = ttk.Frame(paned)
        paned.add(ef, weight=1)
        self.line_title = tk.StringVar()
        ttk.Label(ef, textvariable=self.line_title, style='Dim.TLabel').pack(anchor='w')
        st = self.app.style
        bg = st.lookup('Treeview', 'fieldbackground') or 'white'
        fg = st.lookup('Treeview', 'foreground') or 'black'
        self.edit = tk.Text(ef, height=6, wrap='word', undo=True, font=('Segoe UI', 11), background=bg,
                            foreground=fg, insertbackground=fg, borderwidth=0)
        self.edit.pack(fill='both', expand=True)
        self.edit.tag_configure('var', foreground='#4fa3ff')
        self.edit.bind('<KeyRelease>', lambda e: self.highlight())
        self.edit.bind('<Control-Return>', lambda e: (self.apply(), 'break')[1])
        bf = ttk.Frame(ef)
        bf.pack(fill='x', pady=4)
        ttk.Button(bf, text='Apply  (Ctrl+Enter)', command=self.apply).pack(side='left')
        ttk.Button(bf, text='Revert line to original', command=self.revert).pack(side='left', padx=4)
        ttk.Label(bf, text='Line breaks become \\n.  Keep [VAR …] codes (blue) - they insert names/numbers.',
                  style='Dim.TLabel').pack(side='left', padx=8)

    def label(self, i):
        return self.labels[i] if i < len(self.labels) else '#%d' % i

    def fill(self):
        q = self.q.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        n = 0
        for i, t in enumerate(self.texts):
            changed = t != self.original[i]
            if self.only_changed.get() and not changed:
                continue
            lab = self.label(i)
            if q and q not in t.lower() and q not in lab.lower():
                continue
            self.tree.insert('', 'end', str(i), text=lab, values=(_show(t),), tags=('changed',) if changed else ())
            n += 1
        nch = sum(a != b for a, b in zip(self.texts, self.original))
        self.count.set('%d of %d lines · %d changed%s' % (n, len(self.texts), nch,
                                                           ' (loaded from the current mod)' if self.from_mod else ''))

    def goto(self, i):
        if not self.tree.exists(str(i)):
            self.q.set('')
        if self.tree.exists(str(i)):
            self.tree.selection_set(str(i))
            self.tree.see(str(i))

    def select(self):
        sel = self.tree.selection()
        if not sel:
            return
        if self.cur is not None:
            self.apply(quiet=True)
        self.cur = int(sel[0])
        self.edit.delete('1.0', 'end')
        self.edit.insert('1.0', self.texts[self.cur].replace('\\n', '\n'))
        self.edit.edit_reset()
        self.highlight()
        self.line_title.set('%s   (line %d)%s' % (self.label(self.cur), self.cur,
                                                  '  ·  original: ' + _show(self.original[self.cur])[:120]
                                                  if self.texts[self.cur] != self.original[self.cur] else ''))

    def highlight(self):
        self.edit.tag_remove('var', '1.0', 'end')
        s = self.edit.get('1.0', 'end-1c')
        for m in VAR_RE.finditer(s):
            self.edit.tag_add('var', '1.0+%dc' % m.start(), '1.0+%dc' % m.end())

    def apply(self, quiet=False):
        if self.cur is None:
            return
        new = self.edit.get('1.0', 'end-1c').replace('\r', '').replace('\n', '\\n')
        if new == self.texts[self.cur]:
            return
        if not quiet:
            missing = [v for v in VAR_RE.findall(self.texts[self.cur]) if v not in new]
            if missing and not messagebox.askyesno(
                    'Edit text', 'These codes were removed: %s\nThe game may show the line wrongly. Apply anyway?'
                    % ', '.join(missing[:5]), parent=self):
                return
        self.texts[self.cur] = new
        self.dirty = True
        if self.tree.exists(str(self.cur)):
            self.tree.item(str(self.cur), values=(_show(new),),
                           tags=('changed',) if new != self.original[self.cur] else ())
        nch = sum(a != b for a, b in zip(self.texts, self.original))
        self.count.set(re.sub(r'\d+ changed', '%d changed' % nch, self.count.get()))

    def revert(self):
        if self.cur is None:
            return
        self.edit.delete('1.0', 'end')
        self.edit.insert('1.0', self.original[self.cur].replace('\\n', '\n'))
        self.highlight()
        self.apply(quiet=True)

    def save(self):
        self.apply(quiet=True)
        m = self.app.current_mod()
        if not m:
            return
        if not self.rel:
            from tkinter import messagebox as mb
            mb.showinfo('Edit text', 'This file is neither in the game folder nor in the current mod, so it has no '
                        'place to be saved.', parent=self)
            return
        texts, template, rel = list(self.texts), self.template, self.rel

        def job():
            data = zm.encode_msg(texts, template)
            check = zf.decode_msg(data)
            bad = [i for i, (a, b) in enumerate(zip(check, texts)) if a != b]
            if bad:
                raise zm.ModError('Line %d did not survive encoding (unsupported characters?)' % bad[0])
            m.add_bytes(rel, data)
            return sum(a != b for a, b in zip(texts, zf.decode_msg(template)))

        def done(n):
            self.dirty = False
            self.app.mod_changed(m, rel, '%d line(s) changed' % n)
            self.title(self.title().split(' - saved')[0] + ' - saved into "%s"' % m.name)
        self.app.run_bg('Saving ' + os.path.basename(rel), job, done)

    def close(self):
        self.apply(quiet=True)
        if self.dirty and not messagebox.askyesno('Edit text', 'Close without saving your changes?', parent=self):
            return
        self.destroy()
