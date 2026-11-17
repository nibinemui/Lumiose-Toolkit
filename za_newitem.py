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
NAMES = 'ik_message/dat/%s/common/dressup_item_name'
FIRST_SERIES = 60          # new item ids use series 60+ (the game uses 00-31)
FIRST_NUMBER = 7000        # new model folders: <prefix>_<cat>7000_00_<label> and up (the game uses 0000-1xxx)


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


def _all_folders(root, mod):
    out = set(os.listdir(os.path.join(root, 'ik_chara', 'model_pc')))
    if mod is not None and os.path.isdir(mod.path_for('ik_chara/model_pc')):
        out |= set(os.listdir(mod.path_for('ik_chara/model_pc')))
    return {f.lower() for f in out}


def new_folder_name(root, mod, src_folder, label):
    """<prefix>_<cat>NNNN_00_<label> with the first free NNNN from FIRST_NUMBER."""
    m = re.match(r'^(p\d)_([a-z]+)\d{4}_\d\d_', src_folder)
    if not m:
        raise ItemError('Unexpected model folder name: ' + src_folder)
    label = re.sub(r'[^a-z0-9]+', '', label.lower())[:20] or 'custom'
    taken = _all_folders(root, mod)
    for n in range(FIRST_NUMBER, 10000):
        name = '%s_%s%04d_00_%s' % (m.group(1), m.group(2), n, label)
        if not any(t.startswith('%s_%s%04d_' % (m.group(1), m.group(2), n)) for t in taken):
            return name
    raise ItemError('No free model folder number left.')


def new_ids(src_ids, used, count):
    """ids for `count` variants: the source's category digits + a free series/item + variant 01..count."""
    prefix = str(src_ids[0])[:-6]
    for series in range(FIRST_SERIES, 100):
        for item in range(1, 100):
            ids = [int('%s%02d%02d%02d' % (prefix, series, item, v + 1)) for v in range(count)]
            if not any(i in used for i in ids):
                return ids
    raise ItemError('No free item ids left in this category.')


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


def create_item(root, mod, src_type, name, price=None, shop=None, label='custom', log=None):
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
    folder = new_folder_name(root, mod, src_folder, label)
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
    used = {r.get('id') for r in rows}
    ids = new_ids([r['id'] for r in src_rows], used, len(src_rows))
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
    groups[gk].append({'type': new_type, 'group': max((g.get('group', 0) for g in groups[gk]), default=0) + 1,
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
    ttk.Label(f, text='(dressup_02_select5_lineup is Bundle Up in Rouge District)', style='Dim.TLabel').grid(
        row=6, column=1, sticky='w')
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
            r = create_item(root, mod, src_type, nm, pr, sh, lb)
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
