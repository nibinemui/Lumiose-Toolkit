"""In-game test checklist: what a mod changes, turned into things to check in the game before sharing it.

build(root, mod, names) -> [(section title, [line, ...]), ...]; as_text() makes it plain text for pasting.
Everything here only reads files."""
import json
import os
import re

import za_modding as zm

PERSONAL = 'avalon/data/personal_array.bin'
BANK_EXT = '.bnk'
MODEL_EXT = ('.trmdl', '.trmsh', '.trmbf', '.trskl', '.trmmt', '.trmtr')
ANIM_EXT = ('.tranm', '.tracm', '.tracn', '.traef')

ALWAYS_START = [
    'Install the mod with nothing else changed, start the game and load a save - no crash, no missing textures.',
    'Back up the save first if anything below adds new items or hairstyles.',
]
ALWAYS_END = [
    'Save, quit to the title screen and load again - everything above is still right.',
    'Online: test with a second player who has the same mod build (and once with a player who does not, if '
    'that should work).',
    'Uninstall the mod and load the same save - note what happens (new items / hairstyles worn = possible crash).',
]


def _table_label(rel):
    import za_dataedit
    for p in za_dataedit.PRESETS:
        if p['rel'] == rel:
            return p['name'], p
    return None, None


def _rows(path):
    d = json.loads(zm.to_json(path))
    for k, v in d.items():
        if isinstance(v, list):
            return v
    return []


def _changed_rows(root, mod, rel):
    """(changed indices, added count) of a table the toolkit can read, or None."""
    gp = os.path.join(root, *rel.split('/'))
    if not os.path.isfile(gp) or not zm.schema_for(gp):
        return None
    try:
        g, m = _rows(gp), _rows(mod.path_for(rel))
    except Exception:  # noqa: BLE001 - not readable: just list the file
        return None
    changed = [i for i in range(min(len(g), len(m))) if g[i] != m[i]]
    return changed, max(0, len(m) - len(g)), m


_NAMES = {}


def _row_name(root, preset, r, i):
    try:
        if root not in _NAMES:
            import za_dataedit
            _NAMES[root] = za_dataedit.Names(root)
        lab = preset['label'](r, _NAMES[root]) if preset else ''
        return str(lab).strip() or '#%d' % i
    except Exception:  # noqa: BLE001
        return '#%d' % i


def _short(names, n=8):
    names = list(dict.fromkeys(names))
    return ', '.join(names[:n]) + (' and %d more' % (len(names) - n) if len(names) > n else '')


def build(root, mod, names=None, describer=None):
    files = mod.files()
    if not files:
        return [('Nothing to test', ['This mod has no files yet.'])]
    sections = []
    done = set()
    say_files = lambda fs: done.update(fs)  # noqa: E731

    # new clothing items + hairstyles (the item check knows their names, shops and salons)
    try:
        import za_newitem as ni
        lines, problems = ni.check_mod(root, mod)
    except Exception:  # noqa: BLE001
        lines, problems = [], 0
    items, cur = [], None
    for ln in lines + ['']:
        m = re.match(r'^(\S.*?)  \(([^,]+),', ln)
        where = re.search(r'(?:sold in|offered by) (.+)$', ln)
        if where and cur:
            cur[2] = where.group(1)
        if (m or not ln) and cur:
            nm, what, shop = cur
            if what == 'hairstyle':
                items.append('Hairstyle "%s": choose it at the hair salon (%s), look at it from all sides, run, sit '
                             'and battle - nothing pokes through the head or hat. Try it with a hat on.'
                             % (nm, shop or 'no salon offers it yet'))
            else:
                items.append('"%s": buy it (%s) and wear it. Check every colour, walk/run/battle in it, and look at it '
                             'with other clothes.' % (nm, shop or 'no shop sells it yet'))
            cur = None
        if m:
            cur = [m.group(1), m.group(2), '']
    if items:
        if problems:
            items.insert(0, '"Check new items" (Mods tab) found %d problem(s) - fix those first.' % problems)
        sections.append(('New clothing items & hairstyles', items))
    new_folders = {f.split('/')[2] for f in files if re.match(r'ik_chara/(model_pc|motion_pc|demo_pc)/[^/]+/', f)
                   and not os.path.isdir(os.path.join(root, 'ik_chara', f.split('/')[1], f.split('/')[2]))}
    for f in files:
        if re.search(r'(dress_up_|hair_make_|shop_dress_up_lineup|shop_hair_make_lineup|/sav_pc/|/savm_pc/)', f) or \
                (f.startswith('ik_chara/') and f.count('/') > 2 and f.split('/')[2] in new_folders) or \
                re.match(r'ik_message/dat/[^/]+/common/dressup_item_name\.', f):
            done.add(f)

    # sounds and songs
    banks = [f for f in files if f.lower().endswith(BANK_EXT)]
    if banks:
        say_files(banks)
        snd = []
        try:
            import za_soundbank as sb
            ids = sb.changed_ids(root, mod)
            idx = sb.load_index(root)
            ents = [e for e in (idx or {}).get('entries', []) if e['id'] in ids] if idx else []
            songs = [e for e in ents if e.get('music') or e.get('type') == 'Music']
            sfx = [e for e in ents if e not in songs]
            for e in songs[:20]:
                snd.append('Song "%s": hear it where it plays - right volume next to the other music, and let it '
                           'run to the loop point: the loop is seamless and starts where you set it.' % sb.label(e))
            for e in sfx[:30]:
                snd.append('Sound "%s": trigger it in game - plays, right volume, not cut off.' % sb.label(e))
            more = len(songs) + len(sfx) - len(songs[:20]) - len(sfx[:30])
            if more > 0:
                snd.append('…and %d more replaced sounds (Audio tab, "Only sounds in my mod").' % more)
            if ids and not ents:
                snd.append('%d sound(s) replaced - open the Audio tab once so they can be listed by name.' % len(ids))
        except Exception:  # noqa: BLE001
            pass
        if not snd:
            snd.append('Sound banks changed: %s - play the sounds they hold in game.' % _short(
                os.path.basename(b) for b in banks))
        sections.append(('Sounds & music', snd))

    # Pokémon data
    if PERSONAL in files:
        say_files([PERSONAL])
        r = _changed_rows(root, mod, PERSONAL)
        if r:
            changed, added, rows = r
            nm = []
            for i in changed:
                sp = rows[i].get('species', {})
                num = sp.get('species_model', i)
                nm.append((describer.pokemon_name(num) if describer else 'pm%04d' % num) +
                          (' (form %d)' % sp['species_form'] if sp.get('species_form') else ''))
            sections.append(('Pokémon data', ['Changed: %s. Check their stats/types/abilities in the summary screen, '
                                              'and the moves they learn (level up / TM).' % (_short(nm) or 'nothing')]))

    # other tables (Game data presets, map tables, anything with a schema)
    tables = []
    for f in files:
        if f in done or not f.endswith('.bin') or f.startswith('ik_message/'):
            continue
        title, preset = _table_label(f)
        r = _changed_rows(root, mod, f)
        if r is None and not title:
            continue
        done.add(f)
        title = title or os.path.splitext(os.path.basename(f))[0]
        if r is None:
            tables.append('%s changed - check it in game.' % title)
            continue
        changed, added, rows = r
        what = [_row_name(root, preset, rows[i], i) for i in changed]
        msg = '%s: %s' % (title, _short(what) if what else 'no rows changed')
        if added:
            msg += ' (+%d new row%s)' % (added, 's' if added > 1 else '')
        hint = {'Moves': 'use them in battle - power, accuracy and effect',
                'Items': 'look at them in the bag and use them',
                'Item shops': 'open the shop - prices and stock', 'Clothing shops': 'open the shop',
                'Trainers': 'battle them', 'Item balls (contents)': 'pick them up'}.get(title)
        if 'transform' in f or 'placement' in f or f.startswith('field/'):
            hint = 'walk to the spot - placed right, not floating or inside walls, can be interacted with'
        tables.append(msg + (' - ' + hint if hint else '') + '.')
    if tables:
        sections.append(('Game data & map', tables))

    # text
    text = [f for f in files if f.startswith('ik_message/') and f not in done]
    if text:
        say_files(text)
        langs = sorted({f.split('/')[2] for f in text if f.count('/') > 2})
        stems = sorted({os.path.splitext(os.path.basename(f))[0] for f in text})
        sections.append(('Text', ['Changed text: %s (%s). Read it where it shows - not cut off, line breaks right, '
                                  'nothing else shifted.' % (_short(stems, 6), _short(langs, 4))]))

    # models + animations, grouped by model folder
    groups = {}
    for f in files:
        if f in done:
            continue
        low = f.lower()
        if low.endswith(MODEL_EXT + ANIM_EXT):
            groups.setdefault(os.path.dirname(f), []).append(f)
    mdl = []
    for d, fs in sorted(groups.items()):
        say_files(fs)
        what = _describe_folder(d, describer)
        anim = any(f.lower().endswith(ANIM_EXT) for f in fs)
        if d.startswith('ik_pokemon/'):
            mdl.append('%s: see it in the wild and in battle - idle, walking, every move animation, and its mega '
                       'evolution / other forms if it has them.' % what)
        else:
            mdl.append('%s: look at it in game from all sides%s.' % (what, ' and play its animations' if anim else ''))
    if mdl:
        sections.append(('Models & animations', mdl))

    # textures
    tex = [f for f in files if f not in done and f.lower().endswith('.bntx')]
    if tex:
        say_files(tex)
        by = {}
        for f in tex:
            desc = describer.describe(f) if describer else ''
            by.setdefault(desc.split(' · ')[0] if desc else os.path.dirname(f), []).append(f)
        lines = ['%s (%d texture%s) - look at it in game: right colours, not blurry, no seams.' % (
            k, len(v), 's' if len(v) > 1 else '') for k, v in sorted(by.items())]
        sections.append(('Textures', lines[:40] + (['…and %d more.' % (len(lines) - 40)] if len(lines) > 40 else [])))

    rest = [f for f in files if f not in done]
    if rest:
        sections.append(('Other files', ['%s changed - check what it affects.' % _short(rest, 10)]))
    return [('Every test', ALWAYS_START)] + sections + [('Last', ALWAYS_END)]


def _describe_folder(d, describer):
    m = re.search(r'pm(\d{4})_(\d\d)_(\d\d)', d)
    if m and describer:
        name = describer.pokemon_name(int(m.group(1)))
        form = int(m.group(2))
        return '%s%s (%s)' % (name, ' form %d' % form if form else '', d.split('/')[-1])
    return d


def as_text(mod_name, sections):
    out = ['In-game test checklist - %s' % mod_name, '']
    for title, lines in sections:
        out.append(title)
        out += ['[ ] ' + ln for ln in lines]
        out.append('')
    return '\n'.join(out).rstrip() + '\n'


def open_window(app, mod):
    """Mods tab > Test checklist…: the checklist with tick boxes, copy and save."""
    import tkinter as tk
    from tkinter import filedialog, ttk
    root = app.root_var.get()
    desc = None
    try:
        import za_texnames
        desc = za_texnames.Describer(root, getattr(app, 'names', None))
    except Exception:  # noqa: BLE001
        pass

    def job():
        return build(root, mod, describer=desc)

    def done(sections):
        w = tk.Toplevel(app)
        w.title('Test checklist - %s' % mod.name)
        w.transient(app)
        w.geometry('820x640')
        bg = ttk.Style(w).lookup('TFrame', 'background') or None
        if bg:
            w.configure(background=bg)
        top = ttk.Frame(w, padding=(12, 10, 12, 4))
        top.pack(fill='x')
        ttk.Label(top, text='In-game test checklist', font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        ttk.Label(top, wraplength=780, justify='left', text=(
            'Made from what "%s" changes. Install the mod (Mods tab), go through the list in game and tick what '
            'works. Copy it to share with whoever tests with you.' % mod.name)).pack(anchor='w', pady=(2, 0))
        body = ttk.Frame(w)
        body.pack(fill='both', expand=True, padx=12, pady=6)
        cv = tk.Canvas(body, highlightthickness=0, background=bg or 'white')
        sb = ttk.Scrollbar(body, orient='vertical', command=cv.yview)
        inner = ttk.Frame(cv)
        inner.bind('<Configure>', lambda e: cv.configure(scrollregion=cv.bbox('all')))
        cv.create_window((0, 0), window=inner, anchor='nw')
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        wheel = lambda e: cv.yview_scroll(-1 * (e.delta // 120), 'units')  # noqa: E731
        body.bind('<Enter>', lambda e: cv.bind_all('<MouseWheel>', wheel))
        body.bind('<Leave>', lambda e: cv.unbind_all('<MouseWheel>'))
        w.bind('<Destroy>', lambda e: cv.unbind_all('<MouseWheel>') if e.widget is w else None)
        ticks = []
        for title, lines in sections:
            ttk.Label(inner, text=title, font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(8, 2))
            for ln in lines:
                v = tk.BooleanVar()
                ticks.append((v, ln))
                ttk.Checkbutton(inner, text=ln, variable=v).pack(anchor='w', padx=(8, 0), pady=1)
        for c in inner.winfo_children():
            if isinstance(c, ttk.Checkbutton):          # ttk.Checkbutton has no wraplength: wrap the text here
                c.configure(takefocus=0, text=_wrap(c.cget('text'), 105))

        def text():
            out = ['In-game test checklist - %s' % mod.name, '']
            i = 0
            for title, lines in sections:
                out.append(title)
                for ln in lines:
                    out.append(('[x] ' if ticks[i][0].get() else '[ ] ') + ln)
                    i += 1
                out.append('')
            return '\n'.join(out).rstrip() + '\n'

        def copy():
            w.clipboard_clear()
            w.clipboard_append(text())
            status.set('Copied.')

        def save():
            p = filedialog.asksaveasfilename(parent=w, defaultextension='.txt', initialfile='%s test checklist.txt' % mod.name,
                                             filetypes=[('Text', '*.txt')])
            if p:
                with open(p, 'w', encoding='utf-8') as f:
                    f.write(text())
                status.set('Saved.')
        bar = ttk.Frame(w, padding=(12, 0, 12, 10))
        bar.pack(fill='x')
        status = tk.StringVar()
        ttk.Label(bar, textvariable=status).pack(side='left')
        ttk.Button(bar, text='Close', command=w.destroy).pack(side='right')
        ttk.Button(bar, text='Save as text…', command=save).pack(side='right', padx=4)
        ttk.Button(bar, text='Copy', command=copy).pack(side='right')
        w.bind('<Escape>', lambda e: w.destroy())
        app.test_checklist_win = w
    app.run_bg('Making the test checklist', job, done)


def _wrap(s, n):
    words, lines, cur = s.split(' '), [], ''
    for wd in words:
        if cur and len(cur) + 1 + len(wd) > n:
            lines.append(cur)
            cur = wd
        else:
            cur = (cur + ' ' + wd).strip()
    lines.append(cur)
    return '\n'.join(lines)
