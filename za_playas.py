"""Play as another character: the player's model becomes one of the game's human characters (a rival, a boss,
the hotel manager...), and the player's own face, hair and clothes become invisible.

How the player is put together: ik_chara/model_pc_base/model/p0_base.trmdl is the base - it has the skeleton
but no meshes. Everything visible is a part on top of it: a face (p0_face*), hair (p0_hrs*) and every
clothing item (p1_/p2_ upb, lwb, drs, p0_ ftw, lgw, glv, hdw, bag, acc, eyw), each in its own model_pc folder
with its own skeleton, matched to the base by bone names.

The mod: the base model gets the character's meshes, materials and skeleton (every character's skeleton has the
player's bones, so the player's animations move it), and every part's model is replaced by an empty one - the
same kind of model the base normally is - so whatever the save wears, nothing is drawn over the character.
"""
import json
import os
import re

import za_modding as zm

BASE_DIR = 'ik_chara/model_pc_base/model'
BASE_MDL = BASE_DIR + '/p0_base.trmdl'
PARTS_DIR = 'ik_chara/model_pc'
CHAR_DIRS = ('ik_chara/model_uq', 'ik_chara/model_cc_ir')
# names from the game's trainer names where the model is certain; the rest say what the model is
NICE = {'hotel_manager': 'Hotel manager', 'president': 'President', 'secretary': 'Vinnie (secretary)',
        'friend_f': 'Lida', 'friend_m': 'Naveen', 'rival_f': 'Taunie', 'rival_m': 'Urbain',
        'detective': 'Emma', 'researcher': 'Researcher', 'co_member_m': 'Member of group CO (male)',
        'co_member_f': 'Member of group CO (female)', 'f': 'Woman', 'co': 'Member of group CO',
        'ca': 'Member of group CA'}


class PlayAsError(Exception):
    pass


def _bones(path):
    d = json.loads(zm.to_json(path, zm.schema_for(path)))
    return {n.get('Name') for n in d.get('TransformNodes') or [] if n.get('Name')}


def characters(root):
    """[(label, game folder)] of every human character model that can stand in for the player."""
    out = []
    player = _bones(os.path.join(root, *BASE_DIR.split('/'), 'p0_base.trskl'))
    for d in CHAR_DIRS:
        base = os.path.join(root, *d.split('/'))
        if not os.path.isdir(base):
            continue
        for f in sorted(os.listdir(base)):
            m = re.match(r'^tr(\d{4})_(\d\d)(?:_(.+))?$', f)
            folder = os.path.join(base, f)
            stem = 'tr%s_%s' % (m.group(1), m.group(2)) if m else f
            if not m or m.group(1) == '9999' or not os.path.isfile(os.path.join(folder, stem + '.trmdl')):
                continue
            sk = os.path.join(folder, stem + '.trskl')
            try:
                missing = player - _bones(sk)
            except Exception:  # noqa: BLE001
                continue
            if len(missing) > 4:                 # not a human body the player's animations can move
                continue
            what = m.group(3) or ''
            label = NICE.get(what)
            if label is None:
                b = re.match(r'^([a-z]+)_(boss|leader)(?:_([mf]))?$', what)
                label = ('%s of group %s%s' % (b.group(2).capitalize(), b.group(1).upper(),
                                                {'m': ' (male)', 'f': ' (female)'}.get(b.group(3), ''))
                         if b else what.replace('_', ' ').capitalize() if what else 'Character')
            if m.group(2) != '00':
                label += ' (variant %d)' % int(m.group(2))
            out.append(('%s  -  tr%s' % (label, m.group(1)), '%s/%s' % (d, f)))
    return out


def _empty(mdl):
    """An empty model like the player's base: no meshes, materials or LODs, the same skeleton."""
    return {'Meshes': [], 'Skeleton': mdl.get('Skeleton') or {}, 'Materials': [], 'LODs': [],
            'Bounds': {'MinBound': {'X': 0.0, 'Y': 0.0, 'Z': 0.0}, 'MaxBound': {'X': 0.0, 'Y': 0.0, 'Z': 0.0}},
            'Field_06': {'X': 0.0, 'Y': 0.0, 'Z': 0.0, 'W': 0.0}}


def make(root, mod, char_rel, hide_parts=True, log=None):
    """Make `mod` turn the player into the character in game folder `char_rel` (from characters())."""
    say = log or (lambda s: None)
    src = os.path.join(root, *char_rel.split('/'))
    stem = re.match(r'^(tr\d{4}_\d\d)', os.path.basename(char_rel))
    if not stem or not os.path.isfile(os.path.join(src, stem.group(1) + '.trmdl')):
        raise PlayAsError('Not a character model folder: ' + char_rel)
    stem = stem.group(1)
    sch = zm.schema_for(os.path.join(src, stem + '.trmdl'))
    mdl = json.loads(zm.to_json(os.path.join(src, stem + '.trmdl'), sch))
    # 1. the character's files next to the base model (names kept: the character's model points at them)
    n = 0
    for f in sorted(os.listdir(src)):
        p = os.path.join(src, f)
        if os.path.isfile(p) and not f.endswith('.trmdl'):
            mod.add_file('%s/%s' % (BASE_DIR, f), p)
            n += 1
    # 2. the base model is now the character's model
    out = mod.path_for(BASE_MDL)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    zm.from_json(json.dumps(mdl), sch, out)
    say('base model: %s (%d files)' % (os.path.basename(char_rel), n))
    # 3. the player's own parts drawn as nothing
    hidden = 0
    if hide_parts:
        parts = os.path.join(root, *PARTS_DIR.split('/'))
        for f in sorted(os.listdir(parts)):
            d = os.path.join(parts, f)
            if not os.path.isdir(d):
                continue
            for x in os.listdir(d):
                if x.endswith('.trmdl'):
                    pm = json.loads(zm.to_json(os.path.join(d, x), sch))
                    o = mod.path_for('%s/%s/%s' % (PARTS_DIR, f, x))
                    os.makedirs(os.path.dirname(o), exist_ok=True)
                    zm.from_json(json.dumps(_empty(pm)), sch, o)
                    hidden += 1
        say('player face, hair and clothes hidden: %d models' % hidden)
    return {'character': char_rel, 'files': n, 'hidden': hidden}


def open_window(app):
    """Mods tab > Play as a character...: pick a character, see it in 3D, put it into the current mod."""
    import threading
    import tkinter as tk
    from tkinter import messagebox, ttk
    root = os.path.normpath(app.root_var.get())
    w = tk.Toplevel(app)
    w.title('Play as a character')
    w.geometry('560x560')
    bg = ttk.Style(w).lookup('TFrame', 'background') or None
    if bg:
        w.configure(background=bg)
    f = ttk.Frame(w, padding=12)
    f.pack(fill='both', expand=True)
    ttk.Label(f, wraplength=530, justify='left', text=(
        'Your player becomes one of the game\'s characters. Its own face, hair and clothes are hidden, '
        'whatever the save wears, so only the character is drawn. Pick one, "Show in 3D" to see it, then '
        '"Put into current mod".')).pack(fill='x')
    lf = ttk.Frame(f)
    lf.pack(fill='both', expand=True, pady=8)
    lst = tk.Listbox(lf, height=16, exportselection=False, activestyle='none', borderwidth=0, highlightthickness=0)
    sbar = ttk.Scrollbar(lf, orient='vertical', command=lst.yview)
    lst.configure(yscrollcommand=sbar.set)
    sbar.pack(side='right', fill='y')
    lst.pack(side='left', fill='both', expand=True)
    try:
        c = app.theme_colours()              # the program's light / dark theme
        lst.configure(background=c['field'], foreground=c['fg'], selectbackground=c['sel'], selectforeground=c['selfg'])
    except Exception:  # noqa: BLE001
        pass
    status = tk.StringVar(value='Reading the characters…')
    hide = tk.BooleanVar(value=True)
    ttk.Checkbutton(f, text="Hide the player's own face, hair and clothes", variable=hide).pack(anchor='w')
    bar = ttk.Frame(f)
    bar.pack(fill='x', pady=(8, 0))
    ttk.Label(f, textvariable=status, style='Dim.TLabel', wraplength=530, justify='left').pack(fill='x', pady=(6, 0))
    chars = []

    def cur():
        s = lst.curselection()
        return chars[s[0]] if s else None

    def show():
        c = cur()
        if not c:
            return
        stem = re.match(r'^(tr\d{4}_\d\d)', os.path.basename(c[1])).group(1)
        app.open_viewer(os.path.join(root, *c[1].split('/'), stem + '.trmdl'), with_mod=False)

    def put():
        c = cur()
        if not c:
            messagebox.showinfo('Play as a character', 'Pick a character first.', parent=w)
            return
        m = app.current_mod()
        if not m:
            return
        if any(r.startswith(BASE_DIR + '/') for r in m.files()) and not messagebox.askyesno(
                'Play as a character', 'Mod "%s" already changes the player model. Replace that?' % m.name,
                parent=w):
            return
        h = hide.get()

        def job():
            m.snapshot()
            remove(root, m)
            r = make(root, m, c[1], hide_parts=h)
            m.snapshot('play as %s' % c[0])
            return r

        def done(r):
            app.mod_changed(m, BASE_MDL, 'player is now %s' % c[0].split('  -  ')[0])
            try:
                status.set('Done: in "%s" the player is %s%s. Install the mod and load a copy of your save.' % (
                    m.name, c[0].split('  -  ')[0], ' (own face, hair and clothes hidden: %d models)' % r['hidden']
                    if r['hidden'] else ''))
            except tk.TclError:
                pass
        app.run_bg('Making the player model', job, done)

    def undo():
        m = app.current_mod()
        if not m:
            return

        def job():
            m.snapshot()
            n = remove(root, m)
            m.snapshot('player model change removed')
            return n

        def done(n):
            app.mod_changed(m, BASE_MDL, 'player model change removed')
            try:
                status.set('Removed %d file(s) from "%s": the player looks as normal again.' % (n, m.name))
            except tk.TclError:
                pass
        app.run_bg('Removing the player model change', job, done)

    ttk.Button(bar, text='Show in 3D', command=show).pack(side='left')
    ttk.Button(bar, text='Remove from mod', command=undo).pack(side='left', padx=6)
    ttk.Button(bar, text='Put into current mod', command=put).pack(side='right')
    lst.bind('<Double-1>', lambda e: show())

    def load():
        try:
            r = characters(root)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            app.q.put(lambda: status.set('Could not read the characters: ' + msg))
            return

        def fill():
            chars[:] = r
            for label, _ in r:
                lst.insert('end', label)
            status.set('%d characters.' % len(r))
        app.q.put(fill)
    threading.Thread(target=load, daemon=True).start()
    return w


def remove(root, mod):
    """Take the player model change out of `mod` again."""
    gone = 0
    for rel in list(mod.files()):
        if rel.startswith(BASE_DIR + '/') or (rel.startswith(PARTS_DIR + '/') and rel.endswith('.trmdl')):
            mod.remove(rel)
            gone += 1
    return gone
