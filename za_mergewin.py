"""Merge mods: several of your mods combined into one new mod (Mods tab > Merge mods...).

The same merging as building a combined package: files only one mod has are copied; data tables and text that
several mods change are merged row by row, so every mod's new rows and changes are kept and only a field two
mods change differently goes to the higher one; for other files (models, textures, sounds) the higher mod's
copy is used. The mods themselves are not changed."""
import os
import threading

import za_modding as zm


def merge_into_new(mods, name, game_root, log=None):
    """mods: highest priority first. Makes mod `name` with the merged files. Returns (mod, report lines)."""
    say = log or (lambda s: None)
    if len(mods) < 2:
        raise zm.ModError('Pick at least two mods to merge.')
    winners, conflicts = zm.merge_mods(mods, game_root, say)
    if not winners:
        raise zm.ModError('The picked mods contain no files.')
    new = zm.create_mod(name, author=', '.join(dict.fromkeys(m.info.get('author', '') for m in mods if m.info.get('author'))),
                        description='Merged from (top wins): %s.' % ', '.join(m.name for m in mods))
    try:
        for rel, (src, reader) in sorted(winners.items()):
            new.add_bytes(rel, reader(rel))
    except BaseException:
        import shutil
        shutil.rmtree(new.folder, ignore_errors=True)       # no half-made mod left behind
        raise
    try:
        new.snapshot('merged from %s' % ', '.join(m.name for m in mods))
    except Exception:  # noqa: BLE001
        pass
    merged = sorted(rel for rel, (src, _) in winners.items() if src.startswith('merged: '))
    lines = ['"%s": %d file(s) from %s' % (new.name, len(winners), ', '.join('"%s"' % m.name for m in mods))]
    if merged:
        lines.append('%d table/text file(s) merged row by row: %s%s' % (
            len(merged), ', '.join(os.path.basename(r) for r in merged[:6]), ' …' if len(merged) > 6 else ''))
    if conflicts:
        lines.append('%d file(s) changed differently by several mods - the higher mod\'s version is used:' % len(conflicts))
        lines += ['   %s  (%s)' % (rel, ' > '.join(names)) for rel, names in sorted(conflicts.items())[:20]]
        if len(conflicts) > 20:
            lines.append('   … and %d more' % (len(conflicts) - 20))
    else:
        lines.append('no conflicts')
    return new, lines


def open_window(app):
    import tkinter as tk
    from tkinter import messagebox, ttk
    w = tk.Toplevel(app)
    w.title('Merge mods')
    w.geometry('560x600')
    bg = ttk.Style(w).lookup('TFrame', 'background') or None
    if bg:
        w.configure(background=bg)
    f = ttk.Frame(w, padding=12)
    f.pack(fill='both', expand=True)
    ttk.Label(f, wraplength=530, justify='left', text=(
        'Tick the mods to combine into one new mod. Data tables and text they all change are merged row by row; '
        'when two mods change the same thing differently, or the same model, texture or sound, the higher one in '
        'the list wins - use Up / Down to set the order. The mods themselves stay as they are.')).pack(fill='x')
    mid = ttk.Frame(f)
    mid.pack(fill='both', expand=True, pady=8)
    tree = ttk.Treeview(mid, columns=('files',), show='tree headings', selectmode='browse', height=14)
    tree.heading('#0', text='Mod (top wins)')
    tree.heading('files', text='Files')
    tree.column('#0', width=300, stretch=True)
    tree.column('files', width=60, anchor='e', stretch=False)
    sb = ttk.Scrollbar(mid, orient='vertical', command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    side = ttk.Frame(mid)
    side.pack(side='right', fill='y', padx=(6, 0))
    sb.pack(side='right', fill='y')
    tree.pack(side='left', fill='both', expand=True)
    mods = {m.folder: m for m in zm.list_mods()}
    ticked = set()

    def label(folder):
        return ('☑  ' if folder in ticked else '☐  ') + mods[folder].name

    for folder, m in mods.items():
        try:
            n = len(m.files())
        except OSError:
            n = 0
        tree.insert('', 'end', folder, text=label(folder), values=(n,))

    def toggle(iid=None):
        iid = iid or (tree.selection() or [None])[0]
        if not iid:
            return
        ticked.symmetric_difference_update({iid})
        tree.item(iid, text=label(iid))
        count.set('%d mod(s) ticked' % len(ticked))

    def click(e):
        iid = tree.identify_row(e.y)
        if iid and tree.identify_column(e.x) == '#0':
            tree.selection_set(iid)
            toggle(iid)
            return 'break'
    tree.bind('<Button-1>', click)
    tree.bind('<space>', lambda e: toggle())

    def move(d):
        s = tree.selection()
        if s:
            i = tree.index(s[0])
            tree.move(s[0], '', max(0, i + d))
            tree.see(s[0])
    ttk.Button(side, text='Up', command=lambda: move(-1)).pack(fill='x', pady=1)
    ttk.Button(side, text='Down', command=lambda: move(1)).pack(fill='x', pady=1)
    count = tk.StringVar(value='Click a mod to tick it.')
    ttk.Label(f, textvariable=count, style='Dim.TLabel').pack(anchor='w')
    nf = ttk.Frame(f)
    nf.pack(fill='x', pady=(6, 0))
    ttk.Label(nf, text='New mod name:').pack(side='left')
    name = tk.StringVar(value='Merged mod')
    ttk.Entry(nf, textvariable=name).pack(side='left', fill='x', expand=True, padx=4)
    bar = ttk.Frame(f)
    bar.pack(fill='x', pady=(8, 0))
    status = tk.StringVar(value='')
    ttk.Label(f, textvariable=status, wraplength=530, justify='left').pack(fill='x', pady=(6, 0))

    def go():
        order = [i for i in tree.get_children() if i in ticked]
        if len(order) < 2:
            messagebox.showinfo('Merge mods', 'Tick at least two mods.', parent=w)
            return
        nm = name.get().strip()
        if not nm:
            messagebox.showinfo('Merge mods', 'Give the new mod a name.', parent=w)
            return
        if any(m.name.lower() == nm.lower() for m in zm.list_mods()):
            messagebox.showerror('Merge mods', 'There is a mod called "%s" already - pick another name.' % nm, parent=w)
            return
        picked = [mods[i] for i in order]
        root = app.root_var.get()
        status.set('Merging…')

        def job():
            return merge_into_new(picked, nm, root, log=lambda s: app.q.put(lambda s=s: app.log_build(s)))

        def done(r):
            new, lines = r
            for ln in lines:
                app.log_build(ln)
            app.refresh_mods()
            names = list(app.mods_list.get(0, 'end'))
            if new.name in names:
                app.mods_list.selection_clear(0, 'end')
                app.mods_list.selection_set(names.index(new.name))
                app.mods_list.see(names.index(new.name))
                app.show_mod()
            app.status_var.set('Made the merged mod "%s".' % new.name)
            try:
                status.set('\n'.join(lines[:3]) + ('\n(the full list is in the build log)' if len(lines) > 3 else ''))
            except tk.TclError:
                pass
        app.run_bg('Merging mods', job, done)

    ttk.Button(bar, text='Close', command=w.destroy).pack(side='right')
    ttk.Button(bar, text='Make merged mod', command=go).pack(side='right', padx=6)
    w.bind('<Escape>', lambda e: w.destroy())
    return w
