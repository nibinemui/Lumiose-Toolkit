"""Mod conflict check: finds game files that more than one mod changes.

Looks at your own mods (mods/), external mods in the Mods tab's load order and the mods installed in a
ZA Recomp / Lumiose Online folder (<folder>/mods/<name>/romfs, order and on/off from larecomp.ini).
Files with the same content in every mod are harmless; files with different content are real conflicts:
only one mod's version is used (in Recomp, the mod higher on its Mods page).
"""
import hashlib
import json
import os
import re
import threading
import tkinter as tk
from tkinter import ttk

import za_modding as zm

KINDS = {
    '.bntx': 'Texture', '.trmdl': 'Model', '.trmsh': 'Model mesh', '.trmbf': 'Model mesh data',
    '.trmtr': 'Material', '.trmmt': 'Colour / pattern set', '.trskl': 'Skeleton', '.tranm': 'Animation',
    '.tracn': 'Animation', '.dat': 'Text', '.tbl': 'Text labels', '.bin': 'Data table', '.bfbs': 'Data table',
    '.arc': 'UI archive', '.bnk': 'Sound bank', '.wem': 'Sound', '.pck': 'Sound package', '.trscn': 'Scene',
    '.trpfd': 'File index',
}

HINTS = {
    'Text': 'Both mods replace the whole text file - put the text edits into one mod.',
    'Data table': 'Both mods replace the whole table - redo one mod\'s edits in the other with the table editor.',
    'Colour / pattern set': 'Both mods change this item\'s colours - make the colour edits in one mod.',
    'Texture': 'Only one texture is used - pick which one, or combine the images in one mod.',
}


def kind_of(rel):
    return KINDS.get(os.path.splitext(rel)[1].lower(), 'File')


class Source:
    """One mod as seen by the checker."""

    def __init__(self, label, kind, files, reader, covers=(), enabled=True, rank=None):
        self.label = label                # shown name
        self.kind = kind                  # 'mine' | 'ext' | 'recomp'
        self.files = files                # rel game paths
        self.reader = reader              # rel -> bytes
        self.covers = set(covers)         # names of own mods this Recomp mod is an installed copy of
        self.enabled = enabled            # Recomp: switched on
        self.rank = rank                  # Recomp: position on its Mods page (0 = top, wins)
        self._hash = {}

    def digest(self, rel):
        if rel not in self._hash:
            try:
                self._hash[rel] = hashlib.sha1(self.reader(rel)).hexdigest()
            except (OSError, KeyError):
                self._hash[rel] = None
        return self._hash[rel]


def my_mods():
    return [Source(m.name, 'mine', m.files(), lambda rel, m=m: open(m.path_for(rel), 'rb').read())
            for m in zm.list_mods()]


def external(path):
    e = zm.ExternalMod(path)
    return Source(e.name + ' (external)', 'ext', e.files(), e.read)


def recomp_settings(recomp_dir):
    """(order list, set of switched-off names) from larecomp.ini."""
    order, off = [], set()
    try:
        with open(os.path.join(recomp_dir, 'larecomp.ini'), encoding='utf-8', errors='replace') as f:
            for line in f:
                m = re.match(r'\s*(mods_order|mods_off)\s*=\s*(.*)', line)
                if m:
                    names = [n for n in m.group(2).strip().split('|') if n]
                    if m.group(1) == 'mods_order':
                        order = names
                    else:
                        off = set(names)
    except OSError:
        pass
    return order, off


def recomp_mods(recomp_dir, own_names=(), skip=()):
    """Mods installed in ZA Recomp / Lumiose Online. skip: folder names to leave out."""
    root = os.path.join(recomp_dir, 'mods')
    if not os.path.isdir(root):
        return []
    order, off = recomp_settings(recomp_dir)
    out = []
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if name in skip or name.lower() in ('romfs', 'exefs') or not (os.path.isdir(p) or name.lower().endswith('.zip')):
            continue
        try:
            e = zm.ExternalMod(p)
        except zm.ModError:
            continue
        if not e.files():
            continue
        covers = set()
        info = os.path.join(p, 'za_mod_info.json')         # installed by Lumiose Toolkit
        if os.path.isfile(info):
            try:
                with open(info, encoding='utf-8') as f:
                    covers.update(json.load(f).get('mods', []))
            except (OSError, ValueError):
                pass
        if name in own_names:                              # a package of one of your mods
            covers.add(name)
        label = os.path.splitext(name)[0] if name.lower().endswith('.zip') else name
        out.append(Source(label + ' (in Recomp)', 'recomp', e.files(), e.read, covers,
                          enabled=label not in off, rank=order.index(label) if label in order else None))
    return out


def find_conflicts(sources, include_off=False):
    """[{'rel', 'kind', 'entries': [Source], 'same': bool, 'groups': {label: group letter}, 'winner'}]."""
    by_rel = {}
    for s in sources:
        if s.kind == 'recomp' and not s.enabled and not include_off:
            continue
        for rel in s.files:
            if rel.lower() == 'arc/data.trpfd':          # file index: Recomp builds its own
                continue
            by_rel.setdefault(rel, []).append(s)
    out = []
    for rel, ents in by_rel.items():
        if len(ents) < 2:
            continue
        names = {e.label for e in ents}
        # an installed copy of your own mod doesn't conflict with that mod (but tells where it sits in Recomp)
        copies = {c: e for e in ents if e.kind == 'recomp' for c in e.covers if c in names}
        ents = [e for e in ents if not (e.kind == 'recomp' and any(c in names for c in e.covers))]
        if len(ents) < 2:
            continue
        letters, groups = {}, {}
        for e in ents:
            h = e.digest(rel)
            groups[e.label] = letters.setdefault(h, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'[len(letters) % 26])
        out.append({'rel': rel, 'kind': kind_of(rel), 'entries': ents, 'same': len(letters) == 1,
                    'groups': groups, 'winner': winner(ents, copies), 'copies': copies})
    out.sort(key=lambda c: (c['same'], c['rel']))
    return out


def winner(ents, copies=None):
    """Which mod's file ZA Recomp uses (the highest switched-on mod on its Mods page), or None when none
    of them is installed. Your own mods count with their installed copy's place."""
    copies = copies or {}
    placed = []
    for e in ents:
        r = e if e.kind == 'recomp' else copies.get(e.label)
        if r is not None and r.enabled:
            placed.append((r.rank if r.rank is not None else 10 ** 6,
                           e.label if e.kind == 'recomp' else e.label + ' (installed)'))
    if not placed:
        return None
    placed.sort()
    return placed[0][1]


# ---------------------------------------------------------------------------------------------------------
# window
# ---------------------------------------------------------------------------------------------------------

class ConflictWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.results = []
        self.title('Mod conflicts')
        self.geometry('1060x620')
        self.minsize(720, 400)
        P = ttk.Frame(self, padding=8)
        P.pack(fill='both', expand=True)
        ttk.Label(P, text='Check these mods against each other:').pack(anchor='w')
        opts = ttk.Frame(P)
        opts.pack(fill='x', pady=(2, 6))
        self.v_mine = tk.BooleanVar(value=True)
        self.v_ext = tk.BooleanVar(value=True)
        rd = app.recomp_var.get().strip()
        self.recomp_dir = rd if rd and os.path.isdir(os.path.join(rd, 'mods')) else ''
        self.v_recomp = tk.BooleanVar(value=bool(self.recomp_dir))
        self.v_off = tk.BooleanVar(value=False)
        self.v_same = tk.BooleanVar(value=False)
        n_ext = len([e for e in app.order if e[0] == 'ext'])
        ttk.Checkbutton(opts, text='All my mods (%d)' % len(zm.list_mods()), variable=self.v_mine).pack(side='left')
        ttk.Checkbutton(opts, text='External mods in the load order (%d)' % n_ext, variable=self.v_ext
                        ).pack(side='left', padx=12)
        cb = ttk.Checkbutton(opts, text='Mods installed in ZA Recomp / Lumiose Online', variable=self.v_recomp)
        cb.pack(side='left')
        if not self.recomp_dir:
            cb.state(['disabled'])
        ttk.Checkbutton(opts, text='incl. switched off', variable=self.v_off).pack(side='left', padx=(4, 0))
        ttk.Button(opts, text='Check', command=self.run).pack(side='right')
        self.summary = tk.StringVar(value='')
        ttk.Label(P, textvariable=self.summary, style='Head.TLabel').pack(anchor='w')
        tf = ttk.Frame(P)
        tf.pack(fill='both', expand=True, pady=4)
        self.tree = ttk.Treeview(tf, columns=('kind', 'status', 'used'), show='tree headings')
        self.tree.heading('#0', text='Game file / mod')
        self.tree.heading('kind', text='Type')
        self.tree.heading('status', text='Status')
        self.tree.heading('used', text='In game')
        self.tree.column('#0', width=430)
        self.tree.column('kind', width=110, stretch=False)
        self.tree.column('status', width=270, stretch=False)
        self.tree.column('used', width=200)
        sb = ttk.Scrollbar(tf, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.show_hint())
        self.tree.tag_configure('diff', foreground=app.theme_colours().get('accent', '#0063b1'))
        bot = ttk.Frame(P)
        bot.pack(fill='x')
        ttk.Checkbutton(bot, text='Show identical copies too', variable=self.v_same, command=self.fill).pack(side='left')
        ttk.Button(bot, text='Close', command=self.destroy).pack(side='right')
        ttk.Button(bot, text='Copy list', command=self.copy).pack(side='right', padx=4)
        self.hint = tk.StringVar(value=(
            'A conflict means two mods replace the same game file: the game uses only one of them. Identical copies '
            'are harmless. In ZA Recomp / Lumiose Online the mod higher on the Mods page wins; in a package built '
            'here, the mod higher in the load order wins.'))
        ttk.Label(P, textvariable=self.hint, style='Dim.TLabel', wraplength=1020, justify='left').pack(anchor='w', pady=(6, 0))
        self.run()

    def run(self):
        self.summary.set('Checking…')
        self.tree.delete(*self.tree.get_children())
        mine, ext, rec, off = self.v_mine.get(), self.v_ext.get(), self.v_recomp.get(), self.v_off.get()
        order = list(self.app.order)
        rd = self.recomp_dir

        def work():
            try:
                srcs = my_mods() if mine else []
                if ext:
                    for kind, ref in order:
                        if kind == 'ext':
                            try:
                                srcs.append(external(ref))
                            except zm.ModError:
                                pass
                if rec and rd:
                    srcs += recomp_mods(rd, own_names={m.name for m in zm.list_mods()})
                res = find_conflicts(srcs, include_off=off)
                self.app.q.put(lambda: self.winfo_exists() and self.done(res, len(srcs)))
            except Exception as e:  # noqa: BLE001
                msg = str(e) or type(e).__name__
                self.app.q.put(lambda: self.winfo_exists() and self.summary.set('Check failed: %s' % msg))
        threading.Thread(target=work, daemon=True).start()

    def done(self, res, n_src):
        self.results = res
        self.n_src = n_src
        self.fill()

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        res = self.results
        root = self.app.root_var.get()
        try:
            import za_merge
            for c in res:
                c['mergeable'] = (not c['same'] and bool(root) and
                                  (za_merge.is_text(c['rel']) or za_merge.is_table(c['rel'], root)))
        except Exception:  # noqa: BLE001
            pass
        diff = [c for c in res if not c['same'] and not c.get('mergeable')]
        mergeable = [c for c in res if c.get('mergeable')]
        same = [c for c in res if c['same']]
        self.summary.set('%d mod(s) checked: %s, %d mergeable, %d identical cop%s.' % (
            self.n_src, ('%d real conflict%s' % (len(diff), '' if len(diff) == 1 else 's')) if diff else 'no real conflicts',
            len(mergeable), len(same), 'y' if len(same) == 1 else 'ies'))
        for i, c in enumerate(res):
            if c['same'] and not self.v_same.get():
                continue
            iid = self.tree.insert('', 'end', 'c%d' % i, text=c['rel'], open=not c['same'],
                                   values=(c['kind'], 'Identical copies' if c['same'] else
                                           ('Different - mergeable' if c.get('mergeable') else 'Different (conflict)'),
                                           c['winner'] or ''),
                                   tags=() if c['same'] else ('diff',))
            for e in c['entries']:
                st = 'version %s' % c['groups'][e.label]
                r = e if e.kind == 'recomp' else c['copies'].get(e.label)
                if r is not None:
                    st += ('' if r.enabled else ', switched off') + ('' if e.kind == 'recomp' else ', installed')
                    if r.rank is not None:
                        st += ', #%d on Mods page' % (r.rank + 1)
                elif e.kind == 'mine':
                    st += ', not installed'
                self.tree.insert(iid, 'end', text='   ' + e.label, values=('', st, ''))

    def show_hint(self):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0] if sel[0].startswith('c') else self.tree.parent(sel[0])
        c = self.results[int(iid[1:])]
        if c['same']:
            self.hint.set('Every mod has the same %s here, so it doesn\'t matter which one wins.' % c['kind'].lower())
        elif c.get('mergeable'):
            self.hint.set('Game data/text: put these mods in the load order and use "Install to Recomp" or "Build '
                          'package(s)" once for all of them - their rows are merged into one file. Installed in ZA '
                          'Recomp as separate mods, only one version is used.')
        else:
            self.hint.set('%s  Mods with the same letter have the same file.' % HINTS.get(
                c['kind'], 'Only one mod\'s version of this file is used.'))

    def copy(self):
        lines = []
        for c in self.results:
            if c['same'] and not self.v_same.get():
                continue
            lines.append('%s  [%s, %s]' % (c['rel'], c['kind'], 'identical' if c['same'] else 'conflict'))
            for e in c['entries']:
                lines.append('    %s (version %s)' % (e.label, c['groups'][e.label]))
        self.clipboard_clear()
        self.clipboard_append('\n'.join(lines) or 'No conflicts.')
        self.app.status_var.set('Conflict list copied to the clipboard.')


def install_check(mods, recomp_dir, package_folder):
    """Before installing `mods` into Recomp as `package_folder`: real conflicts with the other installed mods.
    Returns [(rel, [other mod labels])]."""
    own = {m.name for m in zm.list_mods()}
    srcs = []
    for m in mods:
        if isinstance(m, zm.Mod):
            srcs.append(Source(m.name, 'mine', m.files(), lambda rel, m=m: open(m.path_for(rel), 'rb').read()))
        else:
            srcs.append(Source(m.name, 'ext', m.files(), m.read))
    mine = {s.label for s in srcs}
    others = recomp_mods(recomp_dir, own_names=own, skip={package_folder})
    out = []
    for c in find_conflicts(srcs + others):
        if c['same']:
            continue
        labels = [e.label for e in c['entries']]
        if any(lb in mine for lb in labels) and any(e.kind == 'recomp' for e in c['entries']):
            # only report it if an installed mod's version differs from the package's
            mine_groups = {c['groups'][lb] for lb in labels if lb in mine}
            rivals = [e.label for e in c['entries'] if e.kind == 'recomp' and c['groups'][e.label] not in mine_groups]
            if rivals:
                out.append((c['rel'], rivals))
    return out
