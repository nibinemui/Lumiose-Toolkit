"""Search all game text: every line of every message file (dialogue, item and move descriptions, menus), in one
language or all of them, in one search.

The index (text_index.json.gz next to the program) holds each message file's labels and lines. It is built once in
the background (about 5 seconds per language) and after that only files whose size or date changed are read again.
Text the current mod changed is searched in the mod's version, and message files only the mod has are searched too.

Search: every word must match, anywhere in the line or its label, without accents or case ("pokemon" finds
Pokémon). Put words in quotes to find them as one phrase."""
import gzip
import json
import os
import re
import threading
import unicodedata

import za_formats as zf
import za_modding as zm

INDEX_PATH = os.path.join(zm.APP_DIR, 'text_index.json.gz')
VERSION = 1
MSG_DIR = 'ik_message/dat'
ALL = 'All languages'
LIMIT = 2000


def fold(s):
    """Lower case without accents."""
    s = str(s)
    if s.isascii():
        return s.lower()
    s = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in s if not unicodedata.combining(c)).lower()


def languages(root):
    d = os.path.join(root, *MSG_DIR.split('/'))
    try:
        return sorted(x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x)))
    except OSError:
        return []


def _files(base):
    """{path inside the message folder ('English/common/x.dat'): full path} of every .dat under base."""
    out = {}
    for dp, dn, fn in os.walk(base):
        for f in fn:
            if f.endswith('.dat'):
                p = os.path.join(dp, f)
                out[os.path.relpath(p, base).replace(os.sep, '/')] = p
    return out


def _stamp(path):
    try:
        st = os.stat(path)
        tp = path[:-4] + '.tbl'
        t2 = os.stat(tp).st_mtime_ns if os.path.isfile(tp) else 0
        return [st.st_size, st.st_mtime_ns, t2]
    except OSError:
        return None


def read_file(path, labels_from=None):
    """(labels, lines) of a message file, or None if it isn't one. labels_from: the game's copy, whose .tbl
    labels are used when this file has none next to it (a mod usually only has the .dat)."""
    with open(path, 'rb') as f:
        d = f.read()
    if not zf.is_msg_dat(d):
        return None
    tp = path[:-4] + '.tbl'
    if not os.path.isfile(tp) and labels_from:
        tp = labels_from[:-4] + '.tbl'
    try:
        labels = zf.parse_tbl(open(tp, 'rb').read()) if os.path.isfile(tp) else []
    except (OSError, ValueError):
        labels = []
    return labels, zf.decode_msg(d)


def load_index():
    try:
        with gzip.open(INDEX_PATH, 'rt', encoding='utf-8') as f:
            idx = json.load(f)
        if idx.get('version') == VERSION:
            return idx
    except (OSError, ValueError, EOFError):
        pass
    return None


def save_index(idx):
    tmp = INDEX_PATH + '.tmp'
    with gzip.open(tmp, 'wt', encoding='utf-8', compresslevel=5) as f:
        json.dump(idx, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, INDEX_PATH)


def build(root, langs, progress=None, cancel=None, have=None):
    """Index of the game's message files for `langs` (others already in the index are kept). `have`: the index
    already in memory (used instead of the saved one, which may be missing if it couldn't be written)."""
    root = os.path.normpath(root)
    idx = have or load_index()
    if not idx or os.path.normcase(idx.get('root', '')) != os.path.normcase(root):
        idx = {'version': VERSION, 'root': root, 'files': {}}
    base = os.path.join(root, *MSG_DIR.split('/'))
    todo, changed = [], False
    for lang in langs:
        for rel, p in _files(os.path.join(base, lang)).items():
            rel = lang + '/' + rel
            st = _stamp(p)
            have = idx['files'].get(rel)
            if not have or have[0] != st:
                todo.append((rel, p, st))
        prefix = lang + '/'
        gone = [r for r in idx['files'] if r.startswith(prefix) and not os.path.isfile(os.path.join(base, *r.split('/')))]
        for r in gone:
            del idx['files'][r]
            changed = True
    for k, (rel, p, st) in enumerate(todo):
        if cancel and cancel():
            return None
        try:
            r = read_file(p)
        except Exception:  # noqa: BLE001
            r = None
        idx['files'][rel] = [st] + (list(r) if r else [[], []])
        changed = True
        if progress and (k % 20 == 0 or k == len(todo) - 1):
            progress(k + 1, len(todo))
    if changed:
        try:
            save_index(idx)
        except OSError:
            pass
    return idx


def mod_files(mod, root=None):
    """{'English/...dat': (labels, lines)} for message files in the current mod."""
    out = {}
    if mod is None:
        return out
    base = os.path.join(mod.romfs, *MSG_DIR.split('/'))
    if not os.path.isdir(base):
        return out
    game = os.path.join(root, *MSG_DIR.split('/')) if root else None
    for rel, p in _files(base).items():
        try:
            r = read_file(p, os.path.join(game, *rel.split('/')) if game else None)
        except Exception:  # noqa: BLE001
            r = None
        if r:
            out[rel] = r
    return out


def terms(query):
    """Search words: quoted phrases stay together; all folded."""
    out = [fold(a or b) for a, b in re.findall(r'"([^"]+)"|(\S+)', query)]
    return [t for t in out if t]


class Folded:
    """Lower-case, accent-free copies of every line, made once per file and reused between searches."""

    def __init__(self):
        self.cache = {}

    def get(self, key, labels, lines):
        c = self.cache.get(key)
        if c is None or c[0] is not lines:
            low = [fold((labels[i] if i < len(labels) else '') + ' \x00 ' + t) if t else '' for i, t in enumerate(lines)]
            c = self.cache[key] = (lines, low)
        return c[1]


def search(idx, query, langs, mod_over=None, folded=None, limit=LIMIT):
    """[(rel, line index, label, text, from_mod)] - rel is the file's path inside the message folder."""
    ts = terms(query)
    if not ts:
        return []
    folded = folded or Folded()
    files = {r: (v[1], v[2], False) for r, v in idx['files'].items() if r.split('/', 1)[0] in langs}
    for r, (labels, lines) in (mod_over or {}).items():
        if r.split('/', 1)[0] in langs:
            files[r] = (labels, lines, True)
    hits = []
    for rel in sorted(files):
        labels, lines, from_mod = files[rel]
        low = folded.get(('m:' if from_mod else 'g:') + rel, labels, lines)
        for i, s in enumerate(low):
            if s and all(t in s for t in ts):
                hits.append((rel, i, labels[i] if i < len(labels) else '#%d' % i, lines[i], from_mod))
                if len(hits) >= limit:
                    return hits
    return hits


# ---------------------------------------------------------------------------------------------------------
# the window
# ---------------------------------------------------------------------------------------------------------

class SearchWindow:
    def __init__(self, app):
        import tkinter as tk
        from tkinter import ttk
        self.app = app
        self.idx = None
        self.cancelled = False
        self._after = None
        self.folded = Folded()
        self.root = os.path.normpath(app.root_var.get())
        self.langs = languages(self.root)
        w = self.win = tk.Toplevel(app)
        w.title('Search all text')
        w.geometry('1060x640')
        bg = ttk.Style(w).lookup('TFrame', 'background') or None
        if bg:
            w.configure(background=bg)
        top = ttk.Frame(w, padding=(10, 10, 10, 4))
        top.pack(fill='x')
        ttk.Label(top, text='Search all game text:').pack(side='left')
        self.q = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.q)
        e.pack(side='left', fill='x', expand=True, padx=6)
        e.focus_set()
        e.bind('<Return>', lambda ev: self.run())
        self.q.trace_add('write', lambda *a: self._debounce())
        ttk.Label(top, text='in').pack(side='left')
        cur = app.text_lang.get() if hasattr(app, 'text_lang') else 'English'
        self.lang = tk.StringVar(value=cur if cur in self.langs else (self.langs[0] if self.langs else ALL))
        lc = ttk.Combobox(top, textvariable=self.lang, state='readonly', width=14, values=[ALL] + self.langs)
        lc.pack(side='left', padx=6)
        lc.bind('<<ComboboxSelected>>', lambda ev: self.start_index())
        ttk.Button(top, text='Search', command=self.run).pack(side='left')
        ttk.Label(w, style='Dim.TLabel', wraplength=1020, justify='left', padding=(10, 0), text=(
            'Dialogue, item and move descriptions, menus - every message file. Every word must match (in the line or '
            'its label); case and accents don\'t matter; put words in "quotes" to find a phrase. Double-click a result '
            'to edit that line. Text the current mod changed is searched in the mod\'s version.')).pack(fill='x')
        mid = ttk.Frame(w, padding=(10, 6))
        mid.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(mid, columns=('lang', 'file', 'label', 'text'), show='headings', selectmode='browse')
        for c, t, wd in (('lang', 'Language', 90), ('file', 'File', 210), ('label', 'Label', 200),
                         ('text', 'Text', 540)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=wd, stretch=(c == 'text'))
        sb = ttk.Scrollbar(mid, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<Double-1>', lambda ev: self.open())
        self.tree.bind('<Return>', lambda ev: self.open())
        self.tree.bind('<<TreeviewSelect>>', lambda ev: self.preview())
        self.full = tk.Text(w, height=4, wrap='word', relief='flat', padx=10, pady=4)
        self.full.pack(fill='x', padx=10)
        self.full.configure(state='disabled')
        try:
            self.full.configure(background=ttk.Style(w).lookup('TEntry', 'fieldbackground') or 'white',
                                foreground=ttk.Style(w).lookup('TLabel', 'foreground') or 'black')
        except Exception:  # noqa: BLE001
            pass
        bar = ttk.Frame(w, padding=(10, 6, 10, 10))
        bar.pack(fill='x')
        self.status = tk.StringVar(value='Preparing the index…')
        ttk.Label(bar, textvariable=self.status).pack(side='left')
        ttk.Button(bar, text='Close', command=self.close).pack(side='right')
        ttk.Button(bar, text='Edit line', command=self.open).pack(side='right', padx=4)
        w.bind('<Escape>', lambda ev: self.close())
        w.protocol('WM_DELETE_WINDOW', self.close)
        self.hits = {}
        self.busy = False
        self.start_index()

    def close(self):
        self.cancelled = True
        self.win.destroy()

    def chosen(self):
        v = self.lang.get()
        return list(self.langs) if v == ALL else [v]

    def start_index(self):
        langs = self.chosen()
        if self.idx is not None and self.ready_langs >= set(langs):
            self.run()
            return
        if self.busy:
            self.want = True
            return
        self.busy, self.want = True, False

        def progress(done, total):
            self.app.q.put(lambda: self.status.set(
                'Reading the game text (first time only): %d / %d files…' % (done, total)))

        def work():
            try:
                idx = build(self.root, langs, progress, lambda: self.cancelled, self.idx)
                if idx is not None:
                    self.app.q.put(lambda: self.status.set('Preparing the text for searching…'))
                    for r, v in idx['files'].items():
                        if r.split('/', 1)[0] in langs:
                            self.folded.get('g:' + r, v[1], v[2])
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                self.app.q.put(lambda: self.status.set('Could not read the text: ' + msg))
                self.busy = False
                return
            if idx is not None:
                self.app.q.put(lambda: self.ready(idx, langs))
        threading.Thread(target=work, daemon=True).start()

    def ready(self, idx, langs):
        self.busy = False
        if self.cancelled:
            return
        self.idx = idx
        self.ready_langs = getattr(self, 'ready_langs', set()) | set(langs)
        n = sum(len(v[2]) for r, v in idx['files'].items() if r.split('/', 1)[0] in self.chosen())
        self.status.set('%s lines of text ready to search.' % format(n, ','))
        if self.want:
            return self.start_index()
        if self.q.get().strip():
            self.run()

    def _debounce(self):
        if self._after:
            self.win.after_cancel(self._after)
        self._after = self.win.after(350, self.run)

    def run(self):
        self._after = None
        if self.idx is None or self.busy:
            return
        q = self.q.get().strip()
        self.tree.delete(*self.tree.get_children())
        self.hits = {}
        if not q:
            return
        mod = self.app.current_mod(ask=False)
        idx, langs = self.idx, self.chosen()
        self.status.set('Searching…')

        def work():
            try:
                hits = search(idx, q, langs, mod_files(mod, self.root), self.folded)
            except Exception as e:  # noqa: BLE001
                msg = str(e) or type(e).__name__
                self.app.q.put(lambda: self.status.set('Search failed: ' + msg))
                return
            self.app.q.put(lambda: self.show(q, hits, langs))
        threading.Thread(target=work, daemon=True).start()

    def show(self, q, hits, langs):
        if self.cancelled or q != self.q.get().strip() or langs != self.chosen():
            return                          # an older search: the query or language changed meanwhile
        self.tree.delete(*self.tree.get_children())
        self.hits = {}
        files = set()
        for k, (rel, i, label, text, from_mod) in enumerate(hits):
            iid = str(k)
            self.hits[iid] = (rel, i, text, from_mod)
            files.add(rel)
            lang, f = rel.split('/', 1)
            one = text.replace('\\n', ' ').replace('\\r', ' ').replace('\\c', ' ')
            self.tree.insert('', 'end', iid, values=(lang, f[:-4] + ('  [mod]' if from_mod else ''), label,
                                                     one if len(one) < 300 else one[:297] + '…'))
        more = ' (first %d shown)' % len(hits) if len(hits) >= LIMIT else ''
        self.status.set('%d line(s) in %d file(s)%s.' % (len(hits), len(files), more) if hits else 'No matches.')

    def preview(self):
        sel = self.tree.selection()
        if not sel or sel[0] not in self.hits:
            return
        text = self.hits[sel[0]][2].replace('\\n', '\n').replace('\\r', '\n').replace('\\c', '\n')
        self.full.configure(state='normal')
        self.full.delete('1.0', 'end')
        self.full.insert('1.0', text)
        self.full.configure(state='disabled')

    def open(self):
        sel = self.tree.selection()
        if not sel or sel[0] not in self.hits:
            return
        rel, i, _, from_mod = self.hits[sel[0]]
        path = os.path.join(self.root, *MSG_DIR.split('/'), *rel.split('/'))
        if not os.path.isfile(path):
            mod = self.app.current_mod(ask=False)
            if mod is None:
                return
            path = os.path.join(mod.romfs, *MSG_DIR.split('/'), *rel.split('/'))
        self.app.edit_text(path, i)


def open_window(app):
    w = getattr(app, 'text_search_win', None)
    try:
        if w is not None and w.win.winfo_exists() and w.root != os.path.normpath(app.root_var.get()):
            w.close()                       # the game folder changed: start over
        elif w is not None and w.win.winfo_exists():
            w.win.deiconify()
            w.win.lift()
            return w
    except Exception:  # noqa: BLE001
        pass
    app.text_search_win = SearchWindow(app)
    return app.text_search_win
