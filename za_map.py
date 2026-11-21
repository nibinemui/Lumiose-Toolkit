"""Map viewer / editor tab for Lumiose Toolkit.

Shows the Lumiose City map (ui/data/map/map_field_00.arc) with every spawner placement table
(world/ik_data/field/spawner_transform_data/**) drawn on top. Points can be inspected, moved,
duplicated or deleted; edited tables are saved into the current mod as game binaries via flatc.

World -> map calibration was fitted from the spawn data (points vs. walkable map pixels):
the map is centred on world (-496, -502), spans 2 x 517 m and is flipped on both axes.
"""
import copy
import glob
import json
import math
import os
import sys
import re
import threading
import traceback
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageDraw, ImageTk

import za_formats as zf
import za_modding as zm

MAP_CX, MAP_CZ, MAP_HALF = -496.0, -502.0, 517.0
CITY_RADIUS = 505.0

LAYER_STYLE = {   # file stem -> (label, colour)
    'pokemon_spawner_transform_array': ('Pokémon spawns', (40, 140, 255)),
    'itemball_spawner_transform_array': ('Item balls', (235, 50, 50)),
    'random_pop_item_spawner_transform_array': ('Random items', (255, 140, 0)),
    'placement_npc_spawner_transform_array': ('NPCs', (150, 80, 220)),
    'traffic_npc_spawner_transform_array': ('Walking NPCs', (200, 170, 0)),
    'royale_card_spawner_transform_array': ('Royale cards', (0, 170, 140)),
    'wazagimmick_spawner_transform_array': ('Move gimmicks', (120, 120, 120)),
    'medal_spawner_transform_array': ('Medals', (220, 0, 160)),
    'hyperspace_gates': ('Hyperspace portals', (190, 60, 255)),
}
DEFAULT_ON = {'pokemon_spawner_transform_array', 'itemball_spawner_transform_array'}


APP_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))   # the .exe's folder when frozen
CAL_FILE = os.path.join(APP_DIR, 'map_calibration.json')
CITY = 'Lumiose City'
# points outside the city belong to interiors / dungeons / zones that all reuse coordinates near the
# world origin; they are told apart by the area code in their names
_AREA_PATTERNS = (r'(zd[am]\d{3})', r'(?:^|_)(d\d\d)(?=_)', r't\d_(i\d{3}[a-z]?(?:_\d(?=_|$))?)',
                  r'(?:^|_)(t\d_(?:sp)?\d\d)(?=_|$)', r'(?:^|_)(lab)(?=_|$)', r'(?:^|_)(A\d\d_z\d\d|R\d\d)(?=_)',
                  r'(?:^|_)(t\d)(?=_|$)')
# background images for areas: label -> (ui/data folder, archive, texture name prefix)
AREA_BACKGROUNDS = [
    ('(grid only)', None),
    ('Lab B1 (map_dungeon_a_00)', ('map_dungeon', 'map_dungeon_a_00.arc', 'map_dungeon_a_00')),
    ('Lab B2 (map_dungeon_a_01)', ('map_dungeon', 'map_dungeon_a_00.arc', 'map_dungeon_a_01')),
    ('Lab B3 (map_dungeon_a_02)', ('map_dungeon', 'map_dungeon_a_00.arc', 'map_dungeon_a_02')),
    ('Sewers 1 (map_dungeon_b_00)', ('map_dungeon', 'map_dungeon_b_00.arc', 'map_dungeon_b_00')),
    ('Sewers 2 (map_dungeon_b_01)', ('map_dungeon', 'map_dungeon_b_00.arc', 'map_dungeon_b_01')),
    ('Dungeon C (map_dungeon_c_00)', ('map_dungeon', 'map_dungeon_c_00.arc', 'map_dungeon_c_00')),
    ('Field map 2 (map_texture_01)', ('map', 'map_field_01.arc', 'map_texture_01')),
    ('Rift map (map_field_dim_00)', ('map', 'map_field_dim_00.arc', 'map_texture_00')),
]


def area_of(name):
    for pat in _AREA_PATTERNS:
        m = re.search(pat, name)
        if m:
            return m.group(1)
    return 'other'


def city_cal(size):
    sc = size / (2 * MAP_HALF)
    return {'swap': False, 'sx': -1, 'sz': -1, 'scale': sc, 'ox': size / 2 + MAP_CX * sc, 'oy': size / 2 + MAP_CZ * sc}


def _spots(info):
    """[(scene file, position offset)] of a new point: one, or several (a copy in every scene the original is in)."""
    return info if isinstance(info, list) else [info]


def w2m(cal, x, z):
    a, b = (z, x) if cal['swap'] else (x, z)
    return cal['ox'] + cal['sx'] * cal['scale'] * a, cal['oy'] + cal['sz'] * cal['scale'] * b


def m2w(cal, px, py):
    a = (px - cal['ox']) / (cal['sx'] * cal['scale'])
    b = (py - cal['oy']) / (cal['sz'] * cal['scale'])
    return (b, a) if cal['swap'] else (a, b)


def fit_cal(points, size, box=None):
    """Calibration that puts the points' bounding box into `box` (map px) - a starting point."""
    xs = [p[0] for p in points] or [0.0]
    zs = [p[1] for p in points] or [0.0]
    x0, y0, x1, y1 = box or (size * 0.1, size * 0.1, size * 0.9, size * 0.9)
    sc = min((x1 - x0) / max(max(xs) - min(xs), 10.0), (y1 - y0) / max(max(zs) - min(zs), 10.0))
    cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
    return {'swap': False, 'sx': -1, 'sz': -1, 'scale': sc, 'ox': (x0 + x1) / 2 + cx * sc,
            'oy': (y0 + y1) / 2 + cz * sc}


def world_to_map(x, z, size):
    u = 1 - ((x - MAP_CX) / (2 * MAP_HALF) + 0.5)
    v = 1 - ((z - MAP_CZ) / (2 * MAP_HALF) + 0.5)
    return u * size, v * size


def map_to_world(px, py, size):
    u, v = px / size, py / size
    return (1 - u - 0.5) * 2 * MAP_HALF + MAP_CX, (1 - v - 0.5) * 2 * MAP_HALF + MAP_CZ


def _mod_file(path, root, romfs):
    """(file to read, game-relative path): the current mod's copy wins over the game file."""
    rel = os.path.relpath(path, root).replace(os.sep, '/')
    if romfs:
        p = os.path.join(romfs, *rel.split('/'))
        if os.path.isfile(p):
            return p, rel
    return path, rel


class DataTable:
    """A FlatBuffer data table loaded as JSON for editing (spawner / encounter / item data)."""

    def __init__(self, path, root, romfs=None):
        src, self.rel = _mod_file(path, root, romfs)
        self.src, self.stamp = src, zm.file_stamp(src)
        self.path = path
        self.from_mod = src != path
        self.data = json.loads(zm.to_json(src, zm.schema_for(path)))
        self.dirty = False
        self.label = os.path.splitext(os.path.basename(path))[0]


class Layer:
    def __init__(self, path, root, romfs=None):
        src, self.rel = _mod_file(path, root, romfs)
        self.src, self.stamp = src, zm.file_stamp(src)
        self.path = path
        self.stem = os.path.splitext(os.path.basename(path))[0]
        label, colour = LAYER_STYLE.get(self.stem, (self.stem.replace('_spawner_transform_array', '').replace('_', ' ').title(),
                                                    (90, 90, 90)))
        self.label, self.colour = label, colour
        self.data = json.loads(zm.to_json(src, zm.schema_for(path)))
        self.dirty = False
        self.var = None
        # the game's own points, when the mod has its own copy of the table: the others were added by the mod
        self.base = None
        if os.path.normcase(src) != os.path.normcase(path):
            try:
                game = json.loads(zm.to_json(path, zm.schema_for(path)))
                self.base = {s.get('Name') for g in game.get('values', []) for s in g.get('root', [])}
            except Exception:  # noqa: BLE001
                self.base = None

    def added(self, s):
        """True for a point the mod added (not in the game's own table)."""
        return self.base is not None and s.get('Name') not in self.base

    def points(self):
        for gi, g in enumerate(self.data.get('values', [])):
            for si, s in enumerate(g.get('root', [])):
                yield gi, si, s


# Hyperspace portals ("gates"): not in a table but in the districts' scene files - each entry is a point with its
# position (data offset 80) and a Rotation property sheet whose yaw (degrees) is at offset 96
GATE_SCENES = 'world/ik_scene/field/area/t1/sub_scene/t1_gimmick/t1_dim_gate_spawner'
GATE_POS, GATE_YAW = 80, 96
SHOW_PORTALS = False        # the portal layer waits until new portals are seen working in game
_GATE_NAME = re.compile(rb'(?:[a-z0-9]+_gate_[A-Za-z0-9_]+|pos_t\d+_Ev_d\d+_\d+)(?=\0)')


def gate_name(chunk):
    found = _GATE_NAME.findall(bytes(chunk.get('Data', [])))
    return found[-1].decode() if found else None


def gate_yaw_sheet(chunk):
    return next((k for k in chunk.get('Children') or [] if b'Rotation' in bytes(k.get('Data', []))
                 and len(k.get('Data', [])) >= GATE_YAW + 4), None)


class PortalLayer:
    """The Hyperspace portal spots of the city, read from (and saved into) the districts' scene files."""
    is_scene = True
    stem = 'hyperspace_gates'

    def __init__(self, root, romfs=None):
        import struct
        self.label, self.colour = 'Hyperspace portals', (190, 60, 255)
        self.rel, self.path, self.src, self.stamp = GATE_SCENES, None, None, None
        self.dirty, self.var = False, None
        self.files = []                             # (game path, file read, its stamp) - to notice changes
        game_names, any_mod = set(), False          # the game's own portal names (used if a mod changes a scene)
        pts = []
        for p in sorted(glob.glob(os.path.join(root, *GATE_SCENES.split('/'), '*', '*.trscn'))):
            rel = os.path.relpath(p, root).replace(os.sep, '/')
            src = _mod_file(p, root, romfs)[0]
            self.files.append((p, src, zm.file_stamp(src)))
            d = json.loads(zm.to_json(src, zm.schema_for(p)))
            modded = os.path.normcase(src) != os.path.normcase(p)
            game = json.loads(zm.to_json(p, zm.schema_for(p))) if modded else d
            game_names |= {gate_name(c) for c in game['Chunks']}
            any_mod = any_mod or modded
            for c in d['Chunks']:
                name = gate_name(c)
                b = bytes(c.get('Data', []))
                if not name or len(b) < GATE_POS + 12:
                    continue
                x, y, z = struct.unpack_from('<3f', b, GATE_POS)
                sheet = gate_yaw_sheet(c)
                yaw = struct.unpack_from('<f', bytes(sheet['Data']), GATE_YAW)[0] if sheet else 0.0
                pts.append({'Name': name, 'Position': {'X': x, 'Y': y, 'Z': z}, 'Rotation': {'Y': yaw},
                            '_scene': rel})
        if not pts:
            raise ValueError('no Hyperspace portal scene files')
        self.base = game_names if any_mod else None
        self.data = {'values': [{'root': pts}]}

    def added(self, s):
        return self.base is not None and s.get('Name') not in self.base

    def points(self):
        for si, s in enumerate(self.data['values'][0]['root']):
            yield 0, si, s

    def write_into(self, mt, dirty):
        """Positions and facing of every portal into its scene file (as loaded by the Map tab)."""
        import struct
        for s in self.data['values'][0]['root']:
            rel = s.get('_scene')
            if not rel:
                continue
            for c in mt.scene(rel)[0]['Chunks']:
                if gate_name(c) != s['Name']:
                    continue
                b = bytearray(c['Data'])
                x, y, z = _pos(s)
                new = struct.pack('<3f', x, y, z)
                if bytes(b[GATE_POS:GATE_POS + 12]) != new:
                    b[GATE_POS:GATE_POS + 12] = new
                    c['Data'] = list(b)
                    dirty.add(rel)
                sheet = gate_yaw_sheet(c)
                if sheet:
                    kb = bytearray(sheet['Data'])
                    yaw = struct.pack('<f', _roty(s))
                    if bytes(kb[GATE_YAW:GATE_YAW + 4]) != yaw:
                        kb[GATE_YAW:GATE_YAW + 4] = yaw
                        sheet['Data'] = list(kb)
                        dirty.add(rel)
                break


def _pos(s):
    p = s.get('Position') or {}
    return p.get('X', 0.0), p.get('Y', 0.0), p.get('Z', 0.0)


def _set_pos(s, x, y, z):
    s['Position'] = {'X': float(x), 'Y': float(y), 'Z': float(z)}


def _roty(s):
    return (s.get('Rotation') or {}).get('Y', 0.0)


class MapTab:
    def __init__(self, app, parent):
        self.app = app
        self.parent = parent
        self.layers = []
        self.base = None            # full-resolution map (PIL RGB)
        self.size = 4096
        self.zoom = None            # screen px per map px
        self.ox = self.oy = 0.0     # map px at the canvas top-left
        self.sel = None             # (layer, gi, si)
        self.drag = None
        self.photo = None
        self.visible = []           # (sx, sy, layer, gi, si) drawn this frame
        self.species = {}
        self.spawn_info = {}
        self.tables = {}
        self.enc_by_id, self.spawner_by_obj, self.enc_users = {}, {}, {}
        self.item_spawner_by_obj, self.item_table_by_id, self.item_table_users, self.item_names = {}, {}, {}, {}
        self.romfs = None
        self.area = CITY
        self.cal = None
        self.city_base = None
        self.point_area = {}        # point name -> area code (points outside the city)
        self.area_counts = {}
        self.bg_cache = {}
        self.area_labels = {CITY: CITY}
        self.species_names = []
        self.loaded = False
        self._redraw_after = None
        self.build()

    # ---------------- UI ----------------
    def build(self):
        P = self.parent
        paned = ttk.PanedWindow(P, orient='horizontal')
        paned.pack(fill='both', expand=True, padx=4, pady=4)
        left = ttk.Frame(paned)
        paned.add(left, weight=4)
        self.canvas = tk.Canvas(left, highlightthickness=0, background='#181818')
        self.canvas.pack(fill='both', expand=True)
        self.app.map_canvas = self.canvas
        self.coord_var = tk.StringVar(value='')
        ttk.Label(left, textvariable=self.coord_var, style='Dim.TLabel').pack(fill='x')
        c = self.canvas
        c.bind('<Configure>', lambda e: self.redraw_soon())
        c.bind('<MouseWheel>', self.on_wheel)
        c.bind('<ButtonPress-1>', self.on_press)
        c.bind('<B1-Motion>', self.on_drag)
        c.bind('<ButtonRelease-1>', self.on_release)
        c.bind('<Motion>', self.on_motion)
        self.placeholder = c.create_text(20, 20, anchor='nw', fill='#9a9aa3', font=('Segoe UI', 11),
                                         text='Open this tab to load the map…')

        side = ttk.Frame(paned)
        paned.add(side, weight=1)
        ttk.Label(side, text='Layers', style='Head.TLabel').pack(anchor='w')
        self.layer_box = ttk.Frame(side)
        self.layer_box.pack(fill='x', pady=(2, 6))
        sf = ttk.Frame(side)
        sf.pack(fill='x')
        ttk.Label(sf, text='Find:').pack(side='left')
        self.find_var = tk.StringVar()
        e = ttk.Entry(sf, textvariable=self.find_var)
        e.pack(side='left', fill='x', expand=True, padx=3)
        e.bind('<Return>', lambda ev: self.find_next())
        ttk.Button(sf, text='Next', command=self.find_next).pack(side='left')
        af = ttk.Frame(side)
        af.pack(fill='x', pady=(6, 0))
        ttk.Label(af, text='Area:').grid(row=0, column=0, sticky='w')
        self.area_var = tk.StringVar(value=CITY)
        self.area_box = ttk.Combobox(af, textvariable=self.area_var, state='readonly', values=[CITY])
        self.area_box.grid(row=0, column=1, sticky='ew', padx=3)
        self.area_box.bind('<<ComboboxSelected>>', lambda e: self.set_area(self.area_var.get()))
        ttk.Label(af, text='Background:').grid(row=1, column=0, sticky='w')
        self.bg_var = tk.StringVar(value='')
        self.bg_box = ttk.Combobox(af, textvariable=self.bg_var, state='disabled',
                                   values=[b[0] for b in AREA_BACKGROUNDS])
        self.bg_box.grid(row=1, column=1, sticky='ew', padx=3, pady=2)
        self.bg_box.bind('<<ComboboxSelected>>', lambda e: self.set_background(self.bg_var.get()))
        af.columnconfigure(1, weight=1)
        self.align_mode = tk.BooleanVar(value=False)
        al = ttk.Frame(side)
        al.pack(fill='x')
        self.align_chk = ttk.Checkbutton(al, text='Align: drag / wheel moves & scales the points on the image',
                                         variable=self.align_mode, state='disabled')
        self.align_chk.pack(anchor='w')
        ab = ttk.Frame(side)
        ab.pack(fill='x')
        self.align_btns = []
        for text, cmd in (('Flip X', lambda: self.flip('sx')), ('Flip Z', lambda: self.flip('sz')),
                          ('Rotate 90°', self.rotate90), ('Save alignment', self.save_alignment)):
            b = ttk.Button(ab, text=text, command=cmd, state='disabled')
            b.pack(side='left', padx=(0, 3))
            self.align_btns.append(b)
        self.edit_mode = tk.BooleanVar(value=False)
        ttk.Checkbutton(side, text='Edit mode: drag the selected point to move it',
                        variable=self.edit_mode).pack(anchor='w')
        self.show_icons = tk.BooleanVar(value=False)
        ttk.Checkbutton(side, text='Show Pokémon icons on Pokémon spawn points (the most likely one)',
                        variable=self.show_icons, command=self.redraw).pack(anchor='w')
        self.icon_cache = {}
        hb = ttk.Frame(side)
        hb.pack(anchor='w', pady=(4, 0))
        ttk.Button(hb, text='Encounter overview…', command=self.encounter_overview).pack(side='left')
        ttk.Button(hb, text='Hyperspace Pokémon…', command=self.hyperspace_spawns).pack(side='left', padx=(6, 0))

        ttk.Label(side, text='Selected point', style='Head.TLabel').pack(anchor='w', pady=(10, 2))
        g = ttk.Frame(side)
        g.pack(fill='x')
        self.f = {}
        for r, (k, label) in enumerate((('Name', 'Name'), ('X', 'X'), ('Y', 'Y (height)'), ('Z', 'Z'), ('RY', 'Rotation Y°'))):
            ttk.Label(g, text=label + ':').grid(row=r, column=0, sticky='w')
            v = tk.StringVar()
            ttk.Entry(g, textvariable=v, width=28).grid(row=r, column=1, sticky='ew', pady=1)
            self.f[k] = v
        g.columnconfigure(1, weight=1)
        bf = ttk.Frame(side)
        bf.pack(fill='x', pady=4)
        ttk.Button(bf, text='Apply', command=self.apply_fields).pack(side='left')
        ttk.Button(bf, text='Duplicate', command=self.duplicate).pack(side='left', padx=3)
        ttk.Button(bf, text='Delete', command=self.delete).pack(side='left')
        ttk.Button(bf, text='Zoom to', command=self.zoom_to_sel).pack(side='left', padx=3)
        cf = self.create_frame = ttk.LabelFrame(side, text='Empty spot')
        self.create_var = tk.StringVar()
        ttk.Label(cf, textvariable=self.create_var, style='Dim.TLabel').pack(anchor='w', padx=4)
        ttk.Button(cf, text='Create Spawn Point…', command=self.create_spawn_point).pack(anchor='w', padx=4, pady=4)
        self.empty_spot = None
        ef = self.enc_frame = ttk.LabelFrame(side, text='Encounters at this spawn (Pokémon spawns)')
        self.enc_tree = ttk.Treeview(ef, columns=('lv', 'w', 'shared'), height=4, selectmode='browse')
        for col, txt, w in (('#0', 'Pokémon', 150), ('lv', 'Levels', 60), ('w', 'Weight', 50), ('shared', 'Used by', 60)):
            self.enc_tree.heading(col, text=txt)
            self.enc_tree.column(col, width=w, stretch=(col == '#0'))
        self.enc_tree.pack(fill='x')
        self.enc_tree.bind('<<TreeviewSelect>>', lambda e: self.enc_selected())
        er = ttk.Frame(ef)
        er.pack(fill='x', pady=2)
        self.enc_species = ttk.Combobox(er, width=22)
        self.enc_species.pack(side='left')
        self.enc_species.bind('<KeyRelease>', self._filter_species)
        self.enc_min, self.enc_max, self.enc_w = tk.StringVar(), tk.StringVar(), tk.StringVar()
        for lab, var in (('Lv', self.enc_min), ('-', self.enc_max), ('W', self.enc_w)):
            ttk.Label(er, text=lab).pack(side='left', padx=(4, 1))
            ttk.Entry(er, textvariable=var, width=4).pack(side='left')
        eb = ttk.Frame(ef)
        eb.pack(fill='x', pady=(0, 2))
        ttk.Button(eb, text='Apply', command=self.enc_apply).pack(side='left')
        ttk.Button(eb, text='Add (copy)', command=self.enc_add).pack(side='left', padx=3)
        ttk.Button(eb, text='Remove', command=self.enc_remove).pack(side='left')
        ttk.Button(eb, text='Add spawn point…', command=self.add_spawn_point).pack(side='right')
        itf = self.item_frame = ttk.LabelFrame(side, text='Contents (item balls / random items)')
        self.item_tree = ttk.Treeview(itf, columns=('n', 'w', 'shared'), height=3, selectmode='browse')
        for col, txt, w in (('#0', 'Item', 150), ('n', 'Count', 50), ('w', 'Weight', 50), ('shared', 'Used by', 60)):
            self.item_tree.heading(col, text=txt)
            self.item_tree.column(col, width=w, stretch=(col == '#0'))
        self.item_tree.pack(fill='x')
        self.item_tree.bind('<<TreeviewSelect>>', lambda e: self.item_selected())
        ir = ttk.Frame(itf)
        ir.pack(fill='x', pady=2)
        self.item_box = ttk.Combobox(ir, width=24, postcommand=lambda: self._filter_items())
        self.item_box.pack(side='left')
        self.item_box.bind('<KeyRelease>', self._filter_items)
        self.item_count, self.item_weight = tk.StringVar(), tk.StringVar()
        for lab, var in (('x', self.item_count), ('W', self.item_weight)):
            ttk.Label(ir, text=lab).pack(side='left', padx=(4, 1))
            ttk.Entry(ir, textvariable=var, width=4).pack(side='left')
        ib = ttk.Frame(itf)
        ib.pack(fill='x', pady=(0, 2))
        ttk.Button(ib, text='Apply', command=self.item_apply).pack(side='left')
        ttk.Button(ib, text='Add (copy)', command=self.item_add).pack(side='left', padx=3)
        ttk.Button(ib, text='Remove', command=self.item_remove).pack(side='left')
        ttk.Button(ib, text='Make unique', command=self.item_unique).pack(side='left', padx=3)
        self.app.map_info = self.info = tk.Text(side, height=8, wrap='word', borderwidth=0, font=('Consolas', 9))
        self.info.pack(fill='both', expand=True, pady=(4, 4))
        sv = ttk.Frame(side)
        sv.pack(fill='x')
        self.dirty_var = tk.StringVar(value='No unsaved changes')
        ttk.Label(sv, textvariable=self.dirty_var, style='Dim.TLabel').pack(side='left')
        ttk.Button(sv, text='Save changes to current mod', command=self.save_to_mod).pack(side='right')
        self.app.nb.bind('<<NotebookTabChanged>>', self.on_tab, add='+')

    def on_tab(self, event=None):
        if self.app.nb.select() == str(self.parent) and not self.loaded:
            self.loaded = True
            self.load()

    def reload_if_clean(self):
        """The current mod changed or a tool wrote into it: read the tables again unless something is unsaved."""
        if not self.loaded or not self.layers:
            return
        if any(x.dirty for x in self.layers) or any(t.dirty for t in self.tables.values()) or \
                self.__dict__.get('scene_dirty'):
            return                                   # (points created earlier and saved don't hold it back)
        m = self.app.current_mod(ask=False)
        romfs = m.romfs if m else None
        root = os.path.normpath(self.app.root_var.get())
        files = [(o.path, o.src, o.stamp) for o in list(self.layers) + list(self.tables.values()) if o.path]
        for L in self.layers:
            files += getattr(L, 'files', [])         # the portal layer's scene files
        if all(os.path.normcase(_mod_file(p, root, romfs)[0]) == os.path.normcase(src) and zm.file_stamp(src) == st
               for p, src, st in files):
            return
        if self.app.nb.select() == str(self.parent):
            self.load()
        else:
            self.loaded = False

    # ---------------- loading ----------------
    def load(self):
        root = os.path.normpath(self.app.root_var.get())
        m = self.app.current_mod(ask=False)
        romfs = self.romfs = m.romfs if m else None
        self.canvas.itemconfigure(self.placeholder, text='Loading the map and placement tables…')

        def work():
            try:
                arc = dict(zf.parse_sarc(open(os.path.join(root, 'ui', 'data', 'map', 'map_field_00.arc'), 'rb').read()))
                b = next(v for v in arc.values() if v[:4] == b'BNTX')
                t = next(t for t in zf.Bntx(b).textures if t.name.startswith('map_texture'))
                base = t.decode().convert('RGB')
                layers = []
                for p in sorted(glob.glob(os.path.join(root, 'world', 'ik_data', 'field', 'spawner_transform_data',
                                                       '**', '*_transform_array.bin'), recursive=True)):
                    try:
                        layers.append(Layer(p, root, romfs))
                    except Exception:  # noqa: BLE001
                        pass
                if SHOW_PORTALS:
                    try:
                        layers.append(PortalLayer(root, romfs))
                    except Exception:  # noqa: BLE001
                        pass
                species, tables = self.load_spawn_info(root, romfs)
                self.app.q.put(lambda: self.loaded_ok(base, layers, species, tables))
            except Exception as e:  # noqa: BLE001
                text = 'Could not load the map:\n%s\n\nOpen another tab and come back to try again.\n\n%s' % (
                    e, traceback.format_exc()[-600:])

                def failed():
                    self.loaded = False           # opening the tab again retries
                    self.canvas.itemconfigure(self.placeholder, text=text)
                self.app.q.put(failed)
        threading.Thread(target=work, daemon=True).start()

    def load_spawn_info(self, root, romfs=None):
        """Species/item names + the spawner, encounter and item tables (kept for editing)."""
        names = {}
        msg = os.path.join(root, 'ik_message', 'dat', 'English', 'common', 'monsname')
        try:
            labels = zf.parse_tbl(open(msg + '.tbl', 'rb').read())
            for lab, txt in zip(labels, zf.decode_msg(open(msg + '.dat', 'rb').read())):
                if lab.startswith('MONSNAME_'):
                    names[int(lab[9:])] = txt
        except (OSError, ValueError):
            pass
        self.item_names = {}
        try:
            self.item_names = dict(enumerate(zf.decode_msg(open(os.path.join(
                root, 'ik_message', 'dat', 'English', 'common', 'itemname.dat'), 'rb').read())))
        except (OSError, ValueError):
            pass
        tables = {}
        for key, stem in (('enc', 'encount_data_array'), ('spawner', 'pokemon_spawner_data_array'),
                          ('ib_spawner', 'item_ball_spawner_data_array'), ('ib_items', 'item_ball_item_data_array'),
                          ('rp_spawner', 'random_pop_item_spawner_data_array'),
                          ('rp_items', 'random_pop_item_table_data_array'),
                          ('wg_spawner', 'wazagimmick_spawner_data_array'),
                          ('npc_spawner', 'placement_npc_spawner_data_array'),
                          ('gates', 'dimension_gate_spawner_array'),
                          ('dim_wild', 'dimension_wild_pokemon_array'),
                          ('gate_counts', 'dimension_progress_array')):
            try:
                p = glob.glob(os.path.join(root, 'world', '**', stem + '.bin'), recursive=True)[0]
                tables[key] = DataTable(p, root, romfs)
            except (IndexError, OSError, ValueError, zm.ModError):
                pass
        return names, tables

    # ---------------- encounter helpers ----------------
    def index_spawn_tables(self):
        self.spawn_version = getattr(self, 'spawn_version', 0) + 1      # windows showing encounters read them again
        self.enc_by_id, self.spawner_by_obj, self.enc_users = {}, {}, {}
        enc, sp = self.tables.get('enc'), self.tables.get('spawner')
        if enc:
            for gi, g in enumerate(enc.data.get('values', [])):
                for si, e in enumerate(g.get('root', [])):
                    self.enc_by_id[e.get('id')] = e
        if sp:
            for gi, g in enumerate(sp.data.get('values', [])):
                for si, s in enumerate(g.get('root', [])):
                    for o in s.get('appearanceSpawnerObjectInfoList', []):
                        self.spawner_by_obj[o.get('objectName')] = s
                    for ei in s.get('encountDataInfoList', []):
                        self.enc_users[ei.get('encountDataId')] = self.enc_users.get(ei.get('encountDataId'), 0) + 1
        self.index_item_tables()

    def index_item_tables(self):
        """objectName -> item spawner, and table id -> item lottery table (item balls + random items)."""
        self.item_spawner_by_obj, self.item_table_by_id, self.item_table_users = {}, {}, {}
        for sp_key, it_key in (('ib_spawner', 'ib_items'), ('rp_spawner', 'rp_items')):
            sp, it = self.tables.get(sp_key), self.tables.get(it_key)
            if it:
                for g in it.data.get('values', []):
                    for e in g.get('root', []):
                        self.item_table_by_id[e.get('id')] = (e, it_key)
            if sp:
                for g in sp.data.get('values', []):
                    for s in g.get('root', []):
                        for o in s.get('appearanceSpawnerObjectInfoList', []):
                            self.item_spawner_by_obj[o.get('objectName')] = s
                        for ti in s.get('tableInfoList', []):
                            k = ti.get('tableId')
                            self.item_table_users[k] = self.item_table_users.get(k, 0) + 1

    def item_name(self, item_id):
        try:
            return self.item_names.get(int(item_id), '#%s' % item_id)
        except (TypeError, ValueError):
            return str(item_id)

    def item_lines(self, name):
        s = self.item_spawner_by_obj.get(name)
        if not s:
            return []
        out = []
        many = len(s.get('tableInfoList', [])) > 1
        for ti in s.get('tableInfoList', []):
            e = self.item_table_by_id.get(ti.get('tableId'), ({}, None))[0]
            for it in e.get('itemLotteryDataList', []):
                out.append('%s x%s  (weight %s)%s' % (self.item_name(it.get('itemId')), it.get('maxCount', 1) or 1,
                                                       it.get('weight', 0), '  [%s]' % ti.get('tableId') if many else ''))
        return out

    def species_label(self, dev):
        return '%s (#%d)' % (self.species.get(dev, '?'), dev)

    def encounter_lines(self, name):
        s = self.spawner_by_obj.get(name)
        if not s:
            return []
        out = []
        for ei in s.get('encountDataInfoList', []):
            e = self.enc_by_id.get(ei.get('encountDataId'), {})
            lo, hi = e.get('minLevel', 0), e.get('maxLevel', 0)
            out.append('%s Lv.%d%s  (weight %s)' % (self.species.get(e.get('devNo', 0), '#%d' % e.get('devNo', 0)), lo,
                                                     '-%d' % hi if hi != lo else '', ei.get('weight', 0)))
        return out

    def loaded_ok(self, base, layers, species, tables):
        self.base, self.size = base, base.size[0]
        self.city_base, self.cal = base, city_cal(base.size[0])
        self.point_area, self.area_counts = {}, {}
        for L in layers:
            for gi, si, sp in L.points():
                x, y, z = _pos(sp)
                if math.hypot(x - MAP_CX, z - MAP_CZ) > CITY_RADIUS:
                    a = area_of(sp.get('Name', ''))
                    self.point_area[sp.get('Name', '')] = a
                    self.area_counts[a] = self.area_counts.get(a, 0) + 1
        self.area_labels = {CITY: CITY}
        for a, n in sorted(self.area_counts.items(), key=lambda t: -t[1]):
            self.area_labels['%s  (%d points)' % (a, n)] = a
        self.area_box.configure(values=list(self.area_labels))
        self.layers, self.species, self.tables = layers, species, tables
        for k in ('scene_of', 'scenes_of', 'scenes', 'scene_dirty', 'new_points', 'extra_files'):   # new points of the old tables
            self.__dict__.pop(k, None)
        self.icon_cache = {}                         # another mod may have its own (ported) Pokémon's icons
        self.index_spawn_tables()
        self.spawn_info = {n: self.encounter_lines(n) for n in self.spawner_by_obj}
        self.species_names = ['%s (#%d)' % (n, d) for d, n in sorted(species.items())]
        self.enc_species.configure(values=self.species_names)
        for w in self.layer_box.winfo_children():
            w.destroy()
        for L in layers:
            L.var = tk.BooleanVar(value=L.stem in DEFAULT_ON)
            n = sum(1 for _ in L.points())
            row = ttk.Frame(self.layer_box)
            row.pack(fill='x')
            sw = tk.Canvas(row, width=12, height=12, highlightthickness=0, background='#%02x%02x%02x' % L.colour)
            sw.pack(side='left', padx=(0, 4))
            ttk.Checkbutton(row, text='%s (%d)' % (L.label, n), variable=L.var, command=self.redraw).pack(side='left')
        self.canvas.delete(self.placeholder)
        self.fit()

    # ---------------- view ----------------
    def fit(self):
        cw, ch = max(50, self.canvas.winfo_width()), max(50, self.canvas.winfo_height())
        self.zoom = min(cw, ch) / self.size
        self.ox = -(cw / self.zoom - self.size) / 2
        self.oy = -(ch / self.zoom - self.size) / 2
        self.redraw()

    def redraw_soon(self):
        if self._redraw_after:
            self.canvas.after_cancel(self._redraw_after)
        self._redraw_after = self.canvas.after(60, self.redraw)

    def to_screen(self, mx, my):
        return (mx - self.ox) * self.zoom, (my - self.oy) * self.zoom

    def to_map(self, sx, sy):
        return sx / self.zoom + self.ox, sy / self.zoom + self.oy

    def redraw(self):
        self._redraw_after = None
        if self.base is None:
            return
        if self.zoom is None:
            return self.fit()
        cw, ch = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        x0, y0 = self.to_map(0, 0)
        x1, y1 = self.to_map(cw, ch)
        crop = self.base.crop((int(math.floor(x0)), int(math.floor(y0)), int(math.ceil(x1)), int(math.ceil(y1))))
        img = crop.resize((cw, ch), Image.BILINEAR if self.zoom < 1 else Image.NEAREST)
        d = ImageDraw.Draw(img)
        r = 3 if self.zoom < 0.4 else (4 if self.zoom < 1.5 else 6)
        self.visible = []
        icons = self.show_icons.get()
        isz = 22 if self.zoom < 1 else (32 if self.zoom < 3 else 44)
        new_here = self.__dict__.get('new_points', {})
        for L in self.layers:
            if not L.var or not L.var.get():
                continue
            mon = icons and L.stem == 'pokemon_spawner_transform_array'
            for gi, si, s in L.points():
                x, y, z = _pos(s)
                if not self.in_area(s.get('Name', ''), x, z):
                    continue
                sx, sy = self.to_screen(*w2m(self.cal, x, z))
                if -10 < sx < cw + 10 and -10 < sy < ch + 10:
                    # points a mod added (not the game's own) are black, ringed in their layer's colour
                    added = s.get('Name') in new_here or (hasattr(L, 'added') and L.added(s))
                    ic = self.spawn_icon(s.get('Name'), isz) if mon else None
                    if ic is not None:
                        if added:
                            h = isz / 2 + 2
                            d.ellipse((sx - h, sy - h, sx + h, sy + h), fill=(0, 0, 0), outline=L.colour, width=2)
                        img.paste(ic, (int(sx - isz / 2), int(sy - isz / 2)), ic)
                    elif added:
                        d.ellipse((sx - r - 1, sy - r - 1, sx + r + 1, sy + r + 1), fill=(0, 0, 0), outline=L.colour,
                                  width=2)
                    else:
                        d.ellipse((sx - r, sy - r, sx + r, sy + r), fill=L.colour, outline=(255, 255, 255))
                    self.visible.append((sx, sy, L, gi, si))
        if self.sel:
            L, gi, si = self.sel
            try:
                s = L.data['values'][gi]['root'][si]
                x, y, z = _pos(s)
                sx, sy = self.to_screen(*w2m(self.cal, x, z))
                d.ellipse((sx - r - 5, sy - r - 5, sx + r + 5, sy + r + 5), outline=(255, 255, 0), width=3)
                a = math.radians(_roty(s))
                d.line((sx, sy, sx - math.sin(a) * 18, sy - math.cos(a) * 18), fill=(255, 255, 0), width=2)
            except (IndexError, KeyError):
                self.sel = None
        if self.empty_spot is not None and not self.sel:          # the spot a new point would go
            try:
                sx, sy = self.to_screen(*w2m(self.cal, *self.empty_spot))
                d.line((sx - 9, sy, sx + 9, sy), fill=(255, 255, 0), width=2)
                d.line((sx, sy - 9, sx, sy + 9), fill=(255, 255, 0), width=2)
            except Exception:  # noqa: BLE001
                pass
        self.photo = ImageTk.PhotoImage(img)
        self.canvas.delete('map')
        self.canvas.create_image(0, 0, image=self.photo, anchor='nw', tags='map')

    # ---------------- mouse ----------------
    def on_wheel(self, e):
        if self.base is None:
            return
        f = 1.25 if e.delta > 0 else 0.8
        if self.align_mode.get() and self.area != CITY:
            mx, my = self.to_map(e.x, e.y)
            f = 1.05 if e.delta > 0 else 1 / 1.05
            c = self.cal
            c['scale'] *= f
            c['ox'], c['oy'] = mx - (mx - c['ox']) * f, my - (my - c['oy']) * f
            self.redraw()
            return
        mx, my = self.to_map(e.x, e.y)
        self.zoom = min(16.0, max(0.05, self.zoom * f))
        self.ox, self.oy = mx - e.x / self.zoom, my - e.y / self.zoom
        self.redraw()

    def hit(self, x, y):
        best, bd = None, 9.0
        for sx, sy, L, gi, si in self.visible:
            dd = math.hypot(sx - x, sy - y)
            if dd < bd:
                best, bd = (L, gi, si), dd
        return best

    def on_press(self, e):
        h = self.hit(e.x, e.y)
        if self.align_mode.get() and self.area != CITY:
            self.drag = ('align', e.x, e.y, self.cal['ox'], self.cal['oy'])
            return
        if h and self.edit_mode.get() and self.sel == h:
            self.drag = ('point', e.x, e.y)
        else:
            self.drag = ('pan', e.x, e.y, self.ox, self.oy)
            self._click = (e.x, e.y, h)

    def on_drag(self, e):
        if not self.drag:
            return
        if self.drag[0] == 'pan':
            _, x, y, ox, oy = self.drag
            self.ox, self.oy = ox - (e.x - x) / self.zoom, oy - (e.y - y) / self.zoom
            self.redraw()
        elif self.drag[0] == 'align':
            _, x, y, ox, oy = self.drag
            self.cal['ox'], self.cal['oy'] = ox + (e.x - x) / self.zoom, oy + (e.y - y) / self.zoom
            self.redraw()
        else:
            L, gi, si = self.sel
            s = L.data['values'][gi]['root'][si]
            mx, my = self.to_map(e.x, e.y)
            wx, wz = m2w(self.cal, mx, my)
            _set_pos(s, round(wx, 3), _pos(s)[1], round(wz, 3))
            self.mark_dirty(L)
            self.show_sel()
            self.redraw()

    def on_release(self, e):
        if self.drag and self.drag[0] == 'pan' and hasattr(self, '_click'):
            x, y, h = self._click
            if abs(e.x - x) < 4 and abs(e.y - y) < 4:
                self.sel = h
                # an empty spot: remembered for "Create Spawn Point"
                self.empty_spot = None if h else tuple(round(v, 2) for v in m2w(self.cal, *self.to_map(e.x, e.y)))
                self.show_sel()
                self.redraw()
        self.drag = None

    def on_motion(self, e):
        if self.base is None or self.zoom is None:
            return
        wx, wz = m2w(self.cal, *self.to_map(e.x, e.y))
        h = self.hit(e.x, e.y)
        tip = ''
        if h:
            nm = h[0].data['values'][h[1]]['root'][h[2]].get('Name', '')
            tip = '   ·   %s' % nm
            its = self.item_lines(nm)
            if its:
                tip += '   ·   ' + '; '.join(i.split('  (')[0] for i in its[:3]) + (' …' if len(its) > 3 else '')
        self.coord_var.set('World X %.1f  Z %.1f   (zoom %.0f%%)%s' % (wx, wz, self.zoom * 100, tip))

    # ---------------- selection / editing ----------------
    def show_sel(self):
        self.info.delete('1.0', 'end')
        if not self.sel:
            for v in self.f.values():
                v.set('')
            self.show_editors()
            return
        L, gi, si = self.sel
        s = L.data['values'][gi]['root'][si]
        x, y, z = _pos(s)
        self.f['Name'].set(s.get('Name', ''))
        self.f['X'].set('%.3f' % x)
        self.f['Y'].set('%.3f' % y)
        self.f['Z'].set('%.3f' % z)
        self.f['RY'].set('%.1f' % _roty(s))
        lines = ['Layer : %s' % L.label, 'Table : %s' % L.rel, 'Group %d, entry %d' % (gi, si)]
        if s.get('Name') in self.__dict__.get('new_points', {}) or (hasattr(L, 'added') and L.added(s)):
            lines.append('Added by a mod (not in the game) - drawn in black')
        lines.append('')
        mons = self.encounter_lines(s.get('Name'))
        if mons:
            lines += ['Spawns here:'] + ['  ' + m for m in mons] + ['']
        items = self.item_lines(s.get('Name'))
        if items:
            lines += ['Contains (one is picked by weight):' if len(items) > 1 else 'Contains:'] + \
                ['  ' + m for m in items] + ['']
        lines.append(json.dumps(s, indent=1))
        self.info.insert('1.0', '\n'.join(lines))
        self.fill_encounters()
        self.fill_items()
        self.show_editors()

    def show_editors(self):
        """Only show the encounter / item-contents editor that applies to the selected point."""
        name = None
        if self.sel:
            L, gi, si = self.sel
            name = L.data['values'][gi]['root'][si].get('Name')
        if self.empty_spot is not None and not self.sel:
            self.create_var.set('X %.1f, Z %.1f - nothing placed here yet' % self.empty_spot)
        for frame, on in ((self.create_frame, self.empty_spot is not None and not self.sel),
                          (self.enc_frame, name in self.spawner_by_obj),
                          (self.item_frame, name in self.item_spawner_by_obj)):
            if on and not frame.winfo_ismapped():
                frame.pack(fill='x', pady=(6, 2), before=self.info)
            elif not on and frame.winfo_ismapped():
                frame.pack_forget()

    # ---------------- item ball / random item contents ----------------
    def cur_item_table(self):
        """(spawner, [(table info, table entry, table key)]) for the selected item point."""
        if not self.sel:
            return None, []
        L, gi, si = self.sel
        s = self.item_spawner_by_obj.get(L.data['values'][gi]['root'][si].get('Name'))
        if not s:
            return None, []
        out = []
        for ti in s.get('tableInfoList', []):
            e, key = self.item_table_by_id.get(ti.get('tableId'), (None, None))
            if e is not None:
                out.append((ti, e, key))
        return s, out

    def fill_items(self):
        self.item_tree.delete(*self.item_tree.get_children())
        s, tabs = self.cur_item_table()
        for t, (ti, e, key) in enumerate(tabs):
            for k, it in enumerate(e.get('itemLotteryDataList', [])):
                self.item_tree.insert('', 'end', '%d:%d' % (t, k), text=self.item_name(it.get('itemId')),
                                      values=(it.get('maxCount', 1) or 1, it.get('weight', 0),
                                              '%d point(s)' % self.item_table_users.get(ti.get('tableId'), 0)))

    def _item_sel(self):
        """(table info, table entry, table key, item index) of the selected item, or Nones."""
        s, tabs = self.cur_item_table()
        sel = self.item_tree.selection()
        if not tabs or not sel:
            return None, None, None, None
        t, k = (int(x) for x in sel[0].split(':'))
        ti, e, key = tabs[t]
        return ti, e, key, k

    def item_selected(self):
        ti, e, key, k = self._item_sel()
        if ti is None:
            return
        it = e['itemLotteryDataList'][k]
        self.item_box.set('%s %s' % (it.get('itemId'), self.item_name(it.get('itemId'))))
        self.item_count.set(it.get('maxCount', 1) or 1)
        self.item_weight.set(it.get('weight', 0))

    def _filter_items(self, e=None):
        q = self.item_box.get().lower()
        allv = ['%d %s' % (i, n) for i, n in sorted(self.item_names.items()) if n.strip()]
        self.item_box.configure(values=[x for x in allv if q in x.lower()][:400] if q else allv)

    def _shared_ok(self, ti):
        users = self.item_table_users.get(ti.get('tableId'), 0)
        return users <= 1 or messagebox.askyesno(
            'Map', 'The item table "%s" is used by %d points - this changes all of them.\n\n'
            'Press "Make unique" first to change only this point. Continue anyway?'
            % (ti.get('tableId'), users))

    def item_apply(self):
        ti, e, key, k = self._item_sel()
        if ti is None:
            self.app.status_var.set('Select an item ball (or random item) and one of its items first.')
            return
        try:
            iid = int(self.item_box.get().split()[0])
            cnt, w = int(self.item_count.get()), int(self.item_weight.get())
        except (ValueError, IndexError):
            messagebox.showerror('Map', 'Pick an item from the list; count and weight are whole numbers.')
            return
        if not self._shared_ok(ti):
            return
        it = e['itemLotteryDataList'][k]
        it['itemId'] = str(iid)
        it['weight'] = w
        if key == 'ib_items' or 'maxCount' in it:
            it['maxCount'] = max(1, cnt)
        self.tables[key].dirty = True
        self._after_item_edit('%s' % self.item_tree.selection()[0])

    def item_add(self):
        ti, e, key, k = self._item_sel()
        if ti is None:
            s, tabs = self.cur_item_table()
            if not tabs:
                return
            ti, e, key = tabs[0]
            k = -1
        if not self._shared_ok(ti):
            return
        lst = e.setdefault('itemLotteryDataList', [])
        lst.append(copy.deepcopy(lst[k]) if lst else {'itemId': '1', 'weight': 100, 'maxCount': 1})
        self.tables[key].dirty = True
        self._after_item_edit()

    def item_remove(self):
        ti, e, key, k = self._item_sel()
        if ti is None:
            return
        if len(e['itemLotteryDataList']) <= 1:
            messagebox.showinfo('Map', 'An item table needs at least one item.')
            return
        if not self._shared_ok(ti):
            return
        del e['itemLotteryDataList'][k]
        self.tables[key].dirty = True
        self._after_item_edit()

    def item_unique(self):
        """Give the selected point its own copy of its item table (so edits don't change other points)."""
        s, tabs = self.cur_item_table()
        if not tabs:
            return
        for ti, e, key in tabs:
            new = copy.deepcopy(e)
            base = (e.get('id') or 'item_table') + '_mod'
            k = 1
            while '%s%d' % (base, k) in self.item_table_by_id:
                k += 1
            new['id'] = '%s%d' % (base, k)
            self.tables[key].data['values'][0].setdefault('root', []).append(new)
            self.item_table_users[ti.get('tableId')] = max(0, self.item_table_users.get(ti.get('tableId'), 1) - 1)
            ti['tableId'] = new['id']
            self.item_table_by_id[new['id']] = (new, key)
            self.item_table_users[new['id']] = 1
            self.tables[key].dirty = True
        self.tables['ib_spawner' if tabs[0][2] == 'ib_items' else 'rp_spawner'].dirty = True
        self._after_item_edit()
        self.app.status_var.set('This point now has its own item table - edits only change it.')

    def _after_item_edit(self, select=None):
        self.show_sel()
        if select and self.item_tree.exists(select):
            self.item_tree.selection_set(select)
        n = sum(1 for x in self.layers if x.dirty) + sum(1 for t in self.tables.values() if t.dirty)
        self.dirty_var.set('%d table(s) changed - not saved yet' % n)

    def reload_item_balls(self):
        """Called after the Game data tab saved item-ball contents into the mod."""
        if self.base is None:
            return
        root = os.path.normpath(self.app.root_var.get())
        m = self.app.current_mod(ask=False)
        t = self.tables.get('ib_items')
        if t and not t.dirty:
            try:
                self.tables['ib_items'] = DataTable(t.path, root, m.romfs if m else None)
                self.index_item_tables()
                self.show_sel()
            except Exception:  # noqa: BLE001
                pass

    # ---------------- encounter editing ----------------
    def cur_spawner(self):
        if not self.sel:
            return None
        L, gi, si = self.sel
        return self.spawner_by_obj.get(L.data['values'][gi]['root'][si].get('Name'))

    def fill_encounters(self):
        self.enc_tree.delete(*self.enc_tree.get_children())
        s = self.cur_spawner()
        if not s:
            return
        for k, ei in enumerate(s.get('encountDataInfoList', [])):
            e = self.enc_by_id.get(ei.get('encountDataId'), {})
            lo, hi = e.get('minLevel', 0), e.get('maxLevel', 0)
            self.enc_tree.insert('', 'end', str(k), text=self.species_label(e.get('devNo', 0)),
                                 values=('%d-%d' % (lo, hi), ei.get('weight', 0),
                                         '%d spawn(s)' % self.enc_users.get(ei.get('encountDataId'), 0)))

    def _filter_species(self, e):
        q = self.enc_species.get().lower()
        self.enc_species.configure(values=[n for n in self.species_names if q in n.lower()][:400] if q else self.species_names)

    def enc_selected(self):
        s, sel = self.cur_spawner(), self.enc_tree.selection()
        if not s or not sel:
            return
        ei = s['encountDataInfoList'][int(sel[0])]
        e = self.enc_by_id.get(ei.get('encountDataId'), {})
        self.enc_species.set(self.species_label(e.get('devNo', 0)))
        self.enc_min.set(e.get('minLevel', 0))
        self.enc_max.set(e.get('maxLevel', 0))
        self.enc_w.set(ei.get('weight', 0))

    def _species_from_box(self):
        txt = self.enc_species.get()
        import re
        m = re.search(r'#(\d+)\)?\s*$', txt)
        if m:
            return int(m.group(1))
        for d, n in self.species.items():
            if n.lower() == txt.strip().lower():
                return d
        return None

    def enc_apply(self):
        s, sel = self.cur_spawner(), self.enc_tree.selection()
        if not s or not sel:
            self.app.status_var.set('Select a Pokémon spawn and one of its encounters first.')
            return
        ei = s['encountDataInfoList'][int(sel[0])]
        e = self.enc_by_id.get(ei.get('encountDataId'))
        dev = self._species_from_box()
        try:
            lo, hi, w = int(self.enc_min.get()), int(self.enc_max.get()), int(self.enc_w.get())
        except ValueError:
            messagebox.showerror('Map', 'Levels and weight must be whole numbers.')
            return
        if e is None or dev is None:
            messagebox.showerror('Map', 'Pick a Pokémon from the list.')
            return
        users = self.enc_users.get(ei.get('encountDataId'), 0)
        if users > 1 and (e.get('devNo') != dev or e.get('minLevel') != lo or e.get('maxLevel') != hi):
            if not messagebox.askyesno('Map', 'The encounter "%s" is used by %d spawn points - changing the '
                                       'Pokémon/levels changes all of them.\n\nUse "Add (copy)" first if you only '
                                       'want to change this spawn. Continue?' % (ei.get('encountDataId'), users)):
                return
        e['devNo'], e['minLevel'], e['maxLevel'] = dev, lo, max(lo, hi)
        ei['weight'] = w
        self.tables['enc'].dirty = self.tables['spawner'].dirty = True
        self._after_encounter_edit(int(sel[0]))

    def enc_add(self):
        """Copy the selected encounter into a new encounter entry used only by this spawn."""
        s, sel = self.cur_spawner(), self.enc_tree.selection()
        if not s:
            return
        lst = s.setdefault('encountDataInfoList', [])
        src_i = int(sel[0]) if sel else 0
        if not lst:
            messagebox.showinfo('Map', 'This spawn has no encounter to copy.')
            return
        ei = copy.deepcopy(lst[src_i])
        e = copy.deepcopy(self.enc_by_id.get(ei.get('encountDataId'), {}))
        base_id = (e.get('id') or 'ect_custom') + '_mod'
        k = 1
        while '%s%d' % (base_id, k) in self.enc_by_id:
            k += 1
        e['id'] = '%s%d' % (base_id, k)
        self.tables['enc'].data['values'][0].setdefault('root', []).append(e)
        self.enc_by_id[e['id']] = e
        ei['encountDataId'] = e['id']
        lst.append(ei)
        self.enc_users[e['id']] = 1
        self.tables['enc'].dirty = self.tables['spawner'].dirty = True
        self._after_encounter_edit(len(lst) - 1)

    def enc_remove(self):
        s, sel = self.cur_spawner(), self.enc_tree.selection()
        if not s or not sel:
            return
        lst = s.get('encountDataInfoList', [])
        if len(lst) <= 1:
            messagebox.showinfo('Map', 'A spawn needs at least one encounter.')
            return
        ei = lst.pop(int(sel[0]))
        self.enc_users[ei.get('encountDataId')] = max(0, self.enc_users.get(ei.get('encountDataId'), 1) - 1)
        self.tables['spawner'].dirty = True
        self._after_encounter_edit(None)

    def _after_encounter_edit(self, select):
        self.spawn_version = getattr(self, 'spawn_version', 0) + 1
        self.spawn_info = {n: self.encounter_lines(n) for n in self.spawner_by_obj}
        self.show_sel()
        if select is not None and self.enc_tree.exists(str(select)):
            self.enc_tree.selection_set(str(select))
        n = sum(1 for x in self.layers if x.dirty) + sum(1 for t in self.tables.values() if t.dirty)
        self.dirty_var.set('%d table(s) changed - not saved yet' % n)

    def mark_dirty(self, L):
        L.dirty = True
        n = sum(1 for x in self.layers if x.dirty) + sum(1 for t in self.tables.values() if t.dirty)
        self.dirty_var.set('%d table(s) changed - not saved yet' % n)

    def apply_fields(self):
        if not self.sel:
            return
        L, gi, si = self.sel
        s = L.data['values'][gi]['root'][si]
        try:
            x, y, z, ry = (float(self.f[k].get()) for k in ('X', 'Y', 'Z', 'RY'))
        except ValueError:
            messagebox.showerror('Map', 'X, Y, Z and rotation must be numbers.')
            return
        s['Name'] = self.f['Name'].get().strip() or s.get('Name', '')
        _set_pos(s, x, y, z)
        rot = dict(s.get('Rotation') or {})
        rot['Y'] = ry
        s['Rotation'] = rot
        self.mark_dirty(L)
        self.show_sel()
        self.redraw()

    def duplicate(self):
        if not self.sel:
            return
        L, gi, si = self.sel
        if getattr(L, 'is_scene', False):
            messagebox.showinfo('Map', 'To add a Hyperspace portal, click an empty spot near a wall and use '
                                'Create Spawn Point… - it gets its own portal data.')
            return
        root = L.data['values'][gi]['root']
        s = copy.deepcopy(root[si])
        base = s.get('Name', 'point')
        existing = {p.get('Name') for g in L.data['values'] for p in g.get('root', [])}
        k = 2
        while '%s_copy%d' % (base, k) in existing:
            k += 1
        s['Name'] = '%s_copy%d' % (base, k)
        x, y, z = _pos(s)
        _set_pos(s, x + 3, y, z + 3)
        root.insert(si + 1, s)
        self.sel = (L, gi, si + 1)
        self.mark_dirty(L)
        self.show_sel()
        self.redraw()
        self.app.status_var.set('Duplicated as %s. Note: the game only spawns things it has spawner data for '
                                '(names are matched to the spawner tables).' % s['Name'])

    def delete(self):
        if not self.sel:
            return
        L, gi, si = self.sel
        name = L.data['values'][gi]['root'][si].get('Name')
        if not messagebox.askyesno('Map', 'Remove "%s" from %s? (Only in your mod once you save.)' % (name, L.label)):
            return
        gone = L.data['values'][gi]['root'].pop(si)
        self.sel = None
        if getattr(L, 'is_scene', False):                   # a portal: its scene entry and its portal row
            rel = gone.get('_scene')
            if rel:
                chunks = self.scene(rel)[0]['Chunks']
                chunks[:] = [c for c in chunks if gate_name(c) != name]
                self.__dict__.setdefault('scene_dirty', set()).add(rel)
            t = self.tables.get('gates')
            if t:
                for g in t.data.get('values') or []:
                    if 'root' in g:
                        g['root'] = [r for r in g['root'] if r.get('gateSpawnerId') != name]
                t.dirty = True
            self.__dict__.get('new_points', {}).pop(name, None)
        else:
            self.forget_new_point(name)
        self.mark_dirty(L)
        self.show_sel()
        self.redraw()

    # ---------------- areas (interiors / dungeons / zones) ----------------
    def in_area(self, name, x, z):
        if self.area == CITY:
            return math.hypot(x - MAP_CX, z - MAP_CZ) <= CITY_RADIUS
        return self.point_area.get(name) == self.area

    def area_points(self):
        out = []
        for L in self.layers:
            for gi, si, sp in L.points():
                if self.point_area.get(sp.get('Name', '')) == self.area:
                    x, y, z = _pos(sp)
                    out.append((x, z))
        return out

    @staticmethod
    def _load_cals():
        try:
            with open(CAL_FILE, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def set_area(self, label, keep_sel=False):
        code = self.area_labels.get(label, CITY)
        self.area = CITY if code == CITY else code
        if not keep_sel:
            self.sel = None
            self.show_sel()
        if self.area == CITY:
            self.base, self.size, self.cal = self.city_base, self.city_base.size[0], city_cal(self.city_base.size[0])
            self.bg_box.configure(state='disabled')
            self.bg_var.set('')
            self._align_state(False)
        else:
            self.bg_box.configure(state='readonly')
            saved = self._load_cals().get(self.area)
            self.set_background(saved['bg'] if saved else AREA_BACKGROUNDS[0][0], saved and saved.get('cal'))
            return
        self.fit()

    def _align_state(self, on):
        self.align_chk.configure(state='normal' if on else 'disabled')
        for b in self.align_btns:
            b.configure(state='normal' if on else 'disabled')
        if not on:
            self.align_mode.set(False)

    def _bg_image(self, label):
        spec = dict(AREA_BACKGROUNDS).get(label)
        if not spec:
            return None
        if label not in self.bg_cache:
            folder, arc, prefix = spec
            root = os.path.normpath(self.app.root_var.get())
            img = None
            try:
                files = dict(zf.parse_sarc(open(os.path.join(root, 'ui', 'data', folder, arc), 'rb').read()))
                for d in files.values():
                    if d[:4] == b'BNTX':
                        t = next((t for t in zf.Bntx(d).textures if t.name.startswith(prefix)), None)
                        if t:
                            img = t.decode().convert('RGB')
                            break
            except Exception:  # noqa: BLE001
                img = None
            self.bg_cache[label] = img
        return self.bg_cache[label]

    def set_background(self, label, cal=None):
        self.bg_var.set(label)
        img = self._bg_image(label)
        pts = self.area_points()
        if img is None:
            size = 2048
            self.cal = fit_cal(pts, size)
            self.base, self.size = self.grid_image(size), size
            self._align_state(False)
            note = 'Area %s: points on a grid (lines every 10 m).' % self.area
        else:
            self.base, self.size = img, img.size[0]
            if cal:
                self.cal = dict(cal)
            else:
                g = img.convert('L').point(lambda v: 255 if v > 30 else 0)
                self.cal = fit_cal(pts, self.size, g.getbbox())
            self._align_state(True)
            note = ('Area %s on %s%s. Tick "Align" to drag/scale the points onto the image, then "Save alignment".'
                    % (self.area, label, ' (saved alignment)' if cal else ' - rough automatic placement'))
        self.app.status_var.set(note)
        self.fit()

    def grid_image(self, size):
        img = Image.new('RGB', (size, size), (24, 24, 28))
        d = ImageDraw.Draw(img)
        x0, z0 = m2w(self.cal, 0, 0)
        x1, z1 = m2w(self.cal, size, size)
        step = 10 if self.cal['scale'] > 4 else 50
        for k in range(int(min(x0, x1) // step) * step, int(max(x0, x1)) + step, step):
            px = w2m(self.cal, k, 0)[0]
            d.line((px, 0, px, size), fill=(60, 60, 70) if k else (110, 110, 130))
        for k in range(int(min(z0, z1) // step) * step, int(max(z0, z1)) + step, step):
            py = w2m(self.cal, 0, k)[1]
            d.line((0, py, size, py), fill=(60, 60, 70) if k else (110, 110, 130))
        return img

    def flip(self, axis):
        pts = self.area_points()
        if not pts:
            return
        cx, cz = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        before = w2m(self.cal, cx, cz)
        self.cal[axis] *= -1
        after = w2m(self.cal, cx, cz)
        self.cal['ox'] += before[0] - after[0]
        self.cal['oy'] += before[1] - after[1]
        self.redraw()

    def rotate90(self):
        pts = self.area_points()
        if not pts:
            return
        cx, cz = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        before = w2m(self.cal, cx, cz)
        c = self.cal
        c['swap'] = not c['swap']
        c['sx'], c['sz'] = -c['sz'], c['sx']
        after = w2m(c, cx, cz)
        c['ox'] += before[0] - after[0]
        c['oy'] += before[1] - after[1]
        self.redraw()

    def save_alignment(self):
        cals = self._load_cals()
        cals[self.area] = {'bg': self.bg_var.get(), 'cal': self.cal}
        try:
            with open(CAL_FILE, 'w', encoding='utf-8') as f:
                json.dump(cals, f, indent=1)
            self.app.status_var.set('Saved the alignment of area %s on %s.' % (self.area, self.bg_var.get()))
        except OSError as e:
            messagebox.showerror('Map', 'Could not save: %s' % e)

    def zoom_to_sel(self):
        if not self.sel:
            return
        L, gi, si = self.sel
        x, y, z = _pos(L.data['values'][gi]['root'][si])
        mx, my = w2m(self.cal, x, z)
        self.zoom = max(self.zoom, 2.0)
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.ox, self.oy = mx - cw / 2 / self.zoom, my - ch / 2 / self.zoom
        self.redraw()

    def find_next(self):
        q = self.find_var.get().strip().lower()
        if not q or not self.layers:
            return
        order = [(L, gi, si) for L in self.layers for gi, si, s in L.points() if q in s.get('Name', '').lower()]
        if not order:
            self.app.status_var.set('No point name contains "%s"' % q)
            return
        i = 0
        if self.sel in order:
            i = (order.index(self.sel) + 1) % len(order)
        self.sel = order[i]
        if self.sel[0].var and not self.sel[0].var.get():
            self.sel[0].var.set(True)
        L, gi, si = self.sel
        x, y, z = _pos(L.data['values'][gi]['root'][si])
        name = L.data['values'][gi]['root'][si].get('Name', '')
        if not self.in_area(name, x, z):            # the match is in another area: switch to it
            code = self.point_area.get(name)
            label = next((k for k, v in self.area_labels.items() if v == (code or CITY)), CITY)
            self.area_var.set(label)
            self.set_area(label, keep_sel=True)
        self.show_sel()
        self.zoom_to_sel()
        self.app.status_var.set('Match %d of %d' % (i + 1, len(order)))

    # ---------------- new spawn points ----------------
    def find_scene(self, name, pattern='*pokemon*spawner*.trscn'):
        """Game path of the scene file that places spawn point `name` (searched once, then remembered)."""
        cache = self.__dict__.setdefault('scene_of', {})
        if name in cache:
            return cache[name]
        root = os.path.normpath(self.app.root_var.get())
        key = name.encode()
        first = sorted(glob.glob(os.path.join(root, 'world', 'ik_scene', '**', pattern), recursive=True))
        rest = sorted(glob.glob(os.path.join(root, 'world', 'ik_scene', '**', '*spawner*.trscn'), recursive=True))
        for path in first + [x for x in rest if x not in first]:
            rel = os.path.relpath(path, root).replace(os.sep, '/')
            src = _mod_file(path, root, self.romfs)[0]
            try:
                with open(src, 'rb') as f:
                    if key in f.read():
                        cache[name] = rel
                        return rel
            except OSError:
                continue
        cache[name] = None
        return None

    def find_scenes(self, name, pattern):
        """Every scene file matching `pattern` that places `name` (an NPC can be in a day and a night scene)."""
        first = self.find_scene(name, pattern)
        if not first:
            return []
        cache = self.__dict__.setdefault('scenes_of', {})
        if name not in cache:
            root = os.path.normpath(self.app.root_var.get())
            key = name.encode()
            out = [first]
            for path in sorted(glob.glob(os.path.join(root, 'world', 'ik_scene', '**', pattern), recursive=True)):
                rel = os.path.relpath(path, root).replace(os.sep, '/')
                if rel == first:
                    continue
                try:
                    with open(_mod_file(path, root, self.romfs)[0], 'rb') as f:
                        if key in f.read():
                            out.append(rel)
                except OSError:
                    continue
            cache[name] = out
        return cache[name]

    def scene(self, rel):
        """[data, schema path, game path] of a scene file (the mod's copy if it has one), loaded once."""
        sc = self.__dict__.setdefault('scenes', {})
        if rel not in sc:
            root = os.path.normpath(self.app.root_var.get())
            path = os.path.join(root, *rel.split('/'))
            src = _mod_file(path, root, self.romfs)[0]
            sch = zm.schema_for(path)
            sc[rel] = [json.loads(zm.to_json(src, sch)), sch, path]
        return sc[rel]

    @staticmethod
    def _pos_offset(data, x, y, z):
        """Byte offset of the point's position (three floats) inside a scene chunk's data, or None."""
        import struct
        b = bytes(data)
        for k in range(0, len(b) - 11, 4):
            a, bb, c = struct.unpack_from('<3f', b, k)
            if abs(a - x) < 0.01 and abs(bb - y) < 0.01 and abs(c - z) < 0.01:
                return k
        return None

    def add_spawn_point(self):
        """A new Pokémon spawn point next to the selected one: its own position, spawner, encounter and scene entry."""
        import random
        import struct
        if not self.sel:
            return
        L, gi, si = self.sel
        p = L.data['values'][gi]['root'][si]
        old = p.get('Name', '')
        s = self.spawner_by_obj.get(old)
        if not s or not s.get('encountDataInfoList'):
            messagebox.showinfo('Map', 'Select a Pokémon spawn point first (one that lists encounters).')
            return
        e0 = self.enc_by_id.get(s['encountDataInfoList'][0].get('encountDataId'))
        if not e0:
            messagebox.showinfo('Map', "The encounter of %s is missing from the encounter table, so it can't be "
                                'copied. Pick another spawn point.' % old)
            return
        if len(old) < 4 or not old[-3:].isdigit():
            messagebox.showinfo('Map', "%s has an unexpected name, so it can't be copied." % old)
            return
        pick = self._ask_spawn(e0)
        if not pick:
            return
        dev, lo, hi = pick
        rel = self.find_scene(old)
        if not rel:
            messagebox.showerror('Map', 'Spawn point %s isn\'t placed by a scene file the toolkit can find, so it can\'t be '
                                 'copied. Try a spawn point in a Wild Zone.' % old)
            return
        sc = self.scene(rel)
        chunks = sc[0]['Chunks']
        ci = next((i for i, c in enumerate(chunks) if old.encode() in bytes(c.get('Data', []))), None)
        x, y, z = _pos(p)
        off = self._pos_offset(chunks[ci]['Data'], x, y, z) if ci is not None else None
        if off is None:
            messagebox.showerror('Map', 'Could not find where %s\'s position is stored in its scene file, so it '
                                 'can\'t be copied safely.' % old)
            return
        # a new name of the same length that no point, spawner or other mod uses
        taken = {q.get('Name') for LL in self.layers for _, _, q in LL.points()} | set(self.spawner_by_obj)
        for other in zm.list_mods():
            path = other.path_for(self.tables['spawner'].rel)
            if os.path.isfile(path):
                try:
                    d = json.loads(zm.to_json(path, zm.schema_for(self.tables['spawner'].path)))
                    taken |= {o.get('objectName') for g in d['values'] for r in g.get('root', [])
                              for o in r.get('appearanceSpawnerObjectInfoList', [])}
                except Exception:  # noqa: BLE001
                    pass
        new = next((old[:-3] + '%03d' % k for k in range(901, 1000) if old[:-3] + '%03d' % k not in taken), None)
        if new is None:
            messagebox.showerror('Map', 'No free spawn point names left for %s.' % old[:-4])
            return
        nx, nz = x + 3.0, z + 3.0
        # 1. position
        q = copy.deepcopy(p)
        q['Name'] = new
        _set_pos(q, nx, y, nz)
        L.data['values'][gi]['root'].insert(si + 1, q)
        self.mark_dirty(L)
        # 2. encounter (only this spawn point uses it)
        enc = copy.deepcopy(e0)
        enc['id'] = 'ect_' + new
        enc['devNo'], enc['minLevel'], enc['maxLevel'] = dev, lo, max(lo, hi)
        enc.pop('formNo', None)
        egroups = self.tables['enc'].data['values']
        eg = next((g for g in egroups if e0 in g.get('root', [])), egroups[0])
        eg.setdefault('root', []).append(enc)
        self.enc_by_id[enc['id']] = enc
        # 3. spawner
        sn = copy.deepcopy(s)
        sn['id'] = 'id_' + new
        for o in sn.get('appearanceSpawnerObjectInfoList', []):
            o['objectName'] = new
        info = copy.deepcopy(s['encountDataInfoList'][0])
        info['encountDataId'] = enc['id']
        sn['encountDataInfoList'] = [info]
        for g in self.tables['spawner'].data['values']:
            rr = g.get('root', [])
            if s in rr:
                rr.insert(rr.index(s) + 1, sn)
                break
        self.tables['enc'].dirty = self.tables['spawner'].dirty = True
        # 4. scene: the point's chunk tree, renamed, with fresh ids for the chunks that belong only to it
        ids = []

        def all_ids(cs):
            for c in cs:
                ids.append(c.get('Id'))
                all_ids(c.get('Children') or [])
        all_ids(chunks)
        unique = {i for i in ids if ids.count(i) == 1}
        used = set(ids)
        rng = random.Random(hash(new) & 0xFFFFFFFF)

        def rename(c):
            b = bytes(c.get('Data', []))
            if old.encode() in b:
                c['Data'] = list(b.replace(old.encode(), new.encode()))
            if c.get('Id') in unique:
                nid = rng.getrandbits(64)
                while nid in used:
                    nid = rng.getrandbits(64)
                used.add(nid)
                c['Id'] = nid
            for ch in c.get('Children') or []:
                rename(ch)
        nc = copy.deepcopy(chunks[ci])
        rename(nc)
        b = bytearray(nc['Data'])
        struct.pack_into('<3f', b, off, nx, y, nz)
        nc['Data'] = list(b)
        chunks.insert(ci + 1, nc)
        self.__dict__.setdefault('scene_dirty', set()).add(rel)
        self.__dict__.setdefault('new_points', {})[new] = (rel, off)
        # refresh
        self.index_spawn_tables()
        self.spawn_info = {n: self.encounter_lines(n) for n in self.spawner_by_obj}
        self.sel = (L, gi, si + 1)
        self.show_sel()
        self.redraw()
        n = sum(1 for x_ in self.layers if x_.dirty) + sum(1 for t in self.tables.values() if t.dirty)
        self.dirty_var.set('%d table(s) and %d scene(s) changed - not saved yet' % (n, len(self.scene_dirty)))
        self.app.status_var.set('Added spawn point %s (%s, levels %d-%d) 3 m from %s - tick Edit mode to drag it into place, then '
                                'Save to mod.' % (new, self.species_label(dev), lo, max(lo, hi), old))

    def create_spawn_point(self):
        """The step-by-step menu for a new point at the clicked empty spot."""
        if self.empty_spot is None:
            return
        import za_mapcreate
        za_mapcreate.open_menu(self, *self.empty_spot)

    def encounter_overview(self):
        """Every Pokémon the spawners bring, in one sortable, searchable, editable list."""
        if not self.loaded or not self.tables:
            messagebox.showinfo('Encounter overview', 'Wait until the map has loaded.')
            return
        import za_encounters
        za_encounters.open_window(self)

    def hyperspace_spawns(self):
        """Which Pokémon Hyperspace brings, by zone type and rank."""
        if not self.loaded or not self.tables:
            messagebox.showinfo('Hyperspace Pokémon', 'Wait until the map has loaded.')
            return
        import za_hyperspace
        za_hyperspace.open_window(self)

    def forget_new_point(self, name):
        """A spawn point added this session was deleted again: drop its scene entry, spawner and encounter too."""
        info = self.__dict__.get('new_points', {}).pop(name, None)
        if not info:
            return
        for rel, _ in _spots(info):
            chunks = self.scenes[rel][0]['Chunks']
            chunks[:] = [c for c in chunks if name.encode() not in bytes(c.get('Data', []))]
            self.__dict__.setdefault('scene_dirty', set()).add(rel)
        for g in self.tables['spawner'].data['values']:
            g['root'] = [s for s in g.get('root', [])
                         if not any(o.get('objectName') == name for o in s.get('appearanceSpawnerObjectInfoList', []))]
        for g in self.tables['enc'].data['values']:
            g['root'] = [e for e in g.get('root', []) if e.get('id') != 'ect_' + name]
        self.tables['enc'].dirty = self.tables['spawner'].dirty = True
        self.index_spawn_tables()
        self.spawn_info = {n: self.encounter_lines(n) for n in self.spawner_by_obj}

    def _ask_spawn(self, e0):
        """Small dialog: which Pokémon and levels the new spawn point spawns. Returns (species, min, max) or None."""
        w = tk.Toplevel(self.app)
        w.title('Add spawn point')
        w.transient(self.app)
        w.resizable(False, False)
        bg = ttk.Style(w).lookup('TFrame', 'background') or None
        if bg:
            w.configure(background=bg)
        f = ttk.Frame(w, padding=12)
        f.pack(fill='both')
        ttk.Label(f, wraplength=380, justify='left', text=(
            'A new spawn point 3 m from the selected one, with its own encounter. To place it, tick Edit mode and drag '
            'it on the map, then Save to mod. Ported Pokémon in your mod can be picked here too.')).grid(row=0, column=0, columnspan=4,
                                                                                   sticky='w', pady=(0, 8))
        ttk.Label(f, text='Pokémon:').grid(row=1, column=0, sticky='w')
        sp = ttk.Combobox(f, width=30, values=self.species_names)
        sp.grid(row=1, column=1, columnspan=3, sticky='w')
        sp.set(self.species_label(e0.get('devNo', 0)))

        def filt(e):
            q = sp.get().lower()
            sp.configure(values=[n for n in self.species_names if q in n.lower()][:400] if q else self.species_names)
        sp.bind('<KeyRelease>', filt)
        lo, hi = tk.IntVar(value=e0.get('minLevel', 5)), tk.IntVar(value=e0.get('maxLevel', 10))
        ttk.Label(f, text='Levels:').grid(row=2, column=0, sticky='w', pady=6)
        ttk.Spinbox(f, from_=1, to=100, textvariable=lo, width=5).grid(row=2, column=1, sticky='w')
        ttk.Label(f, text='to').grid(row=2, column=2)
        ttk.Spinbox(f, from_=1, to=100, textvariable=hi, width=5).grid(row=2, column=3, sticky='w')
        out = {}

        def ok():
            keep = self.enc_species.get()
            self.enc_species.set(sp.get())
            dev = self._species_from_box()
            self.enc_species.set(keep)
            if dev is None:
                messagebox.showerror('Add spawn point', 'Pick a Pokémon from the list.', parent=w)
                return
            try:
                out['v'] = (dev, int(lo.get()), int(hi.get()))
            except (ValueError, tk.TclError):
                messagebox.showerror('Add spawn point', 'Levels must be whole numbers.', parent=w)
                return
            w.destroy()
        bar = ttk.Frame(f)
        bar.grid(row=3, column=0, columnspan=4, sticky='e', pady=(8, 0))
        ttk.Button(bar, text='Add', command=ok).pack(side='right')
        ttk.Button(bar, text='Cancel', command=w.destroy).pack(side='right', padx=6)
        w.bind('<Return>', lambda e: ok())
        w.bind('<Escape>', lambda e: w.destroy())
        w.grab_set()
        self.app.wait_window(w)
        return out.get('v')

    def _move_in_scene(self, rel, name, off, x, y, z, dirty, rot=None):
        """Write a new point's position (and its rotation, in degrees, when given) into its scene entry. The entry
        holds the position, then the rotation in radians, then the scale - the game turns the NPC by these."""
        import math
        import struct
        for c in self.scenes[rel][0]['Chunks']:
            if name.encode() in bytes(c.get('Data', [])):
                b = bytearray(c['Data'])
                want = bytes(struct.pack('<3f', x, y, z))
                if rot is not None and len(b) >= off + 24 and \
                        all(abs(v) <= 6.3 for v in struct.unpack_from('<3f', b, off + 12)):
                    want += struct.pack('<3f', *(math.radians(float(rot.get(k, 0.0))) for k in 'XYZ'))
                if bytes(b[off:off + len(want)]) != want:
                    b[off:off + len(want)] = want
                    c['Data'] = list(b)
                    dirty.add(rel)
                break

    def save_to_mod(self):
        scene_dirty = self.__dict__.get('scene_dirty', set())
        # new points follow their position on the map into the scene file
        for name, info in self.__dict__.get('new_points', {}).items():
            pt = next((q for LL in self.layers for _, _, q in LL.points() if q.get('Name') == name), None)
            for rel, off in _spots(info):
                if pt is None or rel not in self.__dict__.get('scenes', {}):
                    continue
                self._move_in_scene(rel, name, off, *_pos(pt), dirty=scene_dirty, rot=pt.get('Rotation') or {})
        for L in [x for x in self.layers if x.dirty and getattr(x, 'is_scene', False)]:
            L.write_into(self, scene_dirty)                # portals live in scene files, not a table
            L.dirty = False
        changed = [L for L in self.layers if L.dirty] + [t for t in self.tables.values() if t.dirty]
        extra = self.__dict__.get('extra_files', {})      # other game files a new point needs (wild behaviour)
        if not changed and not scene_dirty and not extra:
            self.app.status_var.set('Nothing to save.')
            return
        m = self.app.current_mod()
        if not m:
            return
        for L in changed:
            if not self.app.confirm_save_target('The map table %s' % os.path.basename(L.rel), L.src, L.stamp, m, L.rel):
                return
        # taken now, on this thread: points dragged while it saves stay unsaved
        texts = [(L, json.dumps(L.data)) for L in changed]
        scene_texts = [(rel, json.dumps(self.scenes[rel][0]), self.scenes[rel][1]) for rel in scene_dirty]
        extra_files = dict(extra)
        extra.clear()

        def job():
            for L, text in texts:
                zm.from_json(text, zm.schema_for(L.path), m.path_for(L.rel))
            for rel, text, sch in scene_texts:
                out = m.path_for(rel)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                zm.from_json(text, sch, out)
            for rel, data in extra_files.items():
                m.add_bytes(rel, data)
            return texts

        def done(r):
            for L, text in r:
                L.src, L.stamp = m.path_for(L.rel), zm.file_stamp(m.path_for(L.rel))
                if json.dumps(L.data) == text:
                    L.dirty = False
            r = [L for L, _ in r]
            n_sc = len(scene_dirty)
            scene_dirty.clear()
            self.dirty_var.set('Saved %d table(s)%s into "%s"' % (len(r), ' and %d scene(s)' % n_sc if n_sc else '', m.name))
            self.app.refresh_mods()
        self.app.run_bg('Saving map changes', job, done)

    def spawn_icon(self, name, size):
        """The icon of the Pokémon most likely to appear at spawn point `name` (RGBA, size x size), or None."""
        s = self.spawner_by_obj.get(name)
        if not s:
            return None
        infos = sorted(s.get('encountDataInfoList') or [], key=lambda i: -(i.get('weight') or 0))
        e = self.enc_by_id.get(infos[0].get('encountDataId')) if infos else None
        if not e or not e.get('devNo'):
            return None
        key = (e['devNo'], e.get('formNo') or 0, size)
        if key not in self.icon_cache:
            self.icon_cache[key] = self._load_icon(key[0], key[1], size)
        return self.icon_cache[key]

    def _load_icon(self, dev, form, size):
        root = os.path.normpath(self.app.root_var.get())
        folders = ['pm%04d_%02d_00' % (dev, form), 'pm%04d_%02d_00' % (dev, form + 50) if form else None,
                   'pm%04d_00_00' % dev]
        for base in [b for b in (self.romfs, root) if b]:
            for f in [x for x in folders if x]:
                p = os.path.join(base, 'ik_pokemon', 'data', f[:6], f, 'icon', f + '_00_0.bntx')
                if os.path.isfile(p):
                    try:
                        with open(p, 'rb') as fh:
                            im = zf.Bntx(fh.read()).textures[0].decode(0, 0).convert('RGBA')
                        return im.resize((size, size), Image.LANCZOS)
                    except Exception:  # noqa: BLE001
                        continue
        return None

    def redraw_theme(self):
        self.redraw()
