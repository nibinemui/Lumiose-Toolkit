"""Text in every language: change one line of game text once and save it into every language the game has.

A line is found in each language by its file and label (labels are the same in every language; a language
whose file has no labels is matched by line number when its file has as many lines). Each language's file is
read from the current mod when the mod has it, else from the game. Only the one line changes: every other line
keeps its exact bytes (the message writer copies unchanged lines as they are)."""
import os
import re

import za_formats as zf
import za_modding as zm

MSG_PREFIX = 'ik_message/dat/'
VAR_RE = re.compile(r'\[VAR [^\]]*\]|\\[rc]')


def split_rel(rel):
    """('English', 'common/x.dat') for 'ik_message/dat/English/common/x.dat', or None."""
    if not rel or not rel.startswith(MSG_PREFIX):
        return None
    parts = rel[len(MSG_PREFIX):].split('/', 1)
    return (parts[0], parts[1]) if len(parts) == 2 and parts[0] else None


def languages(root, mod=None):
    """Every language folder in the game's (and the mod's) message folder."""
    out = set()
    for base in ([root] if root else []) + ([mod.romfs] if mod is not None else []):
        d = os.path.join(base, *MSG_PREFIX.strip('/').split('/'))
        try:
            out.update(x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x)))
        except OSError:
            pass
    return sorted(out)


def _labels(path, fallback=None):
    for p in (path, fallback):
        if not p:
            continue
        tp = p[:-4] + '.tbl'
        if os.path.isfile(tp):
            try:
                with open(tp, 'rb') as f:
                    return zf.parse_tbl(f.read())
            except (OSError, ValueError):
                return []
    return []


def read_line(root, mod, lang, rest, index, label=None, count=None):
    """One language's copy of a line: a dict with lang, rel, src (file read), from_mod, data (its bytes), lines,
    index (the line's number in that file, None if not found), text, stamp (the mod's copy when read, to notice
    later changes) and note (why it can't be changed, or how it was matched)."""
    rel = MSG_PREFIX + lang + '/' + rest
    e = {'lang': lang, 'rel': rel, 'src': None, 'from_mod': False, 'data': None, 'lines': None, 'index': None,
         'text': None, 'note': '', 'stamp': zm.file_stamp(mod.path_for(rel)) if mod is not None else None}
    game = os.path.join(root, *rel.split('/')) if root else None
    cands = ([(mod.path_for(rel), True)] if mod is not None else []) + ([(game, False)] if game else [])
    for p, from_mod in cands:
        if not os.path.isfile(p):
            continue
        try:
            with open(p, 'rb') as f:
                data = f.read()
            ok = zf.is_msg_dat(data)
            lines = zf.decode_msg(data) if ok else None
        except Exception:  # noqa: BLE001 - a damaged file is reported, not raised
            ok, lines = False, None
        if not ok:
            if from_mod:
                e['note'] = "the mod's copy of this file can't be read"
                return e
            continue                        # e.g. an empty file in the dump
        e.update(src=p, from_mod=from_mod, data=data, lines=lines)
        labels = _labels(p, game if from_mod else None)
        if label and labels:
            if index is not None and index < len(labels) and labels[index] == label:
                i = index
            elif label in labels:
                i = labels.index(label)
            else:
                e['note'] = 'this label is not in the file'
                return e
            if i >= len(lines):
                e['note'] = 'this label has no line in the file'
                return e
        elif index is not None and index < len(lines) and (count is None or count == len(lines)):
            i = index
            if label:
                e['note'] = 'matched by line number (no labels)'
        else:
            e['note'] = 'line not found'
            return e
        e['index'], e['text'] = i, lines[i]
        return e
    e['note'] = e['note'] or 'no readable copy of this file'
    return e


def gather(root, mod, rel, index, label=None):
    """The line `index` (label `label`) of the message file `rel` in every language: [read_line dicts]."""
    sp = split_rel(rel)
    if not sp:
        raise zm.ModError('%s is not a game text file (ik_message/dat/<language>/...).' % rel)
    lang0, rest = sp
    first = read_line(root, mod, lang0, rest, index, label)
    count = len(first['lines']) if first['lines'] is not None else None
    out = []
    for lang in languages(root, mod):
        out.append(first if lang == lang0 else read_line(root, mod, lang, rest, index, label, count))
    if lang0 not in languages(root, mod):
        out.insert(0, first)
    return out


def removed_codes(old, new):
    """[VAR …] / \\r / \\c codes of the old text that the new text lacks."""
    return [v for v in VAR_RE.findall(old or '') if v not in new]


def encode_change(e, new):
    """The language's file with only line e['index'] set to `new` (bytes). Checks that it reads back."""
    if e['index'] is None:
        raise zm.ModError('%s: %s' % (e['lang'], e['note'] or 'line not found'))
    lines = list(e['lines'])
    lines[e['index']] = new
    data = zm.encode_msg(lines, e['data'])
    check = zf.decode_msg(data)
    if check != lines:
        bad = next(i for i, (a, b) in enumerate(zip(check, lines)) if a != b)
        raise zm.ModError('%s: line %d did not survive encoding (unsupported characters?)' % (e['lang'], bad))
    return data


def changes(entries, news):
    """[(entry, new text)] of the languages whose text really changes. news: {lang: new text}."""
    return [(e, news[e['lang']]) for e in entries
            if e['index'] is not None and e['lang'] in news and news[e['lang']] != e['text']]


def changed_since_read(mod, entries):
    """Languages whose file in the mod changed since it was read (another window or tool saved it)."""
    return [e['lang'] for e in entries if zm.file_stamp(mod.path_for(e['rel'])) != e['stamp']]


def save(mod, todo):
    """Write every changed language's file into the mod. Every file is built and checked before the first one
    is written, so a problem leaves the mod as it was. Returns [(entry, new text, file bytes)]."""
    built = [(e, new, encode_change(e, new)) for e, new in todo]
    for e, _, data in built:
        mod.add_bytes(e['rel'], data)
    return built


def after_save(mod, built):
    """Bring the entries up to date with what save() wrote (the window keeps showing the mod's copies)."""
    for e, new, data in built:
        lines = list(e['lines'])
        lines[e['index']] = new
        e.update(data=data, lines=lines, text=new, src=mod.path_for(e['rel']), from_mod=True,
                 stamp=zm.file_stamp(mod.path_for(e['rel'])))


# ---------------------------------------------------------------------------------------------------------
# the window
# ---------------------------------------------------------------------------------------------------------

def _show(t):
    return (t or '').replace('\\n', '⏎ ')


class SyncWindow:
    """'Apply to all languages…' of the text editor: one line in every language, one new text for all of them
    (or one per language), saved into the current mod in one go."""

    def __init__(self, editor, index):
        import tkinter as tk
        from tkinter import ttk
        self.ed = editor
        self.app = editor.app
        self.index = index
        self.label = editor.labels[index] if index < len(editor.labels) else None
        self.mod = self.app.current_mod(ask=False)
        self.root = os.path.normpath(self.app.root_var.get())
        self.lang0 = split_rel(editor.rel)[0]
        self.entries = gather(self.root, self.mod, editor.rel, index, self.label)
        self.news = {e['lang']: e['text'] for e in self.entries if e['index'] is not None}
        typed = editor.edit.get('1.0', 'end-1c').replace('\r', '').replace('\n', '\\n')
        if self.lang0 in self.news and editor.cur == index:
            self.news[self.lang0] = typed
        self.sel = None

        w = self.win = tk.Toplevel(editor)
        w.title('Apply to all languages - %s' % (self.label or 'line %d' % index))
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        w.geometry('%dx%d' % (min(1000, sw - 80), min(700, sh - 120)))
        w.transient(editor)
        pad = ttk.Frame(w, padding=(10, 8, 10, 4))
        pad.pack(fill='x')
        ttk.Label(pad, text='%s   ·   %s   (line %d in %s)' % (
            split_rel(editor.rel)[1], self.label or '(no label)', index, self.lang0)).pack(anchor='w')
        ttk.Label(pad, style='Dim.TLabel', wraplength=960, justify='left', text=(
            'Type the new text once and press "Put in every language", or pick a language below and give it its '
            'own text. Only this line changes; every other line of each file stays exactly as it is. Line breaks '
            'become \\n; keep [VAR …] codes (blue) - they insert names and numbers.')).pack(anchor='w', pady=(2, 6))
        st = self.app.style
        bg = st.lookup('Treeview', 'fieldbackground') or 'white'
        fg = st.lookup('Treeview', 'foreground') or 'black'

        def textbox(parent, h):
            t = tk.Text(parent, height=h, wrap='word', undo=True, font=('Segoe UI', 11), background=bg,
                        foreground=fg, insertbackground=fg, borderwidth=0)
            t.tag_configure('var', foreground='#4fa3ff')
            t.bind('<KeyRelease>', lambda ev: self.highlight(t))
            return t

        ttk.Label(w, text='Text for every language:', padding=(10, 0)).pack(anchor='w')
        self.all_box = textbox(w, 3)
        self.all_box.pack(fill='x', padx=10)
        self.all_box.insert('1.0', (self.news.get(self.lang0) or '').replace('\\n', '\n'))
        self.highlight(self.all_box)
        self.all_box.bind('<Control-Return>', lambda ev: (self.put_all(), 'break')[1])
        b = ttk.Frame(w, padding=(10, 4))
        b.pack(fill='x')
        ttk.Button(b, text='Put in every language  (Ctrl+Enter)', command=self.put_all).pack(side='left')
        ttk.Button(b, text='Keep every language as it is', command=self.revert_all).pack(side='left', padx=4)

        mid = ttk.Frame(w, padding=(10, 4))
        mid.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(mid, columns=('cur', 'new'), selectmode='browse')
        self.tree.heading('#0', text='Language')
        self.tree.heading('cur', text='Text now')
        self.tree.heading('new', text='New text')
        self.tree.column('#0', width=150, stretch=False)
        self.tree.column('cur', width=380)
        self.tree.column('new', width=380)
        self.tree.tag_configure('changed', foreground='#e8a33c')
        self.tree.tag_configure('off', foreground='#888888')
        sb = ttk.Scrollbar(mid, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', lambda ev: self.select())

        self.one_title = tk.StringVar(value='Pick a language above to give it its own text.')
        ttk.Label(w, textvariable=self.one_title, style='Dim.TLabel', padding=(10, 0)).pack(anchor='w')
        self.one_box = textbox(w, 3)
        self.one_box.pack(fill='x', padx=10)
        self.one_box.bind('<KeyRelease>', lambda ev: (self.highlight(self.one_box), self.take_one()), add='+')
        ob = ttk.Frame(w, padding=(10, 4))
        ob.pack(fill='x')
        ttk.Button(ob, text='Keep this language as it is', command=self.revert_one).pack(side='left')

        bar = ttk.Frame(w, padding=(10, 6, 10, 10))
        bar.pack(fill='x')
        self.status = tk.StringVar()
        ttk.Label(bar, textvariable=self.status).pack(side='left')
        ttk.Button(bar, text='Close', command=self.close).pack(side='right')
        ttk.Button(bar, text='Save to current mod', command=self.save).pack(side='right', padx=4)
        w.bind('<Escape>', lambda ev: self.close())
        w.protocol('WM_DELETE_WINDOW', self.close)
        self.fill()
        self.all_box.focus_set()

    # ---- display ----
    def highlight(self, box):
        box.tag_remove('var', '1.0', 'end')
        s = box.get('1.0', 'end-1c')
        for m in VAR_RE.finditer(s):
            box.tag_add('var', '1.0+%dc' % m.start(), '1.0+%dc' % m.end())

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        for e in self.entries:
            lang = e['lang'] + ('  [mod]' if e['from_mod'] else '')
            if e['index'] is None:
                self.tree.insert('', 'end', e['lang'], text=lang, values=('(%s)' % e['note'], ''), tags=('off',))
                continue
            new = self.news[e['lang']]
            ch = new != e['text']
            self.tree.insert('', 'end', e['lang'], text=lang,
                             values=(_show(e['text']), _show(new) if ch else '(unchanged)'),
                             tags=('changed',) if ch else ())
        if self.sel and self.tree.exists(self.sel):
            self.tree.selection_set(self.sel)
        self.count()

    def count(self):
        n = len(changes(self.entries, self.news))
        off = [e['lang'] for e in self.entries if e['index'] is None]
        self.status.set('%d language(s) will change%s.' % (
            n, ('  ·  can\'t change: ' + ', '.join(off)) if off else ''))

    def row(self, lang):
        e = next(x for x in self.entries if x['lang'] == lang)
        new = self.news.get(lang)
        if self.tree.exists(lang) and e['index'] is not None:
            ch = new != e['text']
            self.tree.item(lang, values=(_show(e['text']), _show(new) if ch else '(unchanged)'),
                           tags=('changed',) if ch else ())

    # ---- editing ----
    def put_all(self):
        t = self.all_box.get('1.0', 'end-1c').replace('\r', '').replace('\n', '\\n')
        for lang in self.news:
            self.news[lang] = t
        self.fill()
        self.show_one()

    def revert_all(self):
        for e in self.entries:
            if e['index'] is not None:
                self.news[e['lang']] = e['text']
        self.fill()
        self.show_one()

    def select(self):
        sel = self.tree.selection()
        self.sel = sel[0] if sel and sel[0] in self.news else None
        self.show_one()

    def show_one(self):
        self.one_box.delete('1.0', 'end')
        if self.sel is None:
            self.one_title.set('Pick a language above to give it its own text.')
            return
        self.one_title.set('Text for %s:' % self.sel)
        self.one_box.insert('1.0', self.news[self.sel].replace('\\n', '\n'))
        self.one_box.edit_reset()
        self.highlight(self.one_box)

    def take_one(self):
        if self.sel is None:
            return
        self.news[self.sel] = self.one_box.get('1.0', 'end-1c').replace('\r', '').replace('\n', '\\n')
        self.row(self.sel)
        self.count()

    def revert_one(self):
        if self.sel is None:
            return
        e = next(x for x in self.entries if x['lang'] == self.sel)
        self.news[self.sel] = e['text']
        self.show_one()
        self.row(self.sel)
        self.count()

    # ---- saving ----
    def save(self):
        from tkinter import messagebox
        todo = changes(self.entries, self.news)
        if not todo:
            messagebox.showinfo('Apply to all languages', 'No language has a new text yet.', parent=self.win)
            return
        m = self.app.current_mod()
        if not m:
            return
        if self.mod is None or os.path.normcase(m.folder) != os.path.normcase(self.mod.folder):
            # the texts were read with another (or no) current mod: read them again from this one
            self.mod = m
            news = dict(self.news)
            self.entries = gather(self.root, m, self.ed.rel, self.index, self.label)
            self.news = {e['lang']: news.get(e['lang'], e['text']) for e in self.entries if e['index'] is not None}
            self.fill()
            messagebox.showinfo('Apply to all languages', 'The current mod is now "%s": the text now in each '
                                'language was read again from it. Check the list and press Save again.' % m.name,
                                parent=self.win)
            return
        lost = [(e['lang'], removed_codes(e['text'], new)) for e, new in todo]
        lost = [(lang, c) for lang, c in lost if c]
        if lost and not messagebox.askyesno('Apply to all languages', 'These codes were removed:\n\n%s\n\nThe game '
                                            'may show the line wrongly. Save anyway?' % '\n'.join(
                                                '%s: %s' % (lang, ', '.join(c[:4])) for lang, c in lost[:12]),
                                            icon='warning', parent=self.win):
            return
        stale = changed_since_read(m, [e for e, _ in todo])
        if stale and not messagebox.askyesno('Apply to all languages', 'This file changed in "%s" since this window '
                                             'read it (another window or tool saved it), in: %s.\n\nSaving replaces '
                                             'those changes with this window\'s copy. Save anyway? (No: nothing is '
                                             'saved - reopen this window to get the current version.)'
                                             % (m.name, ', '.join(stale)), icon='warning', parent=self.win):
            return
        before = {e['lang']: e['stamp'] for e, _ in todo}

        def done(built):
            after_save(m, built)
            self.ed.synced(self.index, {e['lang']: new for e, new, _ in built}, m, before)
            self.app.mod_changed(m, None, 'line %s saved in %d language(s)' % (self.label or self.index, len(built)))
            if self.win.winfo_exists():
                self.fill()
                self.status.set('Saved in %d language(s) into "%s".' % (len(built), m.name))
        self.app.run_bg('Saving text in %d language(s)' % len(todo), lambda: save(m, todo), done)

    def close(self):
        from tkinter import messagebox
        if changes(self.entries, self.news) and not messagebox.askyesno(
                'Apply to all languages', 'Close without saving the new texts?', parent=self.win):
            return
        self.win.destroy()


def open_window(editor, index):
    return SyncWindow(editor, index)
