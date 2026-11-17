"""New clothing items: make a new, buyable clothing item from an existing one, without replacing anything.

What one clothing item is made of (worked out from the game's files):
  ik_chara/model_pc/<folder>/            the model (its files refer to each other by plain file name)
  ik_chara/sav_pc/<folder>/<folder>.ccdata          parts (base body + the item's model) and colour variants
  ik_chara/savm_pc/<category>/<category>.ccdatam    one entry per variant: "<folder>_<variant>" -> .ccdata
  world/exl/dress_up_data/dress_up_data.bin         one row per variant: id, type, texturePath, price, brand...
  world/exl/dress_up_data/dress_up_group_data.bin   type -> group number + name label
  ik_message/dat/<language>/common/dressup_item_name  the name (label -> text), every language
  world/exl/shop/shop_dress_up_lineup.bin           which shop sells it (optional)

create_item() copies an existing item into a new folder with new ids and writes all of the above into a mod
(starting from the mod's own copies of the shared tables, so several new items add up).

Save data: buying the item writes its id into the save. A save that has (or wears) the item needs the mod -
without it the game may not start. Back up saves before testing new items.
"""
import json
import os
import re
import struct
import tempfile

import za_merge
import za_modding as zm

DRESS_DATA = 'world/exl/dress_up_data/dress_up_data/dress_up_data.bin'
GROUP_DATA = 'world/exl/dress_up_data/dress_up_group_data/dress_up_group_data.bin'
LINEUP = 'world/exl/shop/shop_dress_up_lineup/shop_dress_up_lineup.bin'
HAIR_DATA = 'world/exl/hair_make_data/hair_make_data/hair_make_data.bin'
HAIR_LINEUP = 'world/exl/shop/shop_hair_make_lineup/shop_hair_make_lineup.bin'
HAIR_LIST = 'ik_chara/savm_pc/hrs/hrs.ccdatam'
FIRST_HAIR_ID = 5000       # new hairstyle ids: 5000 + 20 * (series - 60) + n (the game uses blocks 1-1402)
NAMES = 'ik_message/dat/%s/common/dressup_item_name'
FIRST_SERIES = 60          # new item ids use series 60+ (the game uses 00-31)
FIRST_NUMBER = 7000        # new model folders: <prefix>_<cat>7000_00_<label> and up (the game uses 0000-1xxx)
FIRST_GROUP = 1000         # new groups: 1000 and up (the game uses up to ~905)
# Each person on a team picks their own series (Preferences > Mods, setting "item_series", 60-99):
#   ids <category><series><item 01-99><variant>, folders 7000 + (series-60)*100 + n, groups 1000 + (series-60)*100 + n
# so new items made on different PCs never get the same numbers.


def series_ranges(series):
    """(id series, first folder number, first group number) for one person's series (60-99)."""
    series = max(FIRST_SERIES, min(99, int(series or FIRST_SERIES)))
    k = series - FIRST_SERIES
    return series, FIRST_NUMBER + 100 * k, FIRST_GROUP + 100 * k


class ItemError(Exception):
    pass


def _read(root, mod, rel):
    """The mod's version of a file if it has one, else the game's."""
    if mod is not None and os.path.isfile(mod.path_for(rel)):
        with open(mod.path_for(rel), 'rb') as f:
            return f.read()
    p = os.path.join(root, *rel.split('/'))
    with open(p, 'rb') as f:
        return f.read()


def _decode(root, mod, rel):
    data = _read(root, mod, rel)
    schema = zm.schema_for(os.path.join(root, *rel.split('/')))
    if not schema:
        raise ItemError('No schema for ' + rel)
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, os.path.basename(rel))
        with open(p, 'wb') as f:
            f.write(data)
        return json.loads(zm.to_json(p, schema)), schema


def _encode(mod, rel, obj, schema):
    zm.from_json(json.dumps(obj), schema, mod.path_for(rel))


def _rows_key(obj):
    return next(k for k, v in obj.items() if isinstance(v, list))


def split_texture_path(tp):
    """'p2_upb0011_00_turtleneck_normal_00' -> ('p2_upb0011_00_turtleneck', 'normal_00')"""
    m = re.match(r'^(p\d_[a-z]+\d{4}_\d\d_[a-z0-9]+)_(.+)$', tp or '')
    return (m.group(1), m.group(2)) if m else (None, None)


def items(root, mod=None):
    """[{type, folder, name, rows}] - every clothing item (dress-up type) with its variant rows."""
    data, _ = _decode(root, mod, DRESS_DATA)
    out = {}
    for r in data[_rows_key(data)]:
        folder, var = split_texture_path(r.get('texturePath', ''))
        if folder:
            out.setdefault(r.get('type', ''), {'type': r.get('type', ''), 'folder': folder, 'rows': []})['rows'].append(r)
    return out


def lineups(root, mod=None):
    """[(lineup id, number of items)] of the clothing shops."""
    data, _ = _decode(root, mod, LINEUP)
    return [(r.get('id', ''), len(r.get('items', []))) for r in data[_rows_key(data)]]


def _ccdatam_for(root, mod, folder):
    """(rel path of the .ccdatam that lists this folder, decoded, schema)"""
    base = os.path.join(root, 'ik_chara', 'savm_pc')
    for cat in sorted(os.listdir(base)):
        rel = 'ik_chara/savm_pc/%s/%s.ccdatam' % (cat, cat)
        if not os.path.isfile(os.path.join(root, *rel.split('/'))):
            continue
        obj, schema = _decode(root, mod, rel)
        if any(e.get('name', '').startswith(folder + '_') for e in obj.get('entries', [])):
            return rel, obj, schema
    raise ItemError('No category list (.ccdatam) mentions %s.' % folder)


def _all_mods(mod):
    """The current mod and every other mod in the mods folder: new items must not reuse ids, folder or group
    numbers any of them already uses - mods installed together are merged, and duplicates would clash."""
    out = [mod] if mod is not None else []
    try:
        out += [m for m in zm.list_mods() if mod is None or os.path.normcase(m.folder) != os.path.normcase(mod.folder)]
    except OSError:
        pass
    return out


def _all_folders(root, mod):
    out = set(os.listdir(os.path.join(root, 'ik_chara', 'model_pc')))
    for m in _all_mods(mod):
        if os.path.isdir(m.path_for('ik_chara/model_pc')):
            out |= set(os.listdir(m.path_for('ik_chara/model_pc')))
    return {f.lower() for f in out}


def _used_by_mods(root, mod, rel, field):
    """Values of `field` in every row of table `rel` in every mod (current one included)."""
    out = set()
    for m in _all_mods(mod):
        if not os.path.isfile(m.path_for(rel)):
            continue
        try:
            obj, _ = _decode(root, m, rel)
            out |= {r.get(field) for r in obj[_rows_key(obj)]}
        except Exception:  # noqa: BLE001 - an unreadable table just isn't counted
            pass
    return out


def new_folder_name(root, mod, src_folder, label, series=FIRST_SERIES):
    """<prefix>_<cat>NNNN_00_<label> with the first free NNNN of this person's range."""
    m = re.match(r'^(p\d)_([a-z]+)\d{4}_\d\d_', src_folder)
    if not m:
        raise ItemError('Unexpected model folder name: ' + src_folder)
    label = re.sub(r'[^a-z0-9]+', '', label.lower())[:20] or 'custom'
    taken = _all_folders(root, mod)
    first = series_ranges(series)[1]
    for n in list(range(first, first + 100)) + list(range(FIRST_NUMBER + 4000, 10000)):
        name = '%s_%s%04d_00_%s' % (m.group(1), m.group(2), n, label)
        if not any(t.startswith('%s_%s%04d_' % (m.group(1), m.group(2), n)) for t in taken):
            return name
    raise ItemError('No free model folder number left.')


def new_ids(src_ids, used, count, series=FIRST_SERIES):
    """ids for `count` variants: the source's category digits + this person's series + a free item number +
    variant 01..count. Only this series is used, so different people's items never meet."""
    prefix = str(src_ids[0])[:-6]
    if count > 99:
        raise ItemError('This item has more than 99 colour variants.')
    for series in [series_ranges(series)[0]]:
        for item in range(1, 100):
            ids = [int('%s%02d%02d%02d' % (prefix, series, item, v + 1)) for v in range(count)]
            if not any(i in used for i in ids):
                return ids
    raise ItemError('No free item ids left in series %d for this category - pick another series in '
                    'Preferences > Mods.' % series)


def add_name(dat, tbl, label, text):
    """Append one label + line to a message .dat/.tbl pair (labels without a line stay at the end)."""
    lines = za_merge.msg_raw(dat)
    ents = za_merge.tbl_entries(tbl)
    if any(lab == label for _, lab in ents):
        raise ItemError('The name label %s exists already.' % label)
    raw_label = label.encode('utf-8') + b'\0'
    entry = struct.pack('<QH', zm.fnv1a64(label), len(raw_label)) + raw_label
    flag = lines[-1][1] if lines else 0
    lines.append((tuple(zm._encode_text(text)), flag))
    ents = ents[:len(lines) - 1] + [(entry, label)] + ents[len(lines) - 1:]
    return za_merge.msg_build(lines), za_merge.tbl_build(ents)


def create_item(root, mod, src_type, name, price=None, shop=None, label='custom', log=None, series=FIRST_SERIES):
    """Make a new clothing item from the existing item `src_type` (a dress_up_data "type") in `mod`.
    Returns a report dict: folder, ids, type, name label, files written."""
    say = log or (lambda s: None)
    data, data_schema = _decode(root, mod, DRESS_DATA)
    key = _rows_key(data)
    rows = data[key]
    src_rows = [r for r in rows if r.get('type') == src_type]
    if not src_rows:
        raise ItemError('No clothing item with type "%s".' % src_type)
    src_folder, _ = split_texture_path(src_rows[0].get('texturePath', ''))
    if not src_folder or any(split_texture_path(r.get('texturePath', ''))[0] != src_folder for r in src_rows):
        raise ItemError('This item uses several model folders - it cannot be copied yet.')
    folder = new_folder_name(root, mod, src_folder, label, series)
    fm = re.match(r'^p\d_([a-z]+)(\d{4})_\d\d_(.+)$', folder)
    new_type = '%s_%s%s_1' % (fm.group(1), fm.group(3), fm.group(2))        # e.g. upb_custom7000_1
    if any(r.get('type') == new_type for r in rows):
        raise ItemError('The type %s exists already.' % new_type)
    written = []

    # 1. model folder (the game's original files of the source item)
    src_dir = os.path.join(root, 'ik_chara', 'model_pc', src_folder)
    if not os.path.isdir(src_dir):
        raise ItemError('Model folder not found: ' + src_folder)
    for f in sorted(os.listdir(src_dir)):
        p = os.path.join(src_dir, f)
        if os.path.isfile(p):
            mod.add_file('ik_chara/model_pc/%s/%s' % (folder, f), p)
            written.append('ik_chara/model_pc/%s/%s' % (folder, f))
    say('copied the model: ik_chara/model_pc/%s (%d files)' % (folder, len(written)))

    # 2. .ccdata: the same parts and variants, the item's part pointing at the new folder
    src_cc = 'ik_chara/sav_pc/%s/%s.ccdata' % (src_folder, src_folder)
    cc, cc_schema = _decode(root, None, src_cc)
    for part in cc.get('parts', []):
        for k in ('model', 'material_set'):
            if part.get(k):
                part[k] = part[k].replace('/%s/' % src_folder, '/%s/' % folder)
    cc_rel = 'ik_chara/sav_pc/%s/%s.ccdata' % (folder, folder)
    _encode(mod, cc_rel, cc, cc_schema)
    written.append(cc_rel)
    variants = [v.get('name') for v in cc.get('variants', [])]

    # 3. category list: one entry per variant the item's rows use
    m_rel, m_obj, m_schema = _ccdatam_for(root, mod, src_folder)
    used_vars = [split_texture_path(r.get('texturePath', ''))[1] for r in src_rows]
    for v in used_vars:
        if v not in variants:
            raise ItemError('Variant %s is missing from %s.' % (v, src_cc))
        m_obj['entries'].append({'name': '%s_%s' % (folder, v),
                                 'ccdata': '../../sav_pc/%s/%s.ccdata' % (folder, folder), 'variant': v})
    _encode(mod, m_rel, m_obj, m_schema)
    written.append(m_rel)

    # 4. clothing rows with new ids
    used = {r.get('id') for r in rows} | _used_by_mods(root, mod, DRESS_DATA, 'id')
    ids = new_ids([r['id'] for r in src_rows], used, len(src_rows), series)
    ui_max = max((r.get('uiIndex', 0) for r in rows), default=0)
    for k, (r, i) in enumerate(zip(src_rows, ids)):
        n = dict(r)
        n.update({'id': i, 'type': new_type, 'uiIndex': ui_max + 1 + k,
                  'texturePath': '%s_%s' % (folder, split_texture_path(r['texturePath'])[1])})
        if price is not None:
            n['price'] = int(price)
        rows.append(n)
    _encode(mod, DRESS_DATA, data, data_schema)
    written.append(DRESS_DATA)
    say('added %d colour variant(s), ids %d-%d' % (len(ids), ids[0], ids[-1]))

    # 5. group: type -> name label
    groups, g_schema = _decode(root, mod, GROUP_DATA)
    gk = _rows_key(groups)
    name_label = 'dressup_item_group_lt_%s' % folder.split('_', 1)[1]
    taken = {g.get('group', 0) for g in groups[gk]} | _used_by_mods(root, mod, GROUP_DATA, 'group')
    g0 = series_ranges(series)[2]
    group_no = next(n for n in range(g0, g0 + 100000) if n not in taken)
    groups[gk].append({'type': new_type, 'group': group_no,
                       'name_label': name_label})
    _encode(mod, GROUP_DATA, groups, g_schema)
    written.append(GROUP_DATA)

    # 6. the name, in every language the game has
    langs = sorted(d for d in os.listdir(os.path.join(root, 'ik_message', 'dat'))
                   if os.path.isfile(os.path.join(root, *(NAMES % d).split('/')) + '.dat'))
    for lang in langs:
        rel = NAMES % lang
        dat, tbl = add_name(_read(root, mod, rel + '.dat'), _read(root, mod, rel + '.tbl'), name_label, name)
        mod.add_bytes(rel + '.dat', dat)
        mod.add_bytes(rel + '.tbl', tbl)
        written += [rel + '.dat', rel + '.tbl']
    say('named it "%s" in %d language(s)' % (name, len(langs)))

    # 7. shop
    if shop:
        lu, l_schema = _decode(root, mod, LINEUP)
        lk = _rows_key(lu)
        row = next((r for r in lu[lk] if r.get('id') == shop), None)
        if row is None:
            raise ItemError('No shop lineup "%s".' % shop)
        template = row['items'][0] if row.get('items') else {}
        for i in ids:
            it = json.loads(json.dumps(template))
            it['itemId'] = i
            row.setdefault('items', []).append(it)
        _encode(mod, LINEUP, lu, l_schema)
        written.append(LINEUP)
        say('added to the shop lineup %s' % shop)
    return {'folder': folder, 'type': new_type, 'ids': ids, 'name_label': name_label, 'files': written,
            'source': src_type, 'source_folder': src_folder}


# ---------------------------------------------------------------------------------------------------------
# dialog
# ---------------------------------------------------------------------------------------------------------

SAVE_NOTE = ('Save data: buying the item stores its id in your save. Keep this mod installed while a save has '
             'the item, and don\'t wear it when the mod is off - the game may not start. Back up your save first. '
             'Online, other players need the same mod to see it.')


def open_dialog(app, src_type, src_price=None):
    import tkinter as tk
    from tkinter import messagebox, ttk
    root = app.root_var.get()
    mod = app.current_mod()
    if not mod:
        return
    ui = getattr(app, 'data_ui', None)
    if ui is not None and ui.dirty and ui.rel in (DRESS_DATA, LINEUP, GROUP_DATA):
        if not messagebox.askyesno('New clothing item', 'The Game data tab has unsaved changes in this table. '
                                   'Save them first? (Otherwise they would overwrite the new item later.)'):
            return
        ui.save()
    try:
        shops = lineups(root, mod)
    except Exception:  # noqa: BLE001
        shops = []
    w = tk.Toplevel(app)
    w.title('New clothing item')
    w.transient(app)
    w.resizable(False, False)
    bg = ttk.Style(w).lookup('TFrame', 'background') or None
    if bg:
        w.configure(background=bg)
    f = ttk.Frame(w, padding=12)
    f.pack(fill='both', expand=True)
    ttk.Label(f, text='New clothing item', font=('Segoe UI', 11, 'bold')).grid(row=0, column=0, columnspan=2,
                                                                                sticky='w')
    ttk.Label(f, wraplength=460, justify='left', text=(
        'Makes a new item from "%s": a copy of its model in a new folder and new item ids, sold next to the '
        'game\'s clothes - nothing in the game is replaced. Then change its textures or colours like any item.'
        % src_type)).grid(row=1, column=0, columnspan=2, sticky='w', pady=(2, 8))
    name = tk.StringVar(value='New ' + (src_type.split('_')[1].title() if '_' in src_type else 'Item'))
    price = tk.StringVar(value=str(src_price or 1000))
    label = tk.StringVar(value='custom')
    shop_names = ['(not sold in a shop)'] + ['%s  (%d items)' % s for s in shops]
    shop = tk.StringVar(value=next((s for s in shop_names if s.startswith('dressup_02_select5_lineup')),
                                   shop_names[0]))
    for r, (lab, var, wd) in enumerate((('Name in game:', name, 34), ('Price:', price, 10),
                                        ('Folder label:', label, 16)), 2):
        ttk.Label(f, text=lab).grid(row=r, column=0, sticky='w', pady=2)
        ttk.Entry(f, textvariable=var, width=wd).grid(row=r, column=1, sticky='w', pady=2)
    ttk.Label(f, text='Shop:').grid(row=5, column=0, sticky='w', pady=2)
    ttk.Combobox(f, textvariable=shop, values=shop_names, state='readonly', width=40).grid(row=5, column=1, sticky='w')
    ttk.Label(f, text='(dressup_02_select5_lineup is Bundle Up in Rouge District)   ·   your id series: %d '
                      '(Preferences > Mods)' % series_ranges(app.settings.get('item_series', FIRST_SERIES))[0],
              style='Dim.TLabel').grid(row=6, column=1, sticky='w')
    ttk.Label(f, wraplength=460, justify='left', style='Dim.TLabel', text=SAVE_NOTE).grid(
        row=7, column=0, columnspan=2, sticky='w', pady=(10, 6))
    bar = ttk.Frame(f)
    bar.grid(row=8, column=0, columnspan=2, sticky='e')

    def create():
        try:
            pr = int(price.get())
        except ValueError:
            messagebox.showerror('New clothing item', 'The price must be a whole number.', parent=w)
            return
        if not name.get().strip():
            messagebox.showerror('New clothing item', 'Give the item a name.', parent=w)
            return
        sh = shop.get().split('  (')[0]
        sh = None if sh.startswith('(') else sh
        nm, lb = name.get().strip(), label.get().strip() or 'custom'
        w.destroy()

        def job():
            mod.snapshot()
            r = create_item(root, mod, src_type, nm, pr, sh, lb, series=app.settings.get('item_series', FIRST_SERIES))
            mod.snapshot('new clothing item: %s' % nm)
            return r

        def done(r):
            app.mod_changed(mod, '', 'new clothing item "%s"' % nm)
            if ui is not None and ui.rel in (DRESS_DATA, LINEUP, GROUP_DATA) and ui.preset:
                ui.open_preset(ui.preset['name'])          # show the new rows
            result_window(app, mod, r, nm)
        app.run_bg('Making the new clothing item', job, done)
    ttk.Button(bar, text='Create in current mod', command=create).pack(side='right')
    ttk.Button(bar, text='Cancel', command=w.destroy).pack(side='right', padx=6)
    w.bind('<Escape>', lambda e: w.destroy())
    return w


def result_window(app, mod, r, name):
    import tkinter as tk
    from tkinter import filedialog, ttk
    w = tk.Toplevel(app)
    w.title('New clothing item made')
    w.transient(app)
    bg = ttk.Style(w).lookup('TFrame', 'background') or None
    if bg:
        w.configure(background=bg)
    f = ttk.Frame(w, padding=12)
    f.pack(fill='both', expand=True)
    folder = mod.path_for('ik_chara/model_pc/' + r['folder'])
    ttk.Label(f, text='"%s" is in mod "%s"' % (name, mod.name), font=('Segoe UI', 11, 'bold')).pack(anchor='w')
    ttk.Label(f, justify='left', text=(
        'Model folder: ik_chara\\model_pc\\%s\nItem ids: %s\nType: %s\n%d files written.\n\n'
        'Change its look: Edit a texture… (live edit, like any texture), or replace files in its model folder.\n'
        'Install the mod (Mods tab) and look for it in the shop you picked.'
        % (r['folder'], ', '.join(map(str, r['ids'])), r['type'], len(r['files'])))).pack(anchor='w', pady=(4, 8))
    bar = ttk.Frame(f)
    bar.pack(fill='x')
    mdl = next((x for x in sorted(os.listdir(folder)) if x.endswith('.trmdl') and '_lod' not in x), None)
    if mdl:
        ttk.Button(bar, text='Show in 3D', command=lambda: app.open_viewer(
            os.path.join(folder, mdl), embed=app.embed_pref())).pack(side='left')

    def edit_tex():
        p = filedialog.askopenfilename(parent=w, initialdir=folder, title='Texture of the new item',
                                       filetypes=[('Textures', '*.bntx')])
        if p:
            app.live_edit_texture(p, ti=0, layer=0)
    ttk.Button(bar, text='Edit a texture…', command=edit_tex).pack(side='left', padx=6)
    ttk.Button(bar, text='Open folder', command=lambda: os.startfile(folder)).pack(side='left')
    ttk.Button(bar, text='Close', command=w.destroy).pack(side='right')
    return w


# ---------------------------------------------------------------------------------------------------------
# check a mod's new items before sharing it
# ---------------------------------------------------------------------------------------------------------

def check_mod(root, mod):
    """Check every new clothing item in `mod`. Returns (report lines, number of problems)."""
    lines, problems = [], 0

    def bad(msg):
        nonlocal problems
        if '   ✗ ' + msg in lines[item_start[0]:]:
            return                                   # the same problem for another colour variant
        problems += 1
        lines.append('   ✗ ' + msg)
    item_start = [0]
    hair_lines, hair_problems = _check_hair(root, mod)
    if not os.path.isfile(mod.path_for(DRESS_DATA)):
        if hair_lines:
            return hair_lines, hair_problems
        return ['This mod has no new clothing items or hairstyles to check.'], 0
    game, _ = _decode(root, None, DRESS_DATA)
    game_ids = {r.get('id') for r in game[_rows_key(game)]}
    game_types = {r.get('type') for r in game[_rows_key(game)]}
    data, _ = _decode(root, mod, DRESS_DATA)
    rows = data[_rows_key(data)]
    new = [r for r in rows if r.get('type') not in game_types]
    if not new:
        return (hair_lines or ['No new clothing items in this mod.']), hair_problems
    ids = [r.get('id') for r in rows]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    groups, _ = _decode(root, mod, GROUP_DATA)
    gmap = {g.get('type'): g for g in groups[_rows_key(groups)]}
    gnums = [g.get('group') for g in groups[_rows_key(groups)]]
    lu, _ = _decode(root, mod, LINEUP)
    sold = {}
    for row in lu[_rows_key(lu)]:
        for it in row.get('items', []):
            sold.setdefault(it.get('itemId'), []).append(row.get('id'))
    others = {}
    for m in _all_mods(mod)[1:]:
        if os.path.isfile(m.path_for(DRESS_DATA)):
            try:
                o, _ = _decode(root, m, DRESS_DATA)
                for r in o[_rows_key(o)]:
                    if r.get('id') not in game_ids:
                        others.setdefault(r.get('id'), m.name)
            except Exception:  # noqa: BLE001
                pass
    langs = sorted(d for d in os.listdir(os.path.join(root, 'ik_message', 'dat'))
                   if os.path.isfile(os.path.join(root, *(NAMES % d).split('/')) + '.dat'))
    texts = {}
    for lang in langs:
        rel = NAMES % lang
        try:
            import za_formats as zf
            dat, tbl = _read(root, mod, rel + '.dat'), _read(root, mod, rel + '.tbl')
            texts[lang] = dict(zip(zf.parse_tbl(tbl), zf.decode_msg(dat)))
        except Exception:  # noqa: BLE001
            texts[lang] = {}
    by_type = {}
    for r in new:
        by_type.setdefault(r.get('type'), []).append(r)
    for t, rs in sorted(by_type.items()):
        g = gmap.get(t)
        label = g.get('name_label') if g else None
        name = texts.get('English', {}).get(label, '') if label else ''
        lines.append('%s  (%s, %d colour variant(s), ids %s)' % (name or t, t, len(rs),
                                                                ', '.join(str(r.get('id')) for r in rs[:3]) +
                                                                (' …' if len(rs) > 3 else '')))
        before = problems
        item_start[0] = len(lines)
        unsold = []
        for r in rs:
            i = r.get('id')
            if i in game_ids:
                bad('id %d is also a game item' % i)
            if i in dup:
                bad('id %d is used more than once in this mod' % i)
            if i in others:
                bad('id %d is also used by the mod "%s" - they would clash when installed together' % (i, others[i]))
            folder, var = split_texture_path(r.get('texturePath', ''))
            if not folder:
                bad('id %d: texturePath "%s" is not <folder>_<variant>' % (i, r.get('texturePath')))
                continue
            mdir = mod.path_for('ik_chara/model_pc/' + folder)
            if not os.path.isdir(mdir) and not os.path.isdir(os.path.join(root, 'ik_chara', 'model_pc', folder)):
                bad('model folder ik_chara/model_pc/%s is missing' % folder)
            cc_rel = 'ik_chara/sav_pc/%s/%s.ccdata' % (folder, folder)
            if not os.path.isfile(mod.path_for(cc_rel)):
                bad('%s is missing' % cc_rel)
            else:
                try:
                    cc, _ = _decode(root, mod, cc_rel)
                    if var not in [v.get('name') for v in cc.get('variants', [])]:
                        bad('colour variant %s is not in %s' % (var, cc_rel))
                    for part in cc.get('parts', []):
                        mp = part.get('model', '')
                        if mp and 'model_pc/%s/' % folder in mp:
                            f = os.path.join(mdir, os.path.basename(mp))
                            if not os.path.isfile(f):
                                bad('the model %s (named in the .ccdata) is missing' % os.path.basename(mp))
                except Exception as e:  # noqa: BLE001
                    bad('%s cannot be read: %s' % (cc_rel, e))
            try:
                m_rel, m_obj, _ = _ccdatam_for(root, mod, folder)
                if '%s_%s' % (folder, var) not in [e.get('name') for e in m_obj.get('entries', [])]:
                    bad('%s_%s is not listed in %s' % (folder, var, m_rel))
            except ItemError:
                bad('no category list (.ccdatam) lists %s' % folder)
            if i not in sold:
                unsold.append(i)
        if unsold:
            lines.append('   · %d colour variant(s) not sold in any shop (fine if you add them another way)' % len(unsold))
        if not g:
            bad('no group row for %s - the game has no name for it' % t)
        else:
            if gnums.count(g.get('group')) > 1:
                bad('group number %s is used more than once' % g.get('group'))
            missing = [lang for lang in langs if not texts.get(lang, {}).get(label)]
            if missing:
                bad('no name in %d language(s): %s' % (len(missing), ', '.join(missing)))
        if problems == before:
            lines.append('   ✓ complete: model, .ccdata, category list, name in %d languages%s' % (
                len(langs), ', sold in ' + ', '.join(sorted({x for r in rs for x in sold.get(r.get('id'), [])}))
                if any(r.get('id') in sold for r in rs) else ''))
    head = ('%d new item(s) checked: %s.' % (len(by_type), 'no problems' if not problems else
                                               '%d problem(s)' % problems))
    return [head, ''] + lines + ([''] + hair_lines if hair_lines else []), problems + hair_problems


def _check_hair(root, mod):
    """Check the mod's new hairstyles: model, .ccdata, hairstyle list, name, salons, ids."""
    if not os.path.isfile(mod.path_for(HAIR_DATA)):
        return [], 0
    import za_formats as zf
    game, _ = _decode(root, None, HAIR_DATA)
    gids = {r.get('id') for r in game[_rows_key(game)]}
    gnames = {r.get('name') for r in game[_rows_key(game)]}
    data, _ = _decode(root, mod, HAIR_DATA)
    new = [r for r in data[_rows_key(data)] if str(r.get('name', '')).startswith('p0_hrs') and r.get('name') not in gnames]
    if not new:
        return [], 0
    lines, problems = [], 0
    lst, _ = _decode(root, mod, HAIR_LIST)
    listed = {e.get('name') for e in lst.get('entries', [])}
    lu, _ = _decode(root, mod, HAIR_LINEUP)
    offered = {}
    for r in lu[_rows_key(lu)]:
        for it in r.get('items', []):
            offered.setdefault(it.get('itemId'), []).append(r.get('id'))
    ids = [r.get('id') for r in data[_rows_key(data)]]
    langs = sorted(d for d in os.listdir(os.path.join(root, 'ik_message', 'dat'))
                   if os.path.isfile(os.path.join(root, *(NAMES % d).split('/')) + '.dat'))
    texts = {}
    for lang in langs:
        try:
            texts[lang] = dict(zip(zf.parse_tbl(_read(root, mod, NAMES % lang + '.tbl')),
                                   zf.decode_msg(_read(root, mod, NAMES % lang + '.dat'))))
        except Exception:  # noqa: BLE001
            texts[lang] = {}
    for r in new:
        folder, hid = r.get('name'), r.get('id')
        nm = texts.get('English', {}).get(r.get('label'), '') or folder
        lines.append('%s  (hairstyle, id %s, %s)' % (nm, hid, folder))
        bad = []
        if hid in gids or ids.count(hid) > 1:
            bad.append('id %s is used by another hair / make-up entry' % hid)
        if not os.path.isdir(mod.path_for('ik_chara/model_pc/' + folder)):
            bad.append('model folder ik_chara/model_pc/%s is missing' % folder)
        if not os.path.isfile(mod.path_for('ik_chara/sav_pc/%s/%s.ccdata' % (folder, folder))):
            bad.append('ik_chara/sav_pc/%s/%s.ccdata is missing' % (folder, folder))
        if folder not in listed:
            bad.append('not in the hairstyle list (%s)' % HAIR_LIST)
        missing = [lang for lang in langs if not texts.get(lang, {}).get(r.get('label'))]
        if missing:
            bad.append('no name in %d language(s): %s' % (len(missing), ', '.join(missing)))
        for b in bad:
            lines.append('   ✗ ' + b)
        problems += len(bad)
        if not bad:
            lines.append('   ✓ complete: model, .ccdata, hairstyle list, name in %d languages%s' % (
                len(langs), ', offered by ' + ', '.join(offered[hid]) if hid in offered else ' (no salon offers it)'))
    return ['%d new hairstyle(s) checked: %s.' % (len(new), 'no problems' if not problems else '%d problem(s)' % problems),
            ''] + lines, problems


# ---------------------------------------------------------------------------------------------------------
# new hairstyles
# ---------------------------------------------------------------------------------------------------------

def _copy_tree(src_dir, mod, rel_dir, written):
    for f in sorted(os.listdir(src_dir)):
        p = os.path.join(src_dir, f)
        if os.path.isfile(p):
            mod.add_file('%s/%s' % (rel_dir, f), p)
            written.append('%s/%s' % (rel_dir, f))


def create_hairstyle(root, mod, src_folder, name, label='custom', salons=('hairsalon_normal_lineup',), log=None,
                     series=FIRST_SERIES):
    """A new hairstyle from an existing one (its model folder, e.g. p0_hrs0002_00_medium): model, hair motion
    and cutscene animations, .ccdata, the hairstyle list, a hair_make_data row, its name in every language,
    and the salons that offer it. Returns a report dict."""
    say = log or (lambda s: None)
    hair, h_schema = _decode(root, mod, HAIR_DATA)
    hk = _rows_key(hair)
    rows = hair[hk]
    src = next((r for r in rows if r.get('name') == src_folder), None)
    if src is None:
        raise ItemError('No hairstyle with the model folder %s.' % src_folder)
    folder = new_folder_name(root, mod, src_folder, label, series)
    written = []
    # model + animations
    src_dir = os.path.join(root, 'ik_chara', 'model_pc', src_folder)
    _copy_tree(src_dir, mod, 'ik_chara/model_pc/' + folder, written)
    for kind in ('motion_pc', 'demo_pc'):
        d = os.path.join(root, 'ik_chara', kind, src_folder)
        if os.path.isdir(d):
            _copy_tree(d, mod, 'ik_chara/%s/%s' % (kind, folder), written)
    say('copied the model and its animations: %d files' % len(written))
    # .ccdata
    cc, cc_schema = _decode(root, None, 'ik_chara/sav_pc/%s/%s.ccdata' % (src_folder, src_folder))
    for part in cc.get('parts', []):
        for k in ('model', 'material_set'):
            if part.get(k):
                part[k] = part[k].replace('/%s/' % src_folder, '/%s/' % folder)
    cc_rel = 'ik_chara/sav_pc/%s/%s.ccdata' % (folder, folder)
    _encode(mod, cc_rel, cc, cc_schema)
    written.append(cc_rel)
    # hairstyle list
    lst, l_schema = _decode(root, mod, HAIR_LIST)
    for e in [e for e in lst['entries'] if e.get('name', '').startswith(src_folder)]:
        lst['entries'].append({'name': e['name'].replace(src_folder, folder),
                               'ccdata': '../../sav_pc/%s/%s.ccdata' % (folder, folder), 'variant': e.get('variant', '')})
    _encode(mod, HAIR_LIST, lst, l_schema)
    written.append(HAIR_LIST)
    # hair_make_data row
    used = {r.get('id') for r in rows} | _used_by_mods(root, mod, HAIR_DATA, 'id')
    base = FIRST_HAIR_ID + 20 * (series_ranges(series)[0] - FIRST_SERIES)
    hid = next((i for i in range(base + 1, base + 20) if i not in used), None)
    if hid is None:
        raise ItemError('No free hairstyle id left for series %d - pick another series in Preferences > Mods.' % series)
    order = max([r.get('value6', 0) for r in rows if str(r.get('name', '')).startswith('p0_hrs')] +
                [x for x in _used_by_mods(root, mod, HAIR_DATA, 'value6') if isinstance(x, int) and x < 1000] + [0]) + 1
    name_label = 'dressup_item_hair_lt_%s' % folder.split('_', 1)[1]
    row = dict(src)
    row.update({'id': hid, 'name': folder, 'label': name_label, 'value6': order})
    rows.append(row)
    _encode(mod, HAIR_DATA, hair, h_schema)
    written.append(HAIR_DATA)
    # name
    langs = sorted(d for d in os.listdir(os.path.join(root, 'ik_message', 'dat'))
                   if os.path.isfile(os.path.join(root, *(NAMES % d).split('/')) + '.dat'))
    for lang in langs:
        rel = NAMES % lang
        dat, tbl = add_name(_read(root, mod, rel + '.dat'), _read(root, mod, rel + '.tbl'), name_label, name)
        mod.add_bytes(rel + '.dat', dat)
        mod.add_bytes(rel + '.tbl', tbl)
        written += [rel + '.dat', rel + '.tbl']
    # salons
    if salons:
        lu, lu_schema = _decode(root, mod, HAIR_LINEUP)
        for r in lu[_rows_key(lu)]:
            if r.get('id') in salons:
                template = next((it for it in r.get('items', []) if it.get('itemId') == src.get('id')),
                                (r.get('items') or [{}])[0])
                it = json.loads(json.dumps(template))
                it['itemId'] = hid
                r.setdefault('items', []).append(it)
        _encode(mod, HAIR_LINEUP, lu, lu_schema)
        written.append(HAIR_LINEUP)
    say('new hairstyle "%s": id %d, folder %s' % (name, hid, folder))
    return {'folder': folder, 'id': hid, 'name_label': name_label, 'files': written, 'source': src_folder}


def hair_salons(root, mod=None):
    lu, _ = _decode(root, mod, HAIR_LINEUP)
    return [(r.get('id', ''), len(r.get('items', []))) for r in lu[_rows_key(lu)]]


def open_hair_dialog(app, row):
    import tkinter as tk
    from tkinter import messagebox, ttk
    src_folder = row.get('name', '')
    if not str(src_folder).startswith('p0_hrs'):
        messagebox.showinfo('New hairstyle', 'Pick a hairstyle row (its name is a model folder like p0_hrs0002_00_medium) '
                                             '- hair colours and make-up can\'t be copied this way.')
        return
    root = app.root_var.get()
    mod = app.current_mod()
    if not mod:
        return
    ui = getattr(app, 'data_ui', None)
    if ui is not None and ui.dirty and ui.rel in (HAIR_DATA, HAIR_LINEUP):
        if not messagebox.askyesno('New hairstyle', 'The Game data tab has unsaved changes in this table. Save them first?'):
            return
        ui.save()
    w = tk.Toplevel(app)
    w.title('New hairstyle')
    w.transient(app)
    w.resizable(False, False)
    bg = ttk.Style(w).lookup('TFrame', 'background') or None
    if bg:
        w.configure(background=bg)
    f = ttk.Frame(w, padding=12)
    f.pack(fill='both', expand=True)
    ttk.Label(f, text='New hairstyle', font=('Segoe UI', 11, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w')
    ttk.Label(f, wraplength=460, justify='left', text=(
        'Makes a new hairstyle from "%s": a copy of its model and hair animations in a new folder, a new id, and its '
        'name in every language - offered by the salons you tick. Nothing in the game is replaced.' % src_folder)).grid(
        row=1, column=0, columnspan=2, sticky='w', pady=(2, 8))
    name = tk.StringVar(value='New Hairstyle')
    label = tk.StringVar(value='custom')
    for r, (lab, var, wd) in enumerate((('Name in game:', name, 34), ('Folder label:', label, 16)), 2):
        ttk.Label(f, text=lab).grid(row=r, column=0, sticky='w', pady=2)
        ttk.Entry(f, textvariable=var, width=wd).grid(row=r, column=1, sticky='w', pady=2)
    ttk.Label(f, text='Salons:').grid(row=4, column=0, sticky='nw', pady=2)
    box = ttk.Frame(f)
    box.grid(row=4, column=1, sticky='w')
    picks = {}
    try:
        salons = [s for s in hair_salons(root, mod) if s[0].startswith('hairsalon')]
    except Exception:  # noqa: BLE001
        salons = []
    for sid, n in salons:
        picks[sid] = tk.BooleanVar(value=sid == 'hairsalon_normal_lineup')
        ttk.Checkbutton(box, text='%s  (%d)' % (sid, n), variable=picks[sid]).pack(anchor='w')
    ttk.Label(f, wraplength=460, justify='left', style='Dim.TLabel', text=SAVE_NOTE.replace('buying the item', 'choosing it')
              ).grid(row=5, column=0, columnspan=2, sticky='w', pady=(10, 6))
    bar = ttk.Frame(f)
    bar.grid(row=6, column=0, columnspan=2, sticky='e')

    def create():
        if not name.get().strip():
            messagebox.showerror('New hairstyle', 'Give the hairstyle a name.', parent=w)
            return
        nm, lb = name.get().strip(), label.get().strip() or 'custom'
        sal = tuple(k for k, v in picks.items() if v.get())
        series = app.settings.get('item_series', FIRST_SERIES)
        w.destroy()

        def job():
            mod.snapshot()
            r = create_hairstyle(root, mod, src_folder, nm, lb, sal, series=series)
            mod.snapshot('new hairstyle: %s' % nm)
            return r

        def done(r):
            app.mod_changed(mod, '', 'new hairstyle "%s"' % nm)
            if ui is not None and ui.rel in (HAIR_DATA, HAIR_LINEUP) and ui.preset:
                ui.open_preset(ui.preset['name'])
            folder = mod.path_for('ik_chara/model_pc/' + r['folder'])
            mdl = next((x for x in sorted(os.listdir(folder)) if x.endswith('.trmdl') and '_lod' not in x), None)
            messagebox.showinfo('New hairstyle', '"%s" is in mod "%s":\n\nModel folder: ik_chara\\model_pc\\%s\n'
                                'Id: %d\n%d files written (with its hair animations).\n\nChange its look by live-editing '
                                'its textures (search for %s in Browse).' % (nm, mod.name, r['folder'], r['id'],
                                                                              len(r['files']), r['folder']))
            if mdl:
                if app.embed_pref():
                    app.nb.select(0)              # the 3D view lives in the Browse tab's preview pane
                app.open_viewer(os.path.join(folder, mdl), embed=app.embed_pref())
        app.run_bg('Making the new hairstyle', job, done)
    ttk.Button(bar, text='Create in current mod', command=create).pack(side='right')
    ttk.Button(bar, text='Cancel', command=w.destroy).pack(side='right', padx=6)
    w.bind('<Escape>', lambda e: w.destroy())
    return w
