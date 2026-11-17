"""Search game data: every row of every data table the toolkit can read, in one search.

The index (data_index.json.gz next to the program) holds, per table, one entry per row - or per item of a row's
list, for tables that keep their rows in groups - with a label and a search text: the row's strings and whole
numbers, plus Pokémon / move / item names for fields that hold such numbers. It is built once in the background
(about a minute) and after that only tables whose file changed are read again. The current mod's copy of a
table is searched instead of the game's.

Search: every word must match. A number matches a whole number in the row (25 finds 25, not 250); a word
matches anywhere in the row's text, without accents ("flabebe" finds Flabébé)."""
import concurrent.futures
import gzip
import json
import os
import re
import threading
import unicodedata

import za_modding as zm

INDEX_PATH = os.path.join(zm.APP_DIR, 'data_index.json.gz')
VERSION = 2
TEXT_MAX = 3000
EXTS = ('.bin', '.trpmcatalog')
LABEL_KEYS = ('id', 'Id', 'ID', 'name', 'Name', 'zoneId', 'zoneID', 'label', 'Label', 'objectName', 'actionId',
              'ActionID', 'devNo', 'devId', 'DevNo', 'MoveID', 'monsno', 'species')
SPECIES_RE = re.compile(r'(dev_?(no|id)$|^devno$|species$|monsno|^pokemon(no|id)?$|^poke(no|id)$)', re.I)
MOVE_RE = re.compile(r'(waza\d*$|^moveid$|^move\d*$|^waza(no|id)$)', re.I)
ITEM_RE = re.compile(r'(^itemid$|^item(no)?$|^item\d$|itemid$)', re.I)


def fold(s):
    """Lower case without accents."""
    s = unicodedata.normalize('NFKD', str(s))
    return ''.join(c for c in s if not unicodedata.combining(c)).lower()


# ---------------------------------------------------------------------------------------------------------
# which tables
# ---------------------------------------------------------------------------------------------------------

def presets():
    import za_dataedit
    return {p['rel']: p for p in za_dataedit.PRESETS}


def candidates(root):
    """[(rel, path, schema)] - every data file with a known schema, plus the Game data tab's tables."""
    out, seen = [], set()
    pre = presets()
    for rel, p in pre.items():
        path = os.path.join(root, *rel.split('/'))
        if os.path.isfile(path):
            out.append((rel, path, p.get('schema') or zm.schema_for(path)))
            seen.add(rel)
    for dp, dn, fn in os.walk(root):
        for f in fn:
            if not f.endswith(EXTS):
                continue
            path = os.path.join(dp, f)
            rel = os.path.relpath(path, root).replace(os.sep, '/')
            if rel in seen:
                continue
            sch = zm.schema_for(path)
            if sch:
                out.append((rel, path, sch))
                seen.add(rel)
    return out


def table_name(rel, pre=None):
    pre = presets() if pre is None else pre
    if rel in pre:
        return pre[rel]['name']
    return os.path.splitext(os.path.basename(rel))[0]


def _stamp(path):
    try:
        st = os.stat(path)
        return [st.st_size, int(st.st_mtime)]
    except OSError:
        return [0, 0]


# ---------------------------------------------------------------------------------------------------------
# reading one table
# ---------------------------------------------------------------------------------------------------------

class _Names:
    def __init__(self, root):
        import za_dataedit
        n = za_dataedit.Names(root)
        self.full = n
        self.species, self.moves, self.items = n.species, n.moves, n.items


def _texts(node, names, out, depth=0):
    if depth > 12:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, int):
                out.append(str(v))
                if SPECIES_RE.search(k) and v in names.species:
                    out.append(names.species[v])
                elif MOVE_RE.search(k) and v in names.moves and v:
                    out.append(names.moves[v])
                elif ITEM_RE.search(k) and v in names.items and v:
                    out.append(names.items[v])
            elif isinstance(v, str):
                out.append(v)
                if k and SPECIES_RE.search(k) and v.startswith('DEV_'):
                    out.append(v[4:].replace('_', ' '))
            elif isinstance(v, (dict, list)):
                _texts(v, names, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            if isinstance(v, (dict, list)):
                _texts(v, names, out, depth + 1)
            elif isinstance(v, str):
                out.append(v)
            elif isinstance(v, int) and not isinstance(v, bool):
                out.append(str(v))


def _text(row, names):
    out = []
    _texts(row, names, out)
    seen, keep, size = set(), [], 0
    for t in out:
        t = fold(t).strip()
        if not t or t in seen:
            continue
        seen.add(t)
        keep.append(t)
        size += len(t) + 1
        if size > TEXT_MAX:
            break
    return ' '.join(keep)


def _label(row, names, preset=None):
    if preset and preset.get('label'):
        try:
            return str(preset['label'](row, names.full))
        except Exception:  # noqa: BLE001
            pass
    if not isinstance(row, dict):
        return ''
    for k in LABEL_KEYS:
        v = row.get(k)
        if v not in (None, '') and not isinstance(v, (dict, list)):
            if SPECIES_RE.search(k) and isinstance(v, int) and v in names.species:
                return '%s %s' % (v, names.species[v])
            return str(v)
    for v in row.values():
        if isinstance(v, str) and v:
            return v
    for v in row.values():                       # a group: label from its first item
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return _label(v[0], names)
    return ''


def _inner_list(row):
    """A group row ({'root': [item, item, ...]}): its list of items, else None."""
    if not isinstance(row, dict):
        return None
    lists = [v for v in row.values() if isinstance(v, list) and len(v) > 1 and all(isinstance(x, dict) for x in v)]
    if len(lists) == 1 and len(row) <= 2:
        return lists[0]
    return None


def index_table(path, schema, names, preset=None):
    """[[row, item, label, text], ...] for one table; item -1 = the whole row."""
    import za_dataedit
    data = json.loads(zm.to_json(path, schema))
    sch = za_dataedit.load_schema(schema)
    rows_path = (preset or {}).get('rows') or sch.find_rows(data)
    try:
        rows = sch.resolve(data, rows_path)[0] if rows_path else [data]
    except (StopIteration, IndexError, KeyError, TypeError):
        rows = [data]
    out = []
    for i, r in enumerate(rows):
        inner = None if preset else _inner_list(r)
        if inner:
            for j, x in enumerate(inner):
                out.append([i, j, _label(x, names), _text(x, names)])
        else:
            out.append([i, -1, _label(r, names, preset), _text(r, names)])
    return out


# ---------------------------------------------------------------------------------------------------------
# the index
# ---------------------------------------------------------------------------------------------------------

_LOCK = threading.Lock()
_CACHE = {'root': None, 'index': None}
_MOD = {}


def load_index():
    try:
        with gzip.open(INDEX_PATH, 'rt', encoding='utf-8') as f:
            idx = json.load(f)
        return idx if idx.get('version') == VERSION else None
    except (OSError, ValueError, EOFError):
        return None


def save_index(idx):
    tmp = INDEX_PATH + '.tmp'
    with gzip.open(tmp, 'wt', encoding='utf-8', compresslevel=5) as f:
        json.dump(idx, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, INDEX_PATH)


def build(root, progress=None, cancel=None):
    """The index for the game folder: read again only the tables that changed. progress(done, total, rel)."""
    root = os.path.normpath(root)
    with _LOCK:
        if _CACHE['root'] == root and _CACHE['index'] is not None and not _changed(root, _CACHE['index']):
            return _CACHE['index']
    old = load_index() or {}
    if old.get('root') != root:
        old = {}
    tables = dict(old.get('tables', {}))
    cands = candidates(root)
    pre = presets()
    names = _Names(root)
    todo = [(rel, path, sch) for rel, path, sch in cands
            if rel not in tables or tables[rel].get('stamp') != _stamp(path)]
    keep = {rel for rel, _, _ in cands}
    tables = {k: v for k, v in tables.items() if k in keep}
    done = [0]

    def one(item):
        rel, path, sch = item
        if cancel and cancel():
            return rel, None
        try:
            rows = index_table(path, sch, names, pre.get(rel))
        except Exception:  # noqa: BLE001 - a table that doesn't decode is just not searchable
            rows = []
        return rel, {'stamp': _stamp(path), 'name': table_name(rel, pre), 'rows': rows}

    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 4))) as ex:
            for rel, t in ex.map(one, todo):
                done[0] += 1
                if t is not None:
                    tables[rel] = t
                if progress:
                    progress(done[0], len(todo), rel)
        if cancel and cancel():
            return None
    idx = {'version': VERSION, 'root': root, 'tables': tables}
    if todo:
        save_index(idx)
    with _LOCK:
        _CACHE['root'], _CACHE['index'] = root, idx
    return idx


def _changed(root, idx):
    """Quick check: any indexed table whose file changed (the full scan for new files runs on rebuild)."""
    for rel, t in idx['tables'].items():
        if t.get('stamp') != _stamp(os.path.join(root, *rel.split('/'))):
            return True
    return False


def mod_tables(root, mod, idx):
    """{rel: table} for the tables the mod has its own copy of (indexed from the mod's file, cached)."""
    if mod is None:
        return {}
    out = {}
    names = None
    pre = presets()
    for rel in mod.files():
        if rel not in idx['tables']:
            continue
        p = mod.path_for(rel)
        key = (os.path.normcase(p), tuple(_stamp(p)))
        if key not in _MOD:
            if names is None:
                names = _Names(root)
            try:
                sch = (pre.get(rel) or {}).get('schema') or zm.schema_for(os.path.join(root, *rel.split('/')))
                _MOD[key] = {'name': table_name(rel, pre), 'rows': index_table(p, sch, names, pre.get(rel)),
                             'mod': mod.name}
            except Exception:  # noqa: BLE001
                _MOD[key] = None
        if _MOD[key]:
            out[rel] = _MOD[key]
    return out


def search(idx, query, mod_over=None, limit=1000):
    """[(rel, table name, row, item, label, from_mod)] - every word of the query matches."""
    terms = [fold(t) for t in query.split() if t.strip()]
    if not terms:
        return []
    nums = [t for t in terms if t.lstrip('-').isdigit()]
    words = [t for t in terms if t not in nums]
    tables = dict(idx['tables'])
    tables.update(mod_over or {})
    hits = []
    for rel, t in tables.items():
        tname = fold(t['name'] + ' ' + rel)
        for row, item, label, text in t['rows']:
            padded = ' ' + text + ' '
            if any((' ' + n + ' ') not in padded for n in nums):
                continue
            hay = text + ' ' + fold(label)
            if any(w not in hay and w not in tname for w in words):
                continue
            score = sum(1 for w in words if w in fold(label)) * 2 + sum(1 for n in nums if fold(label).startswith(n))
            hits.append((-score, t['name'], row, item, rel, label, bool(t.get('mod'))))
    hits.sort(key=lambda h: (h[0], h[1].lower(), h[2], h[3]))
    return [(h[4], h[1], h[2], h[3], h[5], h[6]) for h in hits[:limit]]


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
        w = self.win = tk.Toplevel(app)
        w.title('Search game data')
        w.geometry('980x620')
        bg = ttk.Style(w).lookup('TFrame', 'background') or None
        if bg:
            w.configure(background=bg)
        top = ttk.Frame(w, padding=(10, 10, 10, 4))
        top.pack(fill='x')
        ttk.Label(top, text='Search every data table:').pack(side='left')
        self.q = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.q)
        e.pack(side='left', fill='x', expand=True, padx=6)
        e.focus_set()
        e.bind('<Return>', lambda ev: self.run())
        self.q.trace_add('write', lambda *a: self._debounce())
        ttk.Button(top, text='Search', command=self.run).pack(side='left')
        ttk.Label(w, style='Dim.TLabel', wraplength=940, justify='left', padding=(10, 0), text=(
            'Names or numbers, e.g. "Pikachu", "Tackle", "Poke Ball", "a0403_w01" or "25 alpha". Every word must '
            'match; a number matches whole numbers only. Double-click a result to open that row in the Game data tab. '
            'Tables in the current mod are searched in the mod\'s version.')).pack(fill='x')
        mid = ttk.Frame(w, padding=(10, 6))
        mid.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(mid, columns=('table', 'row', 'match'), show='headings', selectmode='browse')
        for c, t, wd in (('table', 'Table', 230), ('row', 'Row', 90), ('match', 'Matching row', 600)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=wd, stretch=(c == 'match'))
        sb = ttk.Scrollbar(mid, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<Double-1>', lambda ev: self.open())
        self.tree.bind('<Return>', lambda ev: self.open())
        bar = ttk.Frame(w, padding=(10, 0, 10, 10))
        bar.pack(fill='x')
        self.status = tk.StringVar(value='Preparing the index…')
        ttk.Label(bar, textvariable=self.status).pack(side='left')
        ttk.Button(bar, text='Close', command=self.close).pack(side='right')
        ttk.Button(bar, text='Open row', command=self.open).pack(side='right', padx=4)
        w.bind('<Escape>', lambda ev: self.close())
        w.protocol('WM_DELETE_WINDOW', self.close)
        self.hits = {}
        self.start_index()

    def close(self):
        self.cancelled = True
        self.win.destroy()

    def start_index(self):
        root = self.app.root_var.get()

        def progress(done, total, rel):
            if done == 1 or done % 25 == 0 or done == total:
                self.app.q.put(lambda: self.status.set(
                    'Building the search index (first time only): %d / %d tables…' % (done, total)))

        def work():
            try:
                idx = build(root, progress, lambda: self.cancelled)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                self.app.q.put(lambda: self.status.set('Could not build the index: ' + msg))
                return
            if idx is not None:
                self.app.q.put(lambda: self.ready(idx))
        threading.Thread(target=work, daemon=True).start()

    def ready(self, idx):
        if self.cancelled:
            return
        self.idx = idx
        n = sum(len(t['rows']) for t in idx['tables'].values())
        self.status.set('%d tables, %d rows indexed.' % (len(idx['tables']), n))
        if self.q.get().strip():
            self.run()

    def _debounce(self):
        if self._after:
            self.win.after_cancel(self._after)
        self._after = self.win.after(350, self.run)

    def run(self):
        self._after = None
        if self.idx is None:
            return
        q = self.q.get().strip()
        self.tree.delete(*self.tree.get_children())
        self.hits = {}
        if not q:
            return
        root = self.app.root_var.get()
        mod = self.app.current_mod(ask=False)
        idx = self.idx

        def work():
            over = mod_tables(root, mod, idx)
            hits = search(idx, q, over)
            self.app.q.put(lambda: self.show(q, hits, mod))
        threading.Thread(target=work, daemon=True).start()

    def show(self, q, hits, mod):
        if self.cancelled or q != self.q.get().strip():
            return
        self.tree.delete(*self.tree.get_children())
        self.hits = {}
        tables = set()
        for k, (rel, tname, row, item, label, from_mod) in enumerate(hits):
            iid = str(k)
            self.hits[iid] = (rel, row, item)
            tables.add(rel)
            where = '%d' % row if item < 0 else '%d · item %d' % (row, item + 1)
            self.tree.insert('', 'end', iid, values=(tname + ('  [mod]' if from_mod else ''), where, label))
        more = ' (first %d shown)' % len(hits) if len(hits) >= 1000 else ''
        self.status.set('%d match(es) in %d table(s)%s.' % (len(hits), len(tables), more) if hits
                        else 'No matches.')

    def open(self):
        sel = self.tree.selection()
        if not sel or sel[0] not in self.hits:
            return
        rel, row, item = self.hits[sel[0]]
        self.app.open_data_row(rel, row, item)


def open_window(app):
    w = getattr(app, 'data_search_win', None)
    try:
        if w is not None and w.win.winfo_exists():
            w.win.deiconify()
            w.win.lift()
            return w
    except Exception:  # noqa: BLE001
        pass
    app.data_search_win = SearchWindow(app)
    return app.data_search_win
