"""Lumiose Toolkit - browse, preview and extract textures, text and UI archives
from the ZA game export.

Run:  python za_extractor.py   (or double-click "Lumiose Toolkit.bat")
"""
import json
import os
import queue
import re
import shutil
import tempfile
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

import numpy as np
from PIL import Image, ImageTk

APP_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))   # the .exe's folder when frozen
sys.path.insert(0, APP_DIR)
import za_formats as zf  # noqa: E402
import za_names as zn  # noqa: E402
import za_trinity as zt  # noqa: E402
import za_modding as zm  # noqa: E402
import za_version  # noqa: E402

AUDIO_EXTS = ('.bnk', '.pck', '.wem')
# Trinity formats converted to JSON during bulk extraction (animations/buffers only when picked directly)
BULK_JSON_EXTS = ('.bin', '.trmtr', '.trmdl', '.trskl', '.trmsh', '.trscn', '.trins', '.trpmcatalog')
DEFAULT_GAME_ROMFS = ''          # set on the Mods tab (an unmodified dump's romfs folder)


def pythonw():
    exe = sys.executable
    cand = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    return cand if os.path.isfile(cand) else exe


def viewer_command():
    """How to start the 3D viewer: the .exe runs itself with --viewer; from source it runs za_viewer.py."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--viewer']
    return [pythonw(), os.path.join(APP_DIR, 'za_viewer.py')]


def editor_command():
    """How to start the in-app image editor window (za_editor.py)."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--editor']
    return [pythonw(), os.path.join(APP_DIR, 'za_editor.py')]


def launch_viewer(model_path, anim_path=None):
    args = viewer_command() + [model_path]
    if anim_path:
        args += ['--anim', anim_path]
    subprocess.Popen(args, cwd=APP_DIR)

BUCKET_RE = re.compile(r'pm\d{4}|[a-z]{3}\d{4}_\d\d_[a-z0-9]+')

THEMES = {
    'dark': dict(bg='#1f1f1f', panel='#2a2a2a', field='#252526', fg='#e3e3e3', dim='#8f8f8f',
                 border='#3c3c3c', hover='#383838', sel='#264f78', selfg='#ffffff', accent='#4fa3ff',
                 canvas='#181818', checker=(58, 84), disabled='#6b6b6b'),
    'light': dict(bg='#f0f0f0', panel='#e4e4e4', field='#ffffff', fg='#1b1b1b', dim='#6a6a6a',
                  border='#c4c4c4', hover='#d8d8d8', sel='#cce4f7', selfg='#000000', accent='#0063b1',
                  canvas='#d0d0d0', checker=(170, 215), disabled='#a0a0a0'),
}

SETTINGS_PATH = os.path.join(APP_DIR, 'settings.json')
LOG_PATH = os.path.join(APP_DIR, 'error_log.txt')
DEFAULT_ROOT = ''                # no game folder until the user imports or picks one
DEFAULT_OUT = os.path.join(APP_DIR, 'extracted')

TYPE_FILTERS = {
    'All files': None,
    'Textures (.bntx)': ('.bntx',),
    'Text (.dat)': ('.dat',),
    'UI archives (.arc)': ('.arc',),
    'Textures + archives': ('.bntx', '.arc'),
    'Models (.trmdl)': ('.trmdl',),
    'Models + textures': ('.trmdl', '.bntx'),
    'Animations (.tranm)': ('.tranm',),
}
TYPE_LABELS = {'.bntx': 'Texture', '.dat': 'Text', '.tbl': 'Text labels', '.arc': 'Archive', '.trmdl': 'Model',
               '.tranm': 'Animation'}
MAX_RESULTS = 5000
ZOOMS = ['Fit', '25%', '50%', '100%', '200%', '400%']
CUBE_FACES = ['+X', '-X', '+Y', '-Y', '+Z', '-Z']


def human_size(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%d %s' % (n, unit)) if unit == 'B' else ('%.1f %s' % (n, unit))
        n /= 1024.0


def log_error(text):
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(time.strftime('[%Y-%m-%d %H:%M:%S] ') + text + '\n')
    except OSError:
        pass


def category_of(path):
    ext = os.path.splitext(path)[1].lower()
    cat = {'.bntx': 'tex', '.dat': 'text', '.arc': 'arc', '.trmdl': 'model'}.get(ext)
    if cat:
        return cat
    if ext in AUDIO_EXTS:
        return 'audio'
    if ext in BULK_JSON_EXTS and zm.schema_for(path):
        return 'data'
    return 'other'


# ---------------------------------------------------------------------------
# extraction (runs on a worker thread, no tkinter calls in here)
# ---------------------------------------------------------------------------

class Extractor:
    def __init__(self, root_dir, out_dir, opts, arc_loader):
        self.root_dir = os.path.normcase(os.path.abspath(root_dir))
        self.out_dir = out_dir
        self.o = opts
        self.arc_loader = arc_loader
        self.outputs = 0
        self.errors = []

    def rel(self, path):
        p = os.path.abspath(path)
        if self.o['keep_structure'] and os.path.normcase(p).startswith(self.root_dir + os.sep):
            return os.path.relpath(p, self.root_dir)
        return os.path.basename(p)

    def _write(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        self.outputs += 1

    def texture(self, data, out_noext):
        b = zf.Bntx(data)
        multi = len(b.textures) > 1
        for t in b.textures:
            base = os.path.join(out_noext, t.name) if multi else out_noext
            os.makedirs(os.path.dirname(base) or '.', exist_ok=True)
            if not t.supported:
                self.errors.append('%s: unsupported format %s (raw file kept)' % (base, t.format_name))
                self._write(base + '.bntx', data)
                continue
            if self.o['png']:
                for layer in range(t.array_len):
                    if t.array_len == 1:
                        suffix = ''
                    elif t.is_cubemap:
                        suffix = '_face%d' % layer
                    else:
                        suffix = '_layer%d' % layer
                    img = t.decode(layer, 0, rebuild_normal_z=self.o['rebuild_nrm'])
                    save_png(img, base + suffix + '.png')
                    self.outputs += 1
            if self.o['dds']:
                try:
                    self._write(base + '.dds', t.to_dds())
                except zf.FormatError as e:
                    self.errors.append('%s: %s' % (base, e))

    def text(self, data, tbl, out_noext):
        txt = zf.msg_to_text(data, tbl)
        self._write(out_noext + '.txt', txt.encode('utf-8-sig'))

    def archive(self, data, out_dir):
        for name, inner in zf.parse_sarc(data):
            target = os.path.join(out_dir, *name.split('/'))
            if inner[:4] == b'BNTX' and (self.o['png'] or self.o['dds']):
                self.texture(inner, os.path.splitext(target)[0])
            else:
                self._write(target, inner)

    def model(self, path, out_noext):
        m = zt.Model(path)
        anims = []
        if self.o.get('gltf_anims') and m.skeleton:
            for p in zt.related_animations(path):
                try:
                    anims.append(zt.Animation(p))
                except Exception as e:  # noqa: BLE001
                    self.errors.append('%s: %s' % (p, e))
        os.makedirs(os.path.dirname(out_noext), exist_ok=True)
        zt.export_glb(m, anims, out_noext + '.glb')
        self.outputs += 1

    def audio(self, path, out_noext):
        streams = zm.audio_streams(path)
        os.makedirs(out_noext, exist_ok=True)
        for label, src in streams:
            name = re.sub(r'[^\w\-]+', '_', label).strip('_')
            try:
                zm.decode_audio(src, os.path.join(out_noext, name + '.wav'))
                self.outputs += 1
            except Exception as e:  # noqa: BLE001
                self.errors.append('%s [%s]: %s' % (path, label, e))

    def data_json(self, path, out_path):
        txt = zm.to_json(path)
        self._write(out_path, txt.encode('utf-8'))

    def disk_file(self, path, explicit):
        cat = category_of(path)
        rel = self.rel(path)
        out_noext = os.path.join(self.out_dir, os.path.splitext(rel)[0])
        ext = os.path.splitext(path)[1].lower()
        if not explicit:
            if cat == 'tex' and not self.o['inc_tex']:
                return
            if cat == 'text' and not self.o['inc_text']:
                return
            if cat == 'arc' and not self.o['inc_arc']:
                return
            if cat == 'model' and not self.o.get('inc_model', True):
                return
            if cat == 'data' and not self.o.get('inc_data', True):
                return
            if cat == 'audio' and not self.o.get('inc_audio', False):
                return
            if cat == 'other' and not self.o['inc_other']:
                return  # includes .tbl label files, which are read alongside their .dat
        if cat == 'model' and self.o.get('gltf'):
            self.model(path, out_noext)
            if not (explicit and self.o['inc_other']):
                return
        if cat == 'audio' and self.o.get('wav'):
            self.audio(path, out_noext)
            return
        if self.o.get('json') and (cat == 'data' or (explicit and zm.schema_for(path))):
            self.data_json(path, os.path.join(self.out_dir, rel) + '.json')
            if not self.o['inc_other']:
                return
        with open(path, 'rb') as f:
            data = f.read()
        if data[:4] == b'BNTX':
            self.texture(data, out_noext)
        elif data[:4] == b'SARC':
            self.archive(data, out_noext)
        elif cat == 'text' and zf.is_msg_dat(data):
            tbl_path = os.path.splitext(path)[0] + '.tbl'
            tbl = open(tbl_path, 'rb').read() if os.path.isfile(tbl_path) else None
            self.text(data, tbl, out_noext)
        elif explicit or self.o['inc_other'] or cat in ('model', 'data', 'audio'):
            self._write(os.path.join(self.out_dir, rel), data)

    def arc_member(self, arc_path, name):
        data = self.arc_loader(arc_path)[name]
        rel = self.rel(arc_path)
        target = os.path.join(self.out_dir, os.path.splitext(rel)[0], *name.split('/'))
        if data[:4] == b'BNTX':
            self.texture(data, os.path.splitext(target)[0])
        else:
            self._write(target, data)


def save_png(img, path):
    """Save RGBA image, shrinking to RGB / L when channels are redundant."""
    arr = np.asarray(img)
    if arr[..., 3].min() == 255:
        rgb = arr[..., :3]
        if (rgb[..., 0] == rgb[..., 1]).all() and (rgb[..., 1] == rgb[..., 2]).all():
            img = Image.fromarray(rgb[..., 0], 'L')
        else:
            img = Image.fromarray(np.ascontiguousarray(rgb), 'RGB')
    img.save(path)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Lumiose Toolkit %s' % za_version.VERSION)
        try:
            self.iconbitmap(default=os.path.join(APP_DIR, 'lumiose_toolkit.ico'))
        except tk.TclError:
            pass
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(1400, sw - 40), min(860, sh - 110)
        self.geometry('%dx%d+%d+%d' % (w, h, max(0, (sw - w) // 2), max(0, (sh - h - 60) // 2)))
        self.minsize(900, 520)
        self.q = queue.Queue()
        self.live = {}              # live-edited PNG -> info (see live_edit_texture)
        self.variant_by_id = {}     # dress-up item id -> clothing colour variant (search results)
        self._live_after = None
        self.settings = self.load_settings()
        self.started_with_settings = bool(self.settings)     # False on a brand-new install
        scale = self.settings.get('ui_scale', 1.0)
        if isinstance(scale, (int, float)) and 0.75 <= scale <= 2.0 and abs(scale - 1.0) > 0.01:
            self.tk.call('tk', 'scaling', float(self.tk.call('tk', 'scaling')) * scale)   # interface size
        try:
            zm.HISTORY_MAX = max(1, int(self.settings.get('history_max', zm.HISTORY_MAX)))
        except (TypeError, ValueError):
            pass

        self.root_var = tk.StringVar(value=self.settings.get('root', DEFAULT_ROOT))
        self.out_var = tk.StringVar(value=self.settings.get('out', DEFAULT_OUT))
        self.search_var = tk.StringVar()
        self.filter_var = tk.StringVar(value=self.settings.get('filter', 'All files'))
        self.view_var = tk.StringVar(value='RGBA')
        self.zoom_var = tk.StringVar(value='Fit')
        self.nrm_var = tk.BooleanVar(value=self.settings.get('rebuild_nrm', True))
        self.tex_var = tk.StringVar()
        self.layer_var = tk.IntVar(value=0)
        self.mip_var = tk.IntVar(value=0)
        self.png_var = tk.BooleanVar(value=self.settings.get('png', True))
        self.dds_var = tk.BooleanVar(value=self.settings.get('dds', False))
        self.keep_var = tk.BooleanVar(value=self.settings.get('keep_structure', True))
        self.inc_tex = tk.BooleanVar(value=self.settings.get('inc_tex', True))
        self.inc_text = tk.BooleanVar(value=self.settings.get('inc_text', True))
        self.inc_arc = tk.BooleanVar(value=self.settings.get('inc_arc', True))
        self.inc_other = tk.BooleanVar(value=self.settings.get('inc_other', False))
        self.inc_model = tk.BooleanVar(value=self.settings.get('inc_model', True))
        self.inc_data = tk.BooleanVar(value=self.settings.get('inc_data', True))
        self.inc_audio = tk.BooleanVar(value=self.settings.get('inc_audio', False))
        self.gltf_var = tk.BooleanVar(value=self.settings.get('gltf', True))
        self.gltf_anims_var = tk.BooleanVar(value=self.settings.get('gltf_anims', False))
        self.json_var = tk.BooleanVar(value=self.settings.get('json', True))
        self.wav_var = tk.BooleanVar(value=self.settings.get('wav', True))
        self.mod_var = tk.StringVar(value=self.settings.get('mod', ''))
        self.game_romfs_var = tk.StringVar(value=self.settings.get('game_romfs', DEFAULT_GAME_ROMFS))
        self.pkg_out_var = tk.StringVar(value=self.settings.get('pkg_out', os.path.join(APP_DIR, 'packages')))
        self.recomp_var = tk.StringVar(value=self.settings.get('recomp', ''))
        self.search_mode = tk.StringVar(value='Files & names')
        self.text_lang = tk.StringVar(value=self.settings.get('text_lang', 'English'))
        self.text_index = None          # [(path, line index, label, text, lowercase text)]
        self.text_index_busy = False
        self.pending_line = None
        self.tgt_vars = {k: tk.BooleanVar(value=k in self.settings.get('targets', ['recomp'])) for k in zm.TARGETS}
        self.status_var = tk.StringVar(value='Ready')
        self.index_var = tk.StringVar(value='')
        self.dark_var = tk.BooleanVar(value=self.settings.get('theme', 'dark') == 'dark')

        self.index = []            # list of (lower relpath, abs path)
        self.index_ready = False
        self.names = []            # za_names.NameEntry list
        self.buckets = {}          # id pattern (pm0025, upb0020_00_tshirt, ...) -> list of (rel, abs)
        self.name_hits = {}        # tree iid -> (NameEntry, [(kind, path)], file count)
        self.arc_cache = {}        # arc path -> {name: bytes}
        self.preview_token = 0
        self.cur = None            # current preview result
        self.cur_img = None        # decoded RGBA image of current texture view
        self.photo = None
        self._checker_cache = {}
        self._render_after = None
        self._search_after = None
        self.cancel_flag = threading.Event()
        self.extracting = False

        self.style = ttk.Style(self)
        self.style.theme_use('clam')
        self.build_ui()
        self.apply_theme()
        self.report_callback_exception = self.on_tk_error
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.after(30, self.poll_queue)
        self.reload_root()

    # ---------------- settings ----------------
    def load_settings(self):
        try:
            with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_settings(self):
        s = dict(self.settings)              # keeps Preferences keys (image editor, 3D view, interface size…)
        s.update({
            'root': self.root_var.get(), 'out': self.out_var.get(), 'filter': self.filter_var.get(),
            'rebuild_nrm': self.nrm_var.get(), 'png': self.png_var.get(), 'dds': self.dds_var.get(),
            'keep_structure': self.keep_var.get(), 'inc_tex': self.inc_tex.get(),
            'inc_text': self.inc_text.get(), 'inc_arc': self.inc_arc.get(), 'inc_other': self.inc_other.get(),
            'theme': 'dark' if self.dark_var.get() else 'light',
            'inc_model': self.inc_model.get(), 'inc_data': self.inc_data.get(), 'inc_audio': self.inc_audio.get(),
            'gltf': self.gltf_var.get(), 'gltf_anims': self.gltf_anims_var.get(), 'json': self.json_var.get(),
            'wav': self.wav_var.get(), 'mod': self.mod_var.get(), 'game_romfs': self.game_romfs_var.get(),
            'pkg_out': self.pkg_out_var.get(), 'targets': [k for k, v in self.tgt_vars.items() if v.get()],
            'recomp': self.recomp_var.get(), 'text_lang': self.text_lang.get(),
        })
        self.settings = s
        try:
            with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
                json.dump(s, f, indent=2)
        except OSError:
            pass

    # ---------------- updates ----------------
    def check_updates(self):
        """Background thread at startup: is there a newer release on GitHub? (quiet if offline)"""
        try:
            import za_update
            rel = za_update.check()
        except Exception:  # noqa: BLE001 - offline, rate-limited, repository not public...
            return
        self.q.put(lambda: self.update_found(rel))

    def update_found(self, rel):
        """Show 'Update to X' on the top bar when rel is newer than this version and not skipped."""
        import za_update
        new = (rel is not None and za_update.is_newer(rel['version'])
               and rel['version'] != self.settings.get('skip_version'))
        self.update_rel = rel if new else None
        self.update_btn.configure(text='★ Update to %s' % rel['version'] if new else 'Updates…')
        if new:
            self.status_var.set('Lumiose Toolkit %s is available - click "Update to %s" at the bottom right.'
                                % (rel['version'], rel['version']))

    def open_updates(self):
        import za_update
        w = za_update.UpdateWindow(self, self.update_rel)
        if self.update_rel is None:
            w.recheck()

    def theme_colours(self):
        return THEMES['dark' if self.dark_var.get() else 'light']

    def extract_busy(self):
        return ['the extraction'] if self.extracting else []

    def on_close(self):
        self.stop_embedded()
        self.save_settings()
        self.cancel_flag.set()
        self.destroy()

    def on_tk_error(self, exc, val, tb):
        text = ''.join(traceback.format_exception(exc, val, tb))
        log_error(text)
        if messagebox.askyesno('Lumiose Toolkit - error',
                               '%s\n\nThe details were saved to error_log.txt.\n\nReport this bug?' % val,
                               icon='error', default='no'):
            self.open_report(extra='%s: %s' % (exc.__name__, val))

    def offer_repair(self, found):
        """Ask once to restore values that versions up to 1.3.1 dropped from rebuilt files."""
        lines = ['• %s: %s' % (m.name, rel) for m, rel, n in found[:10]]
        if len(found) > 10:
            lines.append('• … and %d more' % (len(found) - 10))
        ok = messagebox.askyesno(
            'Repair mod files',
            '%d file(s) in your mods were saved by an older version of Lumiose Toolkit and are missing '
            'values the game needs. This can crash the game (for example imported models).\n\n%s\n\n'
            'Repair them now? Nothing else in the files changes, and the old versions stay in History.'
            % (len(found), '\n'.join(lines)), icon='warning')
        if not ok:
            self.settings['repair_defaults_done'] = True     # don't ask again
            self.save_settings()
            return
        mods = {id(m): m for m, rel, n in found}
        for m in mods.values():
            m.snapshot()
        fixed, failed = 0, []
        for m, rel, n in found:
            try:
                zm.repair_dropped_defaults(m.path_for(rel), os.path.join(self.root_var.get(), *rel.split('/')))
                fixed += 1
            except Exception:  # noqa: BLE001
                log_error(traceback.format_exc())
                failed.append(rel)
        for m in mods.values():
            m.snapshot('repaired (missing values restored)')
        if not failed:
            self.settings['repair_defaults_done'] = True
            self.save_settings()
        self.status_var.set('Repaired %d mod file(s).%s' % (fixed, (' Could not repair: ' + ', '.join(failed)) if failed else ''))
        self.refresh_mods()

    def open_whats_new(self, since=None, after_update=False):
        try:
            import za_whatsnew
            za_whatsnew.WhatsNewWindow(self, since=since, after_update=after_update)
        except Exception:  # noqa: BLE001
            log_error(traceback.format_exc())

    def open_report(self, extra=None):
        try:
            import za_report
            za_report.ReportWindow(self, extra=extra)
        except Exception:  # noqa: BLE001
            log_error(traceback.format_exc())
            import webbrowser
            webbrowser.open('https://github.com/%s/issues/new/choose' % za_version.REPO)

    # ---------------- theme ----------------
    def apply_theme(self):
        dark = self.dark_var.get()
        c = THEMES['dark' if dark else 'light']
        s = self.style
        s.configure('.', background=c['bg'], foreground=c['fg'], fieldbackground=c['field'],
                    bordercolor=c['border'], darkcolor=c['bg'], lightcolor=c['bg'], troughcolor=c['panel'],
                    focuscolor=c['accent'], selectbackground=c['sel'], selectforeground=c['selfg'],
                    insertcolor=c['fg'], arrowcolor=c['fg'], font=('Segoe UI', 9))
        s.map('.', foreground=[('disabled', c['disabled'])])
        s.configure('Dim.TLabel', foreground=c['dim'])
        s.configure('Head.TLabel', font=('Segoe UI', 10, 'bold'))
        s.configure('TNotebook', background=c['bg'], bordercolor=c['border'], tabmargins=(2, 4, 2, 0))
        s.configure('TNotebook.Tab', background=c['panel'], foreground=c['fg'], padding=(10, 4), bordercolor=c['border'])
        s.map('TNotebook.Tab', background=[('selected', c['bg']), ('active', c['hover'])],
              foreground=[('selected', c['accent'])])
        s.configure('TLabelframe', background=c['bg'], bordercolor=c['border'])
        s.configure('TLabelframe.Label', background=c['bg'], foreground=c['fg'])
        s.configure('TButton', background=c['panel'], padding=(8, 3), bordercolor=c['border'])
        s.map('TButton', background=[('pressed', c['sel']), ('active', c['hover']), ('disabled', c['bg'])])
        for w in ('TCheckbutton', 'TRadiobutton'):
            s.configure(w, background=c['bg'], indicatorbackground=c['field'], indicatorforeground=c['fg'],
                        upperbordercolor=c['border'], lowerbordercolor=c['border'])
            s.map(w, background=[('active', c['bg'])], indicatorbackground=[('pressed', c['hover']),
                                                                             ('selected', c['field'])])
        s.configure('TEntry', fieldbackground=c['field'], foreground=c['fg'], insertcolor=c['fg'])
        s.configure('TCombobox', fieldbackground=c['field'], background=c['panel'], foreground=c['fg'])
        s.map('TCombobox', fieldbackground=[('readonly', c['field'])], foreground=[('readonly', c['fg'])],
              selectbackground=[('readonly', c['field'])], selectforeground=[('readonly', c['fg'])],
              background=[('active', c['hover'])])
        s.configure('TSpinbox', fieldbackground=c['field'], background=c['panel'], foreground=c['fg'])
        s.map('TSpinbox', fieldbackground=[('readonly', c['field'])], foreground=[('readonly', c['fg'])],
              selectbackground=[('readonly', c['field'])], selectforeground=[('readonly', c['fg'])])
        s.configure('Treeview', background=c['field'], fieldbackground=c['field'], foreground=c['fg'],
                    bordercolor=c['border'], rowheight=22)
        s.map('Treeview', background=[('selected', c['sel'])], foreground=[('selected', c['selfg'])])
        s.configure('Treeview.Heading', background=c['panel'], foreground=c['fg'], relief='flat',
                    bordercolor=c['border'])
        s.map('Treeview.Heading', background=[('active', c['hover'])])
        s.configure('TScrollbar', background=c['panel'], troughcolor=c['bg'], bordercolor=c['bg'],
                    arrowcolor=c['fg'])
        s.map('TScrollbar', background=[('active', c['hover'])])
        s.configure('TProgressbar', background=c['accent'], troughcolor=c['panel'], bordercolor=c['border'])
        s.configure('TPanedwindow', background=c['border'])
        s.configure('Sash', sashthickness=5)
        s.configure('TSeparator', background=c['border'])
        # classic tk widgets
        self.configure(background=c['bg'])
        self.option_add('*TCombobox*Listbox.background', c['field'])
        self.option_add('*TCombobox*Listbox.foreground', c['fg'])
        self.option_add('*TCombobox*Listbox.selectBackground', c['sel'])
        self.option_add('*TCombobox*Listbox.selectForeground', c['selfg'])
        self.text.configure(background=c['field'], foreground=c['fg'], insertbackground=c['fg'],
                            selectbackground=c['sel'], selectforeground=c['selfg'], borderwidth=0,
                            highlightthickness=0)
        self.canvas.configure(background=c['canvas'])
        for w in (getattr(self, n, None) for n in ('audio_list', 'mods_list', 'order_list')):
            if w is not None:
                w.configure(background=c['field'], foreground=c['fg'], selectbackground=c['sel'],
                            selectforeground=c['selfg'])
        for w in (getattr(self, n, None) for n in ('mod_desc', 'build_log', 'map_info')):
            if w is not None:
                w.configure(background=c['field'], foreground=c['fg'], insertbackground=c['fg'],
                            selectbackground=c['sel'], selectforeground=c['selfg'], highlightthickness=0)
        if hasattr(self, 'map_canvas'):
            self.map_canvas.configure(background=c['canvas'])
            self.draw_map()
        if hasattr(self, 'data_ui'):          # Game data tab: the scrolling form area follows the theme
            self.data_ui.canvas.configure(background=c['bg'])
        if hasattr(self, 'audio_ui'):
            self.audio_ui.recolour()
        self.menu.configure(background=c['panel'], foreground=c['fg'], activebackground=c['sel'],
                            activeforeground=c['selfg'], borderwidth=0)
        self.tree.tag_configure('name', foreground=c['accent'], font=('Segoe UI', 9, 'bold'))
        self.tree.tag_configure('header', foreground=c['dim'], font=('Segoe UI', 9, 'italic'))
        self._checker_cache.clear()
        self.after(10, lambda: self.set_titlebar(dark))
        self.render()

    def set_titlebar(self, dark, tries=0):
        """Ask Windows 10/11 to draw a dark (or light) title bar."""
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            val = ctypes.c_int(1 if dark else 0)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old Win10 builds)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val),
                                                             ctypes.sizeof(val)) == 0:
                    break
            # nudge the window so the title bar repaints - only once it is on screen: before that its size
            # reads as 1x1 and the nudge would shrink the window to its minimum size
            if not self.winfo_ismapped() or self.winfo_width() < 100:
                if tries < 40:
                    self.after(150, lambda: self.set_titlebar(dark, tries + 1))
                return
            w, h = self.winfo_width(), self.winfo_height()
            self.geometry('%dx%d' % (w + 1, h))
            self.after(20, lambda: self.geometry('%dx%d' % (w, h)))
        except Exception:  # noqa: BLE001 - purely cosmetic
            pass

    # ---------------- layout ----------------
    def build_ui(self):
        pad = dict(padx=4, pady=3)

        top = ttk.Frame(self)
        top.pack(fill='x', **pad)
        ttk.Label(top, text='Game folder:').pack(side='left')
        ttk.Entry(top, textvariable=self.root_var, width=30).pack(side='left', padx=3)
        ttk.Button(top, text='Browse…', command=self.pick_root).pack(side='left')
        ttk.Button(top, text='Reload', command=self.reload_root).pack(side='left', padx=(3, 3))
        ttk.Button(top, text='Import game files…', command=self.import_game_files).pack(side='left', padx=(0, 12))
        ttk.Label(top, text='Output:').pack(side='left')
        ttk.Entry(top, textvariable=self.out_var, width=30).pack(side='left', padx=3)
        ttk.Button(top, text='Browse…', command=self.pick_out).pack(side='left')
        ttk.Button(top, text='Open', command=self.open_out).pack(side='left', padx=(3, 12))
        ttk.Label(top, text='Current mod:').pack(side='left')
        self.mod_combo = ttk.Combobox(top, textvariable=self.mod_var, state='readonly', width=22)
        self.mod_combo.pack(side='left', padx=3)
        ttk.Button(top, text='New mod…', command=self.new_mod).pack(side='left')
        ttk.Checkbutton(top, text='Dark mode', variable=self.dark_var,
                        command=self.apply_theme).pack(side='right', padx=6)

        stat = ttk.Frame(self)
        stat.pack(side='bottom', fill='x', padx=4, pady=(0, 4))
        self.progress = ttk.Progressbar(stat, mode='determinate', length=260)
        self.progress.pack(side='right')
        self.update_btn = ttk.Button(stat, text='Updates…', command=self.open_updates)
        self.update_btn.pack(side='right', padx=(0, 8))
        ttk.Button(stat, text='Preferences…', command=self.open_preferences).pack(side='right', padx=(0, 4))
        ttk.Button(stat, text='Report a bug…', command=self.open_report).pack(side='right', padx=(0, 4))
        ttk.Button(stat, text='Patch notes', command=self.open_whats_new).pack(side='right', padx=(0, 4))
        self.update_rel = None          # newer release found on GitHub (see check_updates)
        ttk.Label(stat, textvariable=self.status_var).pack(side='left')

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill='both', expand=True, padx=4, pady=(2, 0))
        B = ttk.Frame(self.nb)
        self.nb.add(B, text='  Browse & extract  ')
        self.mods_tab = ttk.Frame(self.nb)
        self.nb.add(self.mods_tab, text='  Mods  ')
        self.map_tab = ttk.Frame(self.nb)
        self.nb.add(self.map_tab, text='  Map  ')
        self.poke_tab = ttk.Frame(self.nb)
        self.nb.add(self.poke_tab, text='  Pokémon data  ')
        self.data_tab = ttk.Frame(self.nb)
        self.nb.add(self.data_tab, text='  Game data  ')
        self.audio_tab = ttk.Frame(self.nb)
        self.nb.add(self.audio_tab, text='  Audio  ')

        srch = ttk.Frame(B)
        srch.pack(fill='x', **pad)
        ttk.Label(srch, text='Search:').pack(side='left')
        ent = ttk.Entry(srch, textvariable=self.search_var, width=50)
        ent.pack(side='left', padx=3)
        ent.bind('<Return>', lambda e: self.run_search())
        self.search_var.trace_add('write', lambda *a: self.schedule_search())
        ttk.Button(srch, text='Clear', command=lambda: self.search_var.set('')).pack(side='left')
        ttk.Label(srch, text='  in:').pack(side='left')
        mc = ttk.Combobox(srch, textvariable=self.search_mode, values=['Files & names', 'Game text'],
                          state='readonly', width=13)
        mc.pack(side='left', padx=3)
        mc.bind('<<ComboboxSelected>>', lambda e: self.run_search())
        self.lang_combo = ttk.Combobox(srch, textvariable=self.text_lang, state='readonly', width=12,
                                       values=self.text_languages())
        self.lang_combo.pack(side='left')
        self.lang_combo.bind('<<ComboboxSelected>>', lambda e: (setattr(self, 'text_index', None), self.run_search()))
        ttk.Label(srch, text='   Show:').pack(side='left')
        cb = ttk.Combobox(srch, textvariable=self.filter_var, values=list(TYPE_FILTERS), state='readonly', width=20)
        cb.pack(side='left', padx=3)
        cb.bind('<<ComboboxSelected>>', lambda e: self.on_filter_change())
        ttk.Label(srch, textvariable=self.index_var, style='Dim.TLabel').pack(side='left', padx=10)

        paned = ttk.PanedWindow(B, orient='horizontal')
        paned.pack(fill='both', expand=True, **pad)

        # --- file tree
        left = ttk.Frame(paned)
        self.tree = ttk.Treeview(left, columns=('size', 'type', 'about'), selectmode='extended')
        self.tree.heading('#0', text='Name')
        self.tree.heading('size', text='Size')
        self.tree.heading('type', text='Type')
        self.tree.heading('about', text='About')
        self.tree.column('about', width=260, stretch=True)
        self.tree.column('#0', width=380, stretch=True)
        self.tree.column('size', width=80, anchor='e', stretch=False)
        self.tree.column('type', width=85, stretch=False)
        ysb = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        xsb = ttk.Scrollbar(left, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        ysb.grid(row=0, column=1, sticky='ns')
        xsb.grid(row=1, column=0, sticky='ew')
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.tree.bind('<<TreeviewOpen>>', self.on_tree_open)
        self.tree.bind('<<TreeviewSelect>>', self.on_tree_select)
        self.tree.bind('<Button-3>', self.on_tree_menu)
        self.tree.bind('<Control-e>', lambda e: self.start_extract())
        self.tree.bind('<Control-a>', lambda e: self.select_all_visible())
        paned.add(left, weight=2)

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label='Extract selected', command=self.start_extract)
        self.menu.add_command(label='Add original file(s) to current mod', command=self.add_selected_to_mod)
        self.menu.add_command(label='Show in Explorer', command=self.show_in_explorer)
        self.menu.add_command(label='Copy path', command=self.copy_path)

        # --- preview
        right = ttk.Frame(paned)
        paned.add(right, weight=3)
        self.info_var = tk.StringVar(value='Select a file to preview it.')
        ttk.Label(right, textvariable=self.info_var, justify='left', wraplength=780).pack(fill='x', padx=4, pady=(2, 4))
        self.act_bar = ttk.Frame(right)          # per-file-type action buttons
        self.act_bar.pack(fill='x', padx=4, pady=(0, 4))

        self.tex_bar = ttk.Frame(right)
        row1 = ttk.Frame(self.tex_bar)
        row1.pack(fill='x')
        row2 = ttk.Frame(self.tex_bar)
        row2.pack(fill='x', pady=(2, 0))
        ttk.Label(row1, text='Texture:').pack(side='left')
        self.tex_combo = ttk.Combobox(row1, textvariable=self.tex_var, state='readonly', width=34)
        self.tex_combo.pack(side='left', padx=(2, 8))
        self.tex_combo.bind('<<ComboboxSelected>>', lambda e: self.on_tex_change())
        ttk.Label(row1, text='Layer:').pack(side='left')
        self.layer_spin = ttk.Spinbox(row1, from_=0, to=0, width=4, textvariable=self.layer_var,
                                      command=self.redecode, state='readonly')
        self.layer_spin.pack(side='left', padx=(2, 8))
        ttk.Label(row1, text='Mip:').pack(side='left')
        self.mip_spin = ttk.Spinbox(row1, from_=0, to=0, width=4, textvariable=self.mip_var,
                                    command=self.redecode, state='readonly')
        self.mip_spin.pack(side='left', padx=(2, 8))
        ttk.Label(row1, text='Zoom:').pack(side='left')
        zc = ttk.Combobox(row1, textvariable=self.zoom_var, values=ZOOMS, state='readonly', width=6)
        zc.pack(side='left', padx=2)
        zc.bind('<<ComboboxSelected>>', lambda e: self.render())
        ttk.Label(row2, text='Show:').pack(side='left')
        for v in ('RGBA', 'RGB', 'R', 'G', 'B', 'A'):
            ttk.Radiobutton(row2, text=v, value=v, variable=self.view_var,
                            command=self.render).pack(side='left')
        ttk.Checkbutton(row2, text='Rebuild normal-map blue', variable=self.nrm_var,
                        command=self.redecode).pack(side='left', padx=10)
        self.tex_orig_var = tk.BooleanVar(value=False)
        self.tex_orig_chk = ttk.Checkbutton(row2, text='Show game original', variable=self.tex_orig_var,
                                            command=lambda: self.refresh_preview(keep=True))
        ttk.Button(row2, text='Save view as PNG…', command=self.save_view).pack(side='left', padx=4)
        ttk.Button(row2, text='Image editor…', command=lambda: self.open_preferences('editor')).pack(side='left')

        self.stack = ttk.Frame(right)
        self.stack.pack(fill='both', expand=True)
        self.canvas_frame = ttk.Frame(self.stack)
        self.canvas = tk.Canvas(self.canvas_frame, background='#2b2b2b', highlightthickness=0)
        cy = ttk.Scrollbar(self.canvas_frame, orient='vertical', command=self.canvas.yview)
        cx = ttk.Scrollbar(self.canvas_frame, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=cy.set, xscrollcommand=cx.set)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        cy.grid(row=0, column=1, sticky='ns')
        cx.grid(row=1, column=0, sticky='ew')
        self.canvas_frame.rowconfigure(0, weight=1)
        self.canvas_frame.columnconfigure(0, weight=1)
        self.canvas.bind('<Configure>', lambda e: self.schedule_render())
        self.canvas.bind('<MouseWheel>', self.on_wheel)
        self.canvas.bind('<Motion>', self.on_canvas_motion)
        self.text = ScrolledText(self.stack, wrap='none', font=('Consolas', 10))
        self.text.configure(state='disabled')
        # 3D view: the viewer runs as its own process and docks into model_host (see show_model_mode)
        self.model_frame = ttk.Frame(self.stack)
        mbar = ttk.Frame(self.model_frame)
        mbar.pack(fill='x', pady=(0, 2))
        self.model_info_var = tk.StringVar()
        ttk.Label(mbar, textvariable=self.model_info_var, style='Dim.TLabel').pack(side='left')
        ttk.Button(mbar, text='Pop out (own window)', command=self.pop_out_viewer).pack(side='right')
        ttk.Button(mbar, text='Reload', command=lambda: self.embed_args and self.show_model_mode(*self.embed_args)
                   ).pack(side='right', padx=4)
        self.model_host = None
        self.embed_proc = None
        self.embed_args = None
        self.audio_frame = ttk.Frame(self.stack)
        self.audio_list = tk.Listbox(self.audio_frame, activestyle='none', font=('Consolas', 10), borderwidth=0,
                                     highlightthickness=0, selectmode='extended')
        asb = ttk.Scrollbar(self.audio_frame, orient='vertical', command=self.audio_list.yview)
        self.audio_list.configure(yscrollcommand=asb.set)
        self.audio_list.pack(side='left', fill='both', expand=True)
        asb.pack(side='right', fill='y')
        self.audio_list.bind('<Double-Button-1>', lambda e: self.play_audio())
        self.pixel_var = tk.StringVar(value='')
        ttk.Label(right, textvariable=self.pixel_var, style='Dim.TLabel').pack(fill='x', padx=4)

        # --- bottom bar (two rows)
        bot = ttk.Frame(B)
        bot.pack(fill='x', **pad)
        ttk.Label(bot, text='Convert:').pack(side='left')
        ttk.Checkbutton(bot, text='Textures → PNG', variable=self.png_var).pack(side='left')
        ttk.Checkbutton(bot, text='+ DDS', variable=self.dds_var).pack(side='left')
        ttk.Checkbutton(bot, text='Models → glTF', variable=self.gltf_var).pack(side='left', padx=(10, 0))
        ttk.Checkbutton(bot, text='with animations', variable=self.gltf_anims_var).pack(side='left')
        ttk.Checkbutton(bot, text='Data → JSON', variable=self.json_var).pack(side='left', padx=(10, 0))
        ttk.Checkbutton(bot, text='Audio → WAV', variable=self.wav_var).pack(side='left', padx=(10, 0))
        ttk.Checkbutton(bot, text='Keep folder structure', variable=self.keep_var).pack(side='left', padx=(16, 0))
        bot2 = ttk.Frame(B)
        bot2.pack(fill='x', padx=4, pady=(0, 3))
        ttk.Label(bot2, text='Folders include:').pack(side='left')
        for text, var in (('Textures', self.inc_tex), ('Text', self.inc_text), ('Archives', self.inc_arc),
                          ('Models', self.inc_model), ('Data', self.inc_data), ('Audio', self.inc_audio),
                          ('Other files (raw)', self.inc_other)):
            ttk.Checkbutton(bot2, text=text, variable=var).pack(side='left')
        self.cancel_btn = ttk.Button(bot2, text='Cancel', command=self.cancel_flag.set, state='disabled')
        self.cancel_btn.pack(side='right', padx=3)
        self.extract_btn = ttk.Button(bot2, text='Extract selected  (Ctrl+E)', command=self.start_extract)
        self.extract_btn.pack(side='right', padx=3)

        self.build_mods_tab()
        self.build_map_tab()
        try:
            import za_pokedit
            self.poke_ui = za_pokedit.PokemonTab(self, self.poke_tab)
        except Exception:  # noqa: BLE001
            log_error(traceback.format_exc())
            ttk.Label(self.poke_tab, text='Pokémon data tab could not start - see error_log.txt').pack(pady=20)
        try:
            import za_dataedit
            self.data_ui = za_dataedit.DataTab(self, self.data_tab)
            self.data_ui.canvas.configure(background=THEMES['dark' if self.dark_var.get() else 'light']['bg'])
        except Exception:  # noqa: BLE001
            log_error(traceback.format_exc())
            ttk.Label(self.data_tab, text='Game data tab could not start - see error_log.txt').pack(pady=20)
        try:
            import za_audiotab
            self.audio_ui = za_audiotab.AudioTab(self, self.audio_tab)
        except Exception:  # noqa: BLE001
            log_error(traceback.format_exc())
            ttk.Label(self.audio_tab, text='Audio tab could not start - see error_log.txt').pack(pady=20)
        self.refresh_mods()

        def fetch_pokedocs():         # files from pkZukan/PokeDocs are downloaded, not shipped (no licence)
            try:
                import za_deps
                if not za_deps.missing():
                    return
                self.q.put(lambda: self.status_var.set('Downloading data files from PokeDocs (first start only)…'))
                probs = za_deps.ensure(log=lambda m: None)
                msg = ('PokeDocs data files downloaded.' if not probs else
                       'Could not download some PokeDocs files (offline?) - the Pokémon data tab and clothing '
                       'prices need them. Run "python za_deps.py" later. ' + '; '.join(probs))
                self.q.put(lambda: self.status_var.set(msg))
            except Exception:  # noqa: BLE001
                log_error(traceback.format_exc())
        threading.Thread(target=fetch_pokedocs, daemon=True).start()

        if self.settings.get('check_updates', True):
            threading.Thread(target=self.check_updates, daemon=True).start()

        def find_installs():          # in the background: list ZA Recomp / Lumiose Online installs
            try:
                self._recomp_found = zm.find_recomp_installs()
            except Exception:  # noqa: BLE001
                self._recomp_found = []
            if self._recomp_found:            # only set it if the user has none (checked on the main thread)
                first = self._recomp_found[0]
                self.q.put(lambda: self.recomp_var.get() or self.recomp_var.set(first))
        threading.Thread(target=find_installs, daemon=True).start()

        # patch notes: once after each update
        try:
            import za_whatsnew
            show, since = za_whatsnew.startup_check(self.settings if self.started_with_settings else {})
            if self.settings.get('last_seen_version') != za_version.VERSION:
                self.settings['last_seen_version'] = za_version.VERSION
                self.save_settings()
            if show:
                self.after(900, lambda: self.open_whats_new(since=since, after_update=True))
        except Exception:  # noqa: BLE001
            log_error(traceback.format_exc())

        repair_root = self.root_var.get()          # read here: tkinter variables belong to this thread

        def check_mod_files():        # files rebuilt by 1.3.1 or older may miss values the game needs
            try:
                root = repair_root
                if root and os.path.isdir(root) and not self.settings.get('repair_defaults_done'):
                    found = zm.find_files_to_repair(root)
                    if found:
                        self.q.put(lambda: self.offer_repair(found))
            except Exception:  # noqa: BLE001
                log_error(traceback.format_exc())
        threading.Thread(target=check_mod_files, daemon=True).start()

        # first start (or a folder without game files): offer to import them
        try:
            import za_setup
            if not za_setup.looks_like_game_folder(self.root_var.get()):
                self.after(700, lambda: self.import_game_files(first_start=True))
        except Exception:  # noqa: BLE001
            log_error(traceback.format_exc())

        self.show_text_mode('Select a file on the left to preview it.\n\n'
                            'Tips:\n'
                            '  • Ctrl/Shift-click to select several files or folders, then Extract.\n'
                            '  • Selecting a folder extracts everything inside it (see "Folders include").\n'
                            '  • .arc UI archives can be expanded like folders.\n'
                            '  • Search by in-game name: a Pokémon ("pikachu", "25") or a clothing item\n'
                            '    ("v-neck t-shirt", "biker jacket"). Matches show as ★ groups at the top;\n'
                            '    small typos are fine. File/folder names are searched too, e.g.  p1 upb tshirt alb')

    # ---------------- Map tab (filled in by za_map) ----------------
    def build_map_tab(self):
        try:
            import za_map
            self.map_ui = za_map.MapTab(self, self.map_tab)
        except Exception:  # noqa: BLE001
            log_error(traceback.format_exc())
            ttk.Label(self.map_tab, text='Map tab could not start - see error_log.txt').pack(pady=20)

    def draw_map(self):
        if getattr(self, 'map_ui', None):
            self.map_ui.redraw()

    # ---------------- Mods tab ----------------
    def build_mods_tab(self):
        T = self.mods_tab
        self.order = []           # load order entries: ('mod', folder) or ('ext', path); top wins
        paned = ttk.PanedWindow(T, orient='horizontal')
        paned.pack(fill='both', expand=True, padx=4, pady=4)

        L = ttk.Frame(paned)
        paned.add(L, weight=1)
        ttk.Label(L, text='My mods', style='Head.TLabel').pack(anchor='w', pady=(0, 4))
        self.mods_list = tk.Listbox(L, exportselection=False, activestyle='none', borderwidth=0, highlightthickness=0)
        self.mods_list.pack(fill='both', expand=True)
        self.mods_list.bind('<<ListboxSelect>>', lambda e: self.show_mod())
        lb = ttk.Frame(L)
        lb.pack(fill='x', pady=4)
        ttk.Button(lb, text='New…', command=self.new_mod).pack(side='left')
        ttk.Button(lb, text='Make current', command=self.make_current).pack(side='left', padx=3)
        ttk.Button(lb, text='Open folder', command=lambda: self.selected_mod() and os.startfile(self.selected_mod().folder)).pack(side='left')
        ttk.Button(lb, text='Delete…', command=self.delete_mod).pack(side='right')

        M = ttk.Frame(paned)
        paned.add(M, weight=2)
        ttk.Label(M, text='Mod details', style='Head.TLabel').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 4))
        self.mod_fields = {}
        for r, (key, label) in enumerate((('name', 'Name'), ('author', 'Author'), ('version', 'Version')), start=1):
            ttk.Label(M, text=label + ':').grid(row=r, column=0, sticky='w', padx=(0, 4))
            v = tk.StringVar()
            ttk.Entry(M, textvariable=v).grid(row=r, column=1, sticky='ew', pady=1)
            self.mod_fields[key] = v
        ttk.Label(M, text='Description:').grid(row=4, column=0, sticky='nw')
        self.mod_desc = tk.Text(M, height=3, wrap='word', borderwidth=0, font=('Segoe UI', 9))
        self.mod_desc.grid(row=4, column=1, sticky='ew', pady=1)
        ttk.Button(M, text='Save details', command=self.save_mod_details).grid(row=5, column=1, sticky='w', pady=(2, 8))
        ttk.Label(M, text='Files in this mod (game paths):').grid(row=6, column=0, columnspan=2, sticky='w')
        self.mod_files = ttk.Treeview(M, show='tree', selectmode='extended')
        self.mod_files.grid(row=7, column=0, columnspan=2, sticky='nsew')
        mb = ttk.Frame(M)
        mb.grid(row=8, column=0, columnspan=2, sticky='ew', pady=4)
        ttk.Button(mb, text='Remove selected file(s)', command=self.remove_mod_files).pack(side='left')
        ttk.Button(mb, text='Show in Explorer', command=self.show_mod_file).pack(side='left', padx=3)
        ttk.Button(mb, text='History / compare / restore…', command=self.mod_history).pack(side='left')
        self.mod_files.bind('<Double-1>', lambda e: self.mod_history())
        ttk.Label(M, text='Add files from the Browse tab: select files → right-click → "Add original file(s) to current mod",\n'
                          'or use the Import buttons in a file\'s preview (PNG, JSON, text).',
                  style='Dim.TLabel', justify='left').grid(row=9, column=0, columnspan=2, sticky='w')
        M.columnconfigure(1, weight=1)
        M.rowconfigure(7, weight=1)

        R = ttk.Frame(paned)
        paned.add(R, weight=2)
        ttk.Label(R, text='Build a package', style='Head.TLabel').pack(anchor='w', pady=(0, 4))
        ttk.Label(R, text='Load order (top wins when two mods change the same file):').pack(anchor='w')
        of = ttk.Frame(R)
        of.pack(fill='both', expand=True)
        self.order_list = tk.Listbox(of, exportselection=False, activestyle='none', borderwidth=0,
                                     highlightthickness=0, height=8)
        self.order_list.pack(side='left', fill='both', expand=True)
        ob = ttk.Frame(of)
        ob.pack(side='left', fill='y', padx=(4, 0))
        for text, cmd in (('+ Selected mod', self.order_add_mod), ('+ Mod zip / folder…', self.order_add_ext),
                          ('Up', lambda: self.order_move(-1)), ('Down', lambda: self.order_move(1)),
                          ('Remove', self.order_remove)):
            ttk.Button(ob, text=text, command=cmd).pack(fill='x', pady=1)
        tf = ttk.Frame(R)
        tf.pack(fill='x', pady=(8, 2))
        ttk.Label(tf, text='Package for:').pack(anchor='w')
        for k, label in zm.TARGETS.items():
            ttk.Checkbutton(tf, text=label, variable=self.tgt_vars[k]).pack(anchor='w', padx=12)
        gf = ttk.Frame(R)
        gf.pack(fill='x', pady=2)
        ttk.Label(gf, text='Game romfs (for Switch/emulator):').pack(side='left')
        ttk.Entry(gf, textvariable=self.game_romfs_var).pack(side='left', fill='x', expand=True, padx=3)
        ttk.Button(gf, text='Browse…', command=lambda: self._pick_dir(self.game_romfs_var, 'Game romfs folder')).pack(side='left')
        pf = ttk.Frame(R)
        pf.pack(fill='x', pady=2)
        ttk.Label(pf, text='Save packages to:').pack(side='left')
        ttk.Entry(pf, textvariable=self.pkg_out_var).pack(side='left', fill='x', expand=True, padx=3)
        ttk.Button(pf, text='Browse…', command=lambda: self._pick_dir(self.pkg_out_var, 'Package folder')).pack(side='left')
        ttk.Button(pf, text='Open', command=lambda: (os.makedirs(self.pkg_out_var.get(), exist_ok=True),
                                                    os.startfile(self.pkg_out_var.get()))).pack(side='left', padx=3)
        bf = ttk.Frame(R)
        bf.pack(fill='x', pady=4)
        ttk.Button(bf, text='Check conflicts', command=self.check_conflicts).pack(side='left')
        ttk.Button(bf, text='Build package(s)', command=self.build_packages).pack(side='left', padx=4)
        rf = ttk.Frame(R)
        rf.pack(fill='x', pady=(2, 4))
        ttk.Label(rf, text='ZA Recomp / Lumiose Online folder:').pack(side='left')
        self.recomp_box = ttk.Combobox(rf, textvariable=self.recomp_var, width=22,
                                       postcommand=self.fill_recomp_installs)
        self.recomp_box.pack(side='left', fill='x', expand=True, padx=3)
        ttk.Button(rf, text='Browse…', command=lambda: self._pick_dir(self.recomp_var, 'ZA Recomp / Lumiose Online folder')
                   ).pack(side='left', padx=(0, 3))
        ttk.Button(rf, text='Install to Recomp', command=self.install_recomp).pack(side='left')
        ttk.Button(rf, text='▶ ZA Recomp', command=lambda: self.launch_game('lasrecomp.exe')).pack(side='left', padx=3)
        ttk.Button(rf, text='▶ Lumiose Online', command=lambda: self.launch_game('Lumiose Online.exe')).pack(side='left')
        self.build_log = ScrolledText(R, height=10, wrap='word', font=('Consolas', 9), borderwidth=0)
        self.build_log.pack(fill='both', expand=True)
        self.log_build('How to install:\n'
                       '  ZA Recomp / Lumiose Online: Mods page → install the zip (or drop it on the window).\n'
                       '  Switch: unzip onto the SD card root (creates atmosphere/contents/%s/romfs).\n'
                       '  Ryujinx: unzip into %%APPDATA%%\\Ryujinx\\mods\\contents\\ (other emulators: their mod/load folder).\n'
                       'Build one combined package for all the mods you use: the Switch/emulator index (data.trpfd)\n'
                       'is made for exactly that set of files, so separate packages would overwrite each other.\n'
                       % zm.TITLE_ID)

    def log_build(self, msg):
        self.build_log.insert('end', msg + '\n')
        self.build_log.see('end')

    def _pick_dir(self, var, title):
        d = filedialog.askdirectory(initialdir=var.get() or APP_DIR, title=title)
        if d:
            var.set(os.path.normpath(d))

    def refresh_mods(self):
        cm = self.current_mod(ask=False) if hasattr(self, 'mod_var') else None
        if cm:
            try:
                cm.snapshot()          # keep a version of every changed file (Mods tab > History)
            except Exception:  # noqa: BLE001
                log_error(traceback.format_exc())
        mods = zm.list_mods()
        names = [m.name for m in mods]
        self.mod_combo.configure(values=names)
        if self.mod_var.get() not in names:
            self.mod_var.set(names[0] if names else '')
        sel = self.mods_list.curselection()
        cur = self.mods_list.get(sel[0]) if sel else self.mod_var.get()
        self.mods_list.delete(0, 'end')
        for n in names:
            self.mods_list.insert('end', n)
        if cur in names:
            i = names.index(cur)
            self.mods_list.selection_set(i)
            self.mods_list.see(i)
        self.show_mod()
        self.refresh_order()

    def selected_mod(self):
        sel = self.mods_list.curselection()
        if not sel:
            return None
        name = self.mods_list.get(sel[0])
        return next((m for m in zm.list_mods() if m.name == name), None)

    def show_mod(self):
        m = self.selected_mod()
        for k, v in self.mod_fields.items():
            v.set(m.info.get(k, '') if m else '')
        self.mod_desc.delete('1.0', 'end')
        self.mod_files.delete(*self.mod_files.get_children())
        if not m:
            return
        self.mod_desc.insert('1.0', m.info.get('description', ''))
        for rel in m.files():
            self.mod_files.insert('', 'end', rel, text=rel)

    def new_mod(self):
        from tkinter import simpledialog
        name = simpledialog.askstring('New mod', 'Name for the new mod:', parent=self)
        if not name:
            return None
        try:
            m = zm.create_mod(name.strip())
        except zm.ModError as e:
            messagebox.showerror('Lumiose Toolkit', str(e))
            return None
        self.mod_var.set(m.name)
        self.refresh_mods()
        names = list(self.mods_list.get(0, 'end'))
        if m.name in names:
            self.mods_list.selection_clear(0, 'end')
            self.mods_list.selection_set(names.index(m.name))
            self.show_mod()
        self.status_var.set('Created mod "%s" - it is now the current mod.' % m.name)
        return m

    def make_current(self):
        m = self.selected_mod()
        if m:
            self.mod_var.set(m.name)
            self.status_var.set('Current mod: ' + m.name)

    def delete_mod(self):
        m = self.selected_mod()
        if not m:
            return
        n = len(m.files())
        if not messagebox.askyesno('Delete mod', 'Permanently delete the mod "%s" and its %d file(s)?\n\n%s\n\n'
                                   'This cannot be undone.' % (m.name, n, m.folder), icon='warning'):
            return
        shutil.rmtree(m.folder, ignore_errors=True)
        self.order = [e for e in self.order if not (e[0] == 'mod' and e[1] == m.folder)]
        self.refresh_mods()

    def save_mod_details(self):
        m = self.selected_mod()
        if not m:
            return
        old_name, old_folder = m.name, m.folder
        new_name = self.mod_fields['name'].get().strip()
        note = ''
        if new_name != old_name or zm.folder_name(new_name) != os.path.basename(old_folder):
            try:
                m = zm.rename_mod(m, new_name)
            except zm.ModError as e:
                messagebox.showerror('Rename mod', str(e))
                return
            # everything that points at the old folder or name follows the mod
            self.order = [('mod', m.folder) if e == ('mod', old_folder) else e for e in self.order]
            for e in self.live.values():
                if os.path.normcase(e['mod'].folder) == os.path.normcase(old_folder):
                    e['mod'] = m
            if self.mod_var.get() in (old_name, os.path.basename(old_folder)):
                self.mod_var.set(m.name)
            rd = self.recomp_var.get()
            if rd and os.path.isfile(os.path.join(rd, 'mods', zm.folder_name(old_name), 'za_mod_info.json')):
                note = (' Its copy installed in ZA Recomp is still called "%s" - install it again and remove the old '
                        'one on Recomp\'s Mods page.' % old_name)
        for k, v in self.mod_fields.items():
            m.info[k] = v.get().strip()
        m.info['description'] = self.mod_desc.get('1.0', 'end').strip()
        m.save()
        self.status_var.set('Saved details of "%s"%s.%s' % (
            m.name, (' (renamed from "%s", folder mods\\%s)' % (old_name, os.path.basename(m.folder)))
            if m.name != old_name else '', note))
        self.refresh_mods()
        names = list(self.mods_list.get(0, 'end'))
        if m.name in names:
            self.mods_list.selection_clear(0, 'end')
            self.mods_list.selection_set(names.index(m.name))
            self.mods_list.see(names.index(m.name))
            self.show_mod()

    def remove_mod_files(self):
        m = self.selected_mod()
        sel = self.mod_files.selection()
        if not m or not sel:
            return
        if not messagebox.askyesno('Lumiose Toolkit', 'Remove %d file(s) from "%s"? (Deletes the modded copies.)'
                                   % (len(sel), m.name)):
            return
        for rel in sel:
            m.remove(rel)
        self.show_mod()

    def mod_history(self):
        m = self.selected_mod()
        if not m:
            return
        sel = self.mod_files.selection()
        import za_history
        za_history.HistoryWindow(self, m, sel[0] if sel else None)

    def show_mod_file(self):
        m = self.selected_mod()
        sel = self.mod_files.selection()
        if m and sel:
            subprocess.Popen(['explorer', '/select,', os.path.normpath(m.path_for(sel[0]))])

    # load order
    def refresh_order(self):
        self.order_list.delete(0, 'end')
        live = {m.folder: m for m in zm.list_mods()}
        self.order = [e for e in self.order if e[0] == 'ext' or e[1] in live]
        for kind, ref in self.order:
            label = live[ref].name if kind == 'mod' else '%s  (external)' % os.path.basename(ref.rstrip('\\/'))
            self.order_list.insert('end', label)

    def order_add_mod(self):
        m = self.selected_mod() or self.current_mod(ask=False)
        if m and ('mod', m.folder) not in self.order:
            self.order.append(('mod', m.folder))
            self.refresh_order()

    def order_add_ext(self):
        p = filedialog.askopenfilename(title='Mod .zip (or Cancel to pick a folder)',
                                       filetypes=[('Zip', '*.zip'), ('All files', '*.*')])
        if not p:
            p = filedialog.askdirectory(title='Mod folder (must contain a romfs folder)')
        if p:
            try:
                ext = zm.ExternalMod(p)
            except zm.ModError as e:
                messagebox.showerror('Lumiose Toolkit', str(e))
                return
            if not ext.files():
                messagebox.showwarning('Lumiose Toolkit', 'No romfs folder found in that mod.')
                return
            self.order.append(('ext', p))
            self.refresh_order()

    def order_move(self, d):
        sel = self.order_list.curselection()
        if not sel:
            return
        i = sel[0]
        j = i + d
        if 0 <= j < len(self.order):
            self.order[i], self.order[j] = self.order[j], self.order[i]
            self.refresh_order()
            self.order_list.selection_set(j)

    def order_remove(self):
        sel = self.order_list.curselection()
        if sel:
            del self.order[sel[0]]
            self.refresh_order()

    def _order_mods(self):
        live = {m.folder: m for m in zm.list_mods()}
        out = []
        for kind, ref in self.order:
            out.append(live[ref] if kind == 'mod' else zm.ExternalMod(ref))
        if not out:
            m = self.selected_mod()
            if m:
                out = [m]
        return out

    def check_conflicts(self):
        """Window listing game files that more than one mod changes (own mods, load order, ZA Recomp)."""
        import za_conflicts
        za_conflicts.ConflictWindow(self)

    def _package_name(self, mods):
        if len(mods) == 1:
            return mods[0].name
        from tkinter import simpledialog
        return simpledialog.askstring('Package name', 'Name for the combined mod:', parent=self,
                                      initialvalue='My mod pack') or 'My mod pack'

    def install_recomp(self):
        mods = self._order_mods()
        if not mods:
            self.log_build('Add mods to the load order (or select one mod) first.')
            return
        self.save_settings()
        name = self._package_name(mods)
        rd = self.recomp_var.get()
        try:                                  # other mods already in Recomp that change the same files?
            import za_conflicts
            clash = za_conflicts.install_check(mods, rd, re.sub(r'[<>:"/\\|?*]+', '_', name).strip())
        except Exception:  # noqa: BLE001
            log_error(traceback.format_exc())
            clash = []
        if clash:
            lines = ['  %s  (also in %s)' % (rel, ', '.join(o.replace(' (in Recomp)', '') for o in others))
                     for rel, others in clash[:8]]
            if len(clash) > 8:
                lines.append('  … and %d more' % (len(clash) - 8))
            if not messagebox.askyesno(
                    'Mod conflicts', '%d file(s) in "%s" are also changed - differently - by other mods installed in '
                    'ZA Recomp:\n\n%s\n\nIn game, the mod higher on Recomp\'s Mods page wins. Install anyway?\n\n'
                    '("Check conflicts" in the Mods tab shows the details.)' % (len(clash), name, '\n'.join(lines)),
                    icon='warning'):
                self.log_build('Install cancelled: %d conflict(s) with installed mods.' % len(clash))
                return

        game_root = self.root_var.get()

        def job():
            return zm.install_recomp(mods, rd, name, log=lambda s: self.q.put(lambda s=s: self.log_build(s)),
                                     game_root=game_root)
        self.run_bg('Installing into ZA Recomp', job,
                    lambda d: self.log_build('Done. Start ZA Recomp / Lumiose Online - the mod "%s" is on its Mods page '
                                             '(order and on/off are set there).' % os.path.basename(d)))

    def fill_recomp_installs(self):
        """Fill the folder list with every ZA Recomp / Lumiose Online install found on the drives."""
        vals = list(getattr(self, '_recomp_found', None) or [])
        cur = self.recomp_var.get()
        if cur and cur not in vals:
            vals.insert(0, cur)
        self.recomp_box.configure(values=vals)

    def launch_game(self, exe):
        p = os.path.join(self.recomp_var.get(), exe)
        if not os.path.isfile(p):
            messagebox.showerror('Lumiose Toolkit', 'Not found: ' + p)
            return
        subprocess.Popen([p], cwd=self.recomp_var.get())
        self.status_var.set('Started ' + exe)

    def build_packages(self):
        mods = self._order_mods()
        targets = [k for k, v in self.tgt_vars.items() if v.get()]
        if not mods:
            self.log_build('Add mods to the load order (or select one mod) first.')
            return
        if not targets:
            self.log_build('Tick at least one "Package for" target.')
            return
        self.save_settings()
        out, romfs = self.pkg_out_var.get(), self.game_romfs_var.get()
        name = mods[0].name if len(mods) == 1 else None
        if name is None:
            from tkinter import simpledialog
            name = simpledialog.askstring('Package name', 'Name for the combined package:', parent=self,
                                          initialvalue='My mod pack') or 'My mod pack'
        self.log_build('Building "%s" from %d mod(s) for: %s' % (name, len(mods), ', '.join(zm.TARGETS[t] for t in targets)))

        game_root = self.root_var.get()

        def job():
            return zm.build_package(mods, out, targets, romfs, name,
                                    log=lambda s: self.q.put(lambda s=s: self.log_build(s)), game_root=game_root)
        self.run_bg('Building packages', job, lambda r: (self.log_build('Done: %d package(s) in %s' % (len(r[0]), out)),
                                                          os.startfile(out)))

    # ---------------- folder pickers ----------------
    def import_game_files(self, first_start=False):
        import za_setup
        za_setup.ImportWindow(self, first_start=first_start)

    def pick_root(self):
        d = filedialog.askdirectory(initialdir=self.root_var.get() or DEFAULT_ROOT, title='Choose the game export folder')
        if d:
            self.root_var.set(os.path.normpath(d))
            self.reload_root()

    def pick_out(self):
        d = filedialog.askdirectory(initialdir=self.out_var.get() or DEFAULT_OUT, title='Choose output folder')
        if d:
            self.out_var.set(os.path.normpath(d))

    def open_out(self):
        d = self.out_var.get()
        os.makedirs(d, exist_ok=True)
        os.startfile(d)

    # ---------------- tree ----------------
    def type_exts(self):
        return TYPE_FILTERS.get(self.filter_var.get())

    def file_visible(self, name):
        exts = self.type_exts()
        return exts is None or name.lower().endswith(exts)

    def reload_root(self, reindex=True):
        self.tree.delete(*self.tree.get_children())
        if not self.root_var.get().strip():
            self.status_var.set('No game folder yet - click "Import game files…" or Browse… at the top.')
            return
        root = os.path.normpath(self.root_var.get())
        if reindex:
            self.arc_cache.clear()
        if not os.path.isdir(root):
            self.status_var.set('Game folder not found: ' + root)
            return
        self.populate_dir('', root)
        if reindex:
            self.start_index(root)
            if self.search_var.get().strip():
                self.run_search()

    def populate_dir(self, parent, path, prefix=''):
        try:
            entries = list(os.scandir(path))
        except OSError as e:
            self.status_var.set(str(e))
            return
        entries.sort(key=lambda e: (not e.is_dir(), e.name.lower()))
        app = os.path.normcase(APP_DIR)
        for e in entries:
            p = os.path.normpath(e.path)
            if e.is_dir():
                if os.path.normcase(p) == app:
                    continue
                iid = prefix + 'D|' + p
                self.tree.insert(parent, 'end', iid, text=e.name, values=('', 'Folder'))
                self.tree.insert(iid, 'end', iid + '|dummy', text='…')
            elif self.file_visible(e.name):
                self.insert_file(parent, p, e.name, e.stat().st_size, prefix)

    def insert_file(self, parent, path, text, size, prefix=''):
        iid = prefix + 'F|' + path
        if self.tree.exists(iid):
            return
        ext = os.path.splitext(path)[1].lower()
        label = TYPE_LABELS.get(ext, ext.lstrip('.').upper())
        info = zn.unnamed_info(path) if ext == '.bin' else None
        if info:
            label = info['type'].split(' (')[0] + ' ?'
            text = '%s   (%s · %s)' % (text, info['type'], info['owner'])
        about = self.about(path) if ext == '.bntx' else ''
        self.tree.insert(parent, 'end', iid, text=text, values=(human_size(size), label, about))
        if ext == '.arc':
            self.tree.insert(iid, 'end', iid + '|dummy', text='…')

    @staticmethod
    def split_iid(iid):
        """'~3~D|path' -> ('~3~', 'D|path'); items under a name match carry a prefix to stay unique."""
        m = re.match(r'~\d+~', iid)
        return (m.group(0), iid[m.end():]) if m else ('', iid)

    def on_tree_open(self, event):
        iid = self.tree.focus()
        kids = self.tree.get_children(iid)
        if len(kids) == 1 and kids[0].endswith('|dummy'):
            self.tree.delete(kids[0])
            prefix, real = self.split_iid(iid)
            if real.startswith('N|'):
                self.populate_name(iid)
            elif real.startswith('D|'):
                self.populate_dir(iid, real[2:], prefix)
            elif real.startswith('F|'):
                self.populate_arc(iid, real[2:], prefix)

    def load_arc(self, path):
        if path not in self.arc_cache:
            if len(self.arc_cache) > 8:
                self.arc_cache.pop(next(iter(self.arc_cache)))
            with open(path, 'rb') as f:
                self.arc_cache[path] = dict(zf.parse_sarc(f.read()))
        return self.arc_cache[path]

    def populate_arc(self, iid, path, prefix=''):
        try:
            files = self.load_arc(path)
        except Exception as e:  # noqa: BLE001
            self.status_var.set('Could not open archive: %s' % e)
            return
        for name in sorted(files, key=str.lower):
            data = files[name]
            ext = os.path.splitext(name)[1].lower()
            label = 'Texture' if data[:4] == b'BNTX' else ext.lstrip('.').upper()
            self.tree.insert(iid, 'end', prefix + 'A|%s|%s' % (path, name), text=name,
                             values=(human_size(len(data)), label))

    def on_filter_change(self):
        if self.search_var.get().strip():
            self.run_search()
        else:
            self.reload_root(reindex=False)

    def select_all_visible(self):
        self.tree.selection_set(self.tree.get_children(''))
        return 'break'

    # ---------------- search ----------------
    def start_index(self, root):
        self.index = []
        self.index_ready = False
        self.index_var.set('Indexing files…')
        token = object()
        self._index_token = token

        def work():
            out, app = [], os.path.normcase(APP_DIR)
            buckets = {}
            pm_re = re.compile(r'pm\d{4}')
            model_re = re.compile(r'(?<![a-z])[a-z]{3}\d{4}_\d\d_[a-z0-9]+')
            for dp, dn, fn in os.walk(root):
                dn[:] = [d for d in dn if os.path.normcase(os.path.join(dp, d)) != app]
                for f in fn:
                    p = os.path.join(dp, f)
                    rel = os.path.relpath(p, root).lower()
                    out.append((rel, p))
                    for k in set(pm_re.findall(rel)) | set(model_re.findall(rel)):
                        buckets.setdefault(k, []).append((rel, p))
                if self._index_token is not token:
                    return
            names = zn.build(root)
            tex = {}
            try:                               # plain-English texture descriptions (search + About column)
                import za_texnames
                desc = za_texnames.Describer(root, names)
                for rel, p in out:
                    if rel.endswith('.bntx'):
                        d = desc.describe(rel)
                        if d:
                            tex[rel] = (d, desc.search_text(rel, d))
            except Exception:  # noqa: BLE001
                log_error(traceback.format_exc())
            self.q.put(lambda: self.index_done(token, out, buckets, names, tex))

        threading.Thread(target=work, daemon=True).start()

    def index_done(self, token, out, buckets, names, tex=None):
        if token is not self._index_token:
            return
        self.index = out
        self.buckets = buckets
        self.names = names
        self.tex_desc = tex or {}
        self.index_ready = True
        self.index_var.set('%d files indexed · %d Pokémon/clothing names' % (len(out), len(names)))
        if self.search_var.get().strip():
            self.run_search()

    def name_nodes(self, entry, exts):
        """Top-level folders/files that belong to a named Pokémon / clothing item."""
        root = os.path.normpath(self.root_var.get())
        pat = entry.pattern
        nodes, seen, count = [], set(), 0
        for rel, p in self.buckets.get(pat, []):
            if exts is not None and not rel.endswith(exts):
                continue
            count += 1
            parts = os.path.relpath(p, root).split(os.sep)
            i = next((j for j, s in enumerate(parts) if pat in s.lower()), len(parts) - 1)
            if i < len(parts) - 1:
                node = ('D', os.path.join(root, *parts[:i + 1]))
            else:
                node = ('F', os.path.normpath(p))
            if node not in seen:
                seen.add(node)
                nodes.append(node)
        nodes.sort(key=lambda n: (n[0] != 'D', n[1].lower()))
        return nodes, count

    def populate_name(self, iid):
        entry, nodes, count = self.name_hits[iid]
        prefix = '~%s~' % iid[2:]
        root = os.path.normpath(self.root_var.get())
        if entry.kind == 'Clothing' and getattr(entry, 'key', None):
            try:
                variants = zn.clothing_variants(root).get(entry.key, [])
            except Exception:  # noqa: BLE001
                variants = []
            if variants:
                gid = prefix + 'CV|' + entry.key
                if not self.tree.exists(gid):
                    self.tree.insert(iid, 'end', gid, text='Colour variants (%d)' % len(variants),
                                     values=('', 'In-game colours'))
                    for v in variants:
                        self.variant_by_id[v['id']] = v
                        self.tree.insert(gid, 'end', prefix + 'VI|%d' % v['id'],
                                         text='%s   (#%d%s)' % (zn.variant_label(v), v['id'],
                                                               ', $%d' % v['price'] if v['price'] else ''),
                                         values=('', 'Variant'))
        for kind, path in nodes:
            rel = os.path.relpath(path, root)
            if kind == 'D':
                did = prefix + 'D|' + path
                if not self.tree.exists(did):
                    self.tree.insert(iid, 'end', did, text=rel, values=('', 'Folder'))
                    self.tree.insert(did, 'end', did + '|dummy', text='…')
            else:
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0
                self.insert_file(iid, path, rel, size, prefix)

    def schedule_search(self):
        if self._search_after:
            self.after_cancel(self._search_after)
        self._search_after = self.after(350, self.run_search)

    def text_languages(self):
        d = os.path.join(self.root_var.get(), 'ik_message', 'dat')
        try:
            return sorted(x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x)))
        except OSError:
            return ['English']

    def build_text_index(self):
        if self.text_index_busy:
            return
        self.text_index_busy = True
        lang_dir = os.path.join(self.root_var.get(), 'ik_message', 'dat', self.text_lang.get())
        self.status_var.set('Reading all %s game text (first time only)…' % self.text_lang.get())

        def work():
            idx = []
            for dp, dn, fn in os.walk(lang_dir):
                for f in sorted(fn):
                    if not f.endswith('.dat'):
                        continue
                    p = os.path.join(dp, f)
                    try:
                        with open(p, 'rb') as fh:
                            d = fh.read()
                        if not zf.is_msg_dat(d):
                            continue
                        tp = p[:-4] + '.tbl'
                        labels = zf.parse_tbl(open(tp, 'rb').read()) if os.path.isfile(tp) else []
                        for i, t in enumerate(zf.decode_msg(d)):
                            if t:
                                idx.append((p, i, labels[i] if i < len(labels) else '#%d' % i, t, t.lower()))
                    except Exception:  # noqa: BLE001
                        continue

            def done():
                self.text_index, self.text_index_busy = idx, False
                self.status_var.set('%d lines of %s text indexed' % (len(idx), self.text_lang.get()))
                self.run_search()
            self.q.put(done)
        threading.Thread(target=work, daemon=True).start()

    def run_text_search(self, q):
        if self.text_index is None:
            self.build_text_index()
            return
        terms = q.split()
        root = os.path.normpath(self.root_var.get())
        self.tree.delete(*self.tree.get_children())
        n = 0
        for p, i, label, text, low in self.text_index:
            if all(t in low for t in terms):
                rel = os.path.relpath(p, os.path.join(root, 'ik_message', 'dat'))
                snippet = text if len(text) < 140 else text[:137] + '…'
                self.tree.insert('', 'end', 'T|%s|%d' % (p, i), text='%s  ·  %s' % (snippet.replace('\\n', ' '), label),
                                 values=(rel, 'Text line'))
                n += 1
                if n >= MAX_RESULTS:
                    break
        self.status_var.set('%d text line(s) contain "%s"%s' % (n, q, ' (first %d)' % MAX_RESULTS if n >= MAX_RESULTS else ''))

    def run_search(self):
        self._search_after = None
        q = self.search_var.get().strip().lower()
        if not q:
            self.reload_root(reindex=False)
            return
        if self.search_mode.get() == 'Game text':
            return self.run_text_search(q)
        if not self.index_ready:
            self.status_var.set('Still indexing - search will run when done.')
            return
        exts = self.type_exts()
        self.tree.delete(*self.tree.get_children())
        self.name_hits = {}

        # 1) in-game names (Pokémon, clothing)
        groups, unknown = [], []
        for entry in zn.search(self.names, q, limit=25):
            if not entry.pattern:
                unknown.append(entry)
                continue
            nodes, count = self.name_nodes(entry, exts)
            if count:
                groups.append((entry, nodes, count))
        for i, entry in enumerate(unknown[:5]):
            iid = 'U|%d' % i
            self.name_hits[iid] = (entry, [], 0)
            self.tree.insert('', 'end', iid, text='☆ ' + entry.label(), values=('?', 'Name match'),
                             tags=('header',))
        for i, (entry, nodes, count) in enumerate(groups):
            iid = 'N|%d' % i
            self.name_hits[iid] = (entry, nodes, count)
            self.tree.insert('', 'end', iid, text='★ ' + entry.label(),
                             values=('%d files' % count, 'Name match'), tags=('name',))
            self.tree.insert(iid, 'end', iid + '|dummy', text='…')
        if len(groups) == 1:
            self.tree.item('N|0', open=True)
            self.tree.delete('N|0|dummy')
            self.populate_name('N|0')

        # 2) file / folder paths, and what textures show (za_texnames descriptions)
        try:
            import za_texnames
            q_plain = za_texnames.plain(q)
        except Exception:  # noqa: BLE001
            q_plain = q
        terms = q_plain.replace('\\', '/').split()
        alts = [(t, t.replace('-', '').replace("'", '')) for t in terms]
        hits = []
        tex = getattr(self, 'tex_desc', {})
        for rel, p in self.index:
            r = rel.replace('\\', '/')
            if rel in tex:
                r = r + ' ' + tex[rel][1]
            if all(a in r or b in r for a, b in alts) and (exts is None or r.split(' ', 1)[0].endswith(exts)):
                hits.append((rel, p))
                if len(hits) >= MAX_RESULTS:
                    break
        root = os.path.normpath(self.root_var.get())
        if groups and hits:
            self.tree.insert('', 'end', 'H|paths', text='— files whose path contains "%s" —' % q,
                             values=('%d' % len(hits), ''), tags=('header',))
        for rel, p in hits:
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            self.insert_file('', os.path.normpath(p), os.path.relpath(p, root), size)
        more = ' (showing first %d)' % MAX_RESULTS if len(hits) >= MAX_RESULTS else ''
        msg = '%d file match(es)%s' % (len(hits), more)
        if groups:
            msg = '%d name match(es), ' % len(groups) + msg
        elif unknown and not hits:
            msg = 'Name found, but its model folder is unknown - click the ☆ row for details'
        elif not hits:
            msg = 'Nothing found for "%s"' % q
        self.status_var.set(msg)

    # ---------------- context menu ----------------
    def on_tree_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        if iid:
            self.menu.tk_popup(event.x_root, event.y_root)

    def _first_path(self):
        sel = self.tree.selection()
        if not sel:
            return None
        iid = self.split_iid(sel[0])[1]
        if iid.startswith('A|'):
            return iid[2:].split('|', 1)[0]
        if iid.startswith('N|'):
            nodes = self.name_hits[iid][1]
            return nodes[0][1] if nodes else None
        if iid.startswith(('H|', 'U|')):
            return None
        return iid[2:]

    def show_in_explorer(self):
        p = self._first_path()
        if p:
            subprocess.Popen(['explorer', '/select,', os.path.normpath(p)])

    def copy_path(self):
        p = self._first_path()
        if p:
            self.clipboard_clear()
            self.clipboard_append(p)
            self.status_var.set('Copied: ' + p)

    # ---------------- preview ----------------
    def item_bytes(self, iid):
        if iid.startswith('A|'):
            arc, name = iid[2:].split('|', 1)
            return name, self.load_arc(arc)[name]
        path = iid[2:]
        with open(path, 'rb') as f:
            return os.path.basename(path), f.read()

    def on_tree_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        tree_iid = self.tree.focus() if self.tree.focus() in sel else sel[0]
        iid = self.split_iid(tree_iid)[1]
        if len(sel) > 1:
            self.status_var.set('%d items selected' % len(sel))
        if iid.startswith('H|'):
            return
        if iid.startswith('T|'):                      # game-text hit: preview its file at that line
            p, line = iid[2:].rsplit('|', 1)
            self.pending_line = (p, int(line))
            iid = 'F|' + p
        if iid.startswith('U|'):
            self.preview_token += 1
            self.cur = None
            entry = self.name_hits[iid][0]
            self.info_var.set('%s   [%s · %s]' % (entry.name, entry.kind, entry.detail))
            self.show_text_mode(
                '%s - no model folder found.\n\n'
                'If the game\'s model catalog is in the export\n'
                '(ik_pokemon\\catalog\\catalog\\poke_resource_table.trpmcatalog), this Pokémon is not\n'
                'in Legends: Z-A, so it has no model.\n\n'
                'If the catalog is missing, Pokémon from Kalos onward (Dex #650-905) can\'t be mapped:\n'
                'they use development model numbers (e.g. Braixen #654 is pm0718). Restore the catalog\n'
                'with restore_missing.py and press Reload.' % entry.name)
            return
        if iid.startswith('N|'):
            self.preview_token += 1
            self.cur = None
            entry, nodes, count = self.name_hits[iid]
            root = os.path.normpath(self.root_var.get())
            self.info_var.set('%s   [%s · %s]' % (entry.name, entry.kind, entry.detail))
            lines = ['%s  -  %s (%s)' % (entry.name, entry.kind, entry.detail), '',
                     '%d file(s) belong to this, in:' % count, '']
            lines += ['  %s %s' % ('[folder]' if k == 'D' else '[file]  ', os.path.relpath(p, root))
                      for k, p in nodes]
            lines += ['', 'Expand it on the left to browse and preview the files,',
                      'or press "Extract selected" to extract all of them.']
            if entry.kind == 'Clothing':
                lines += ['', 'Folder prefixes: p1_ = male player model, p2_ = female player model,',
                          'p0_ = shared by both.  model_pc holds the 3D model + textures.']
            self.show_text_mode('\n'.join(lines))
            return
        if iid.startswith('CV|'):
            self.preview_token += 1
            self.cur = None
            self.info_var.set('Colour variants')
            self.show_text_mode('Every colour variant the game sells of this item, with its in-game colour names.\n\n'
                                'Expand the group and pick one: "Try on player body" and "Open 3D viewer" then show '
                                'exactly that variant, "Edit clothing colours..." changes the colours in your mod and '
                                '"Edit pattern textures..." opens the textures of its pattern for live editing.')
            self.set_actions(None)
            return
        if iid.startswith('VI|'):
            self.preview_token += 1
            v = self.variant_by_id.get(int(iid[3:]))
            if not v:
                return
            self.cur = {'kind': 'clothing_variant', 'variant': v}
            pat = 'Pattern %s' % v['set'][4:] if v['set'].startswith('tex_') else v['set'].title()
            self.info_var.set('%s   [clothing variant #%d]' % (zn.variant_label(v), v['id']))
            lines = ['%s' % zn.variant_label(v), '',
                     'Item id:       %d' % v['id'],
                     'Model folder:  ik_chara\\model_pc\\%s' % v['folder'],
                     'Pattern:       %s (%s)' % (pat, v['set']),
                     'Main colour:   %s' % (v['main_name'] or ('choice %d' % (v['main'] + 1) if v['main'] is not None else 'fixed')),
                     'Second colour: %s' % (v['second_name'] or ('choice %d' % (v['second'] + 1) if v['second'] is not None else 'fixed')),
                     'Price:         %s' % ('$%d' % v['price'] if v['price'] else 'not sold / free'),
                     '', 'Shop icon:     %s' % v['texturePath']]
            self.show_text_mode('\n'.join(lines))
            self.set_actions(self.cur)
            return
        if iid.startswith('D|'):
            self.preview_token += 1
            self.cur = None
            self.info_var.set('Folder: ' + iid[2:])
            self.show_text_mode('Folder selected.\n\nPress "Extract selected" to extract everything inside it '
                                '(recursively), using the "Folders include" options at the bottom.')
            return
        if iid.endswith('|dummy'):
            return
        self.preview_token += 1
        token = self.preview_token
        self.info_var.set('Loading…')
        rebuild_nrm = self.nrm_var.get()  # tk variables may only be read on the main thread
        mod_src = self.mod_copy_of(iid)    # (mod name, file in the mod) when the current mod has its own copy
        show_orig = self.tex_orig_var.get()

        def work():
            res = {'iid': iid}
            try:
                name, data = self.item_bytes(iid)
                res['name'], res['size'] = name, len(data)
                kind = zf.detect_kind(name, data)
                path = iid[2:] if iid.startswith('F|') else None
                ext = os.path.splitext(name)[1].lower()
                if kind == 'other' and path:
                    if ext == '.trmdl':
                        kind = 'model'
                    elif ext == '.tranm':
                        kind = 'anim'
                    elif ext in AUDIO_EXTS:
                        kind = 'audio'
                    elif zm.schema_for(path):
                        kind = 'data'
                res['kind'] = kind
                res['path'] = path
                if kind == 'model':
                    m = zt.Model(path)
                    res['text'] = m.summary()
                    res['anims'] = len(zt.related_animations(path)) if m.skeleton else 0
                elif kind == 'anim':
                    a = zt.Animation(path)
                    mp = zt.find_model_for(path)
                    res['model'] = mp
                    res['text'] = ('Skeletal animation  %s\n\n  %d frames at %d fps (%.2f s), %s\n  %d animated bones\n\n'
                                   'Model: %s\n\nUse "Play in 3D viewer" to watch it on its model.'
                                   % (a.name, a.frames, a.fps, a.duration, 'looping' if a.loop else 'plays once',
                                      len(a.tracks), mp or '(no model found in this or the parent folders)'))
                elif kind == 'audio':
                    res['streams'] = zm.audio_streams(path)
                elif kind == 'data':
                    js = zm.to_json(path)
                    res['text'] = js if len(js) < 400_000 else (
                        js[:400_000] + '\n\n… (truncated in the preview - use "Export JSON" for the full file)')
                elif kind == 'texture':
                    if mod_src:
                        res['mod_name'] = mod_src[0]
                        if not show_orig:
                            with open(mod_src[1], 'rb') as f:
                                mdata = f.read()
                            if iid.startswith('A|'):
                                mdata = dict(zf.parse_sarc(mdata)).get(iid[2:].split('|', 1)[1], data)
                            data, res['from_mod'] = mdata, True
                            res['size'] = len(data)
                    b = zf.Bntx(data)
                    res['bntx'] = b
                    t = b.textures[0]
                    if t.supported:
                        res['image'] = t.decode(0, 0, rebuild_normal_z=rebuild_nrm)
                elif kind == 'text':
                    tbl = None
                    if iid.startswith('F|'):
                        tp = os.path.splitext(iid[2:])[0] + '.tbl'
                        if os.path.isfile(tp):
                            with open(tp, 'rb') as f:
                                tbl = f.read()
                    res['text'] = zf.msg_to_text(data, tbl)
                elif kind == 'archive':
                    files = zf.parse_sarc(data)
                    lines = ['%-60s %10s  %s' % (n, human_size(len(d)), 'BNTX texture' if d[:4] == b'BNTX' else '')
                             for n, d in files]
                    res['text'] = ('SARC archive with %d files (expand it in the tree to preview them):\n\n'
                                   % len(files)) + '\n'.join(lines)
                else:
                    strs = zf.printable_strings(data)
                    head = 'No converter for this file type - it will be copied as-is when extracted.\n\n'
                    info = zn.unnamed_info(name)
                    if info:
                        head = ('UNNAMED FILE - the export only knows its path hash (%s), and that hash is\n'
                                'not in the PokeDocs Z-A hash lists, so its real name is unknown.\n\n'
                                'Identified from its contents as:\n'
                                '  Type : %s\n  For  : %s\n  Note : %s\n\n%s'
                                % (os.path.splitext(name)[0], info['type'], info['owner'], info['note'], head))
                    res['text'] = (head + 'Readable strings (first %d):\n  %s\n\nHex (first 4 KB):\n%s'
                                   % (len(strs), '\n  '.join(strs) or '(none)', zf.hexdump(data)))
            except Exception as e:  # noqa: BLE001
                res['error'] = '%s: %s' % (type(e).__name__, e)
                log_error(traceback.format_exc())
            self.q.put(lambda: self.show_preview(token, res))

        threading.Thread(target=work, daemon=True).start()

    def show_preview(self, token, res):
        if token != self.preview_token:
            return
        self.cur = res
        head = '%s   (%s)' % (res.get('name', ''), human_size(res.get('size', 0)))
        if 'error' in res:
            self.info_var.set(head)
            self.show_text_mode('Could not read this file:\n\n' + res['error'])
            return
        if res['kind'] == 'texture':
            b = res['bntx']
            names = ['%d: %s' % (i, t.name) for i, t in enumerate(b.textures)]
            self.tex_combo.configure(values=names)
            about = self.about(res.get('path')) if res.get('path') else ''
            if about:
                res['src_note'] = '   ·   ' + about
            if res.get('mod_name'):
                res['src_note'] = res.get('src_note', '') + (
                    '   ·   your mod "%s" version' % res['mod_name'] if res.get('from_mod') else
                    '   ·   game original (your mod "%s" has its own version)' % res['mod_name'])
                self.tex_orig_chk.pack(side='left', padx=(10, 0))
            else:
                self.tex_orig_chk.pack_forget()
            head += res.get('src_note', '')
            keep, self._keep_view = getattr(self, '_keep_view', None), None
            if keep and keep[0] == res['iid'] and keep[1] in names:      # refreshed after a save: same view
                self.tex_var.set(keep[1])
                self.update_tex_controls()
                self.layer_var.set(keep[2])
                self.mip_var.set(keep[3])
                self.show_image_mode()
                self.redecode()
                self.set_actions(res)
                return
            self.tex_var.set(names[0])
            self.layer_var.set(0)
            self.mip_var.set(0)
            self.update_tex_controls()
            self.info_var.set(head + '\n' + b.textures[0].describe())
            if 'image' in res:
                self.cur_img = res['image']
                self.show_image_mode()
                self.render()
            else:
                self.show_text_mode('This texture format (%s) is not supported for preview.\n'
                                    'It will be copied as raw .bntx when extracted.' % b.textures[0].format_name)
        elif res['kind'] == 'audio':
            self.info_var.set(head + '   [audio · %d sounds]' % len(res['streams']))
            self.show_audio_mode([label for label, src in res['streams']])
        elif res['kind'] == 'model' and res.get('path') and self.settings.get('auto_3d', True):
            first = (res.get('text') or '').strip().splitlines()
            self.info_var.set(head + '   [model]' + (('\n' + first[0]) if first else ''))
            self.show_model_mode(res['path'])
        else:
            self.info_var.set(head + '   [%s]' % res['kind'])
            self.show_text_mode(res['text'], wrap='none' if res['kind'] in ('other', 'archive', 'data', 'model')
                                else 'word')
            pl = self.pending_line
            if pl and res.get('path') and os.path.normcase(pl[0]) == os.path.normcase(res['path']):
                line = '%d.0' % (pl[1] + 1)
                res['hit_line'] = pl[1]
                self.text.tag_remove('hit', '1.0', 'end')
                self.text.tag_add('hit', line, '%s lineend' % line)
                self.text.tag_configure('hit', background=THEMES['dark' if self.dark_var.get() else 'light']['sel'])
                self.text.see(line)
        self.pending_line = None
        self.set_actions(res)

    # ---------------- per-file actions ----------------
    def set_actions(self, res):
        for w in self.act_bar.winfo_children():
            w.destroy()
        if not res or 'error' in res:
            return
        kind, path = res.get('kind'), res.get('path')
        acts = []
        if kind == 'clothing_variant':
            v = res['variant']
            acts += [('Try on player body (these colours)', lambda: self.open_clothing_variant(v, True, embed=self.embed_pref())),
                     ('Show in 3D (these colours)', lambda: self.open_clothing_variant(v, False, embed=self.embed_pref())),
                     ('Edit clothing colours…', lambda: self.edit_clothing_colours(self.clothing_model(v['folder']), v)),
                     ('Edit pattern textures…', lambda: self.edit_pattern_textures(self.clothing_model(v['folder']), v))]
        if kind == 'model':
            acts += [('Show in 3D', lambda: self.open_viewer(path, embed=True)),
                     ('Open 3D viewer window', lambda: self.open_viewer(path)),
                     ('Edit colours…', lambda: self.edit_materials(path)),
                     ('Export glTF (.glb)…', lambda: self.export_model(path, res.get('anims', 0))),
                     ('Import edited glTF into mod… (experimental)', lambda: self.import_model(path))]
            if '\\p1_' in path or '\\p2_' in path or '/p1_' in path or '/p2_' in path:
                acts.insert(1, ('Try on player body', lambda: self.try_on(path, embed=self.embed_pref())))
                try:
                    has_colours = bool(zt.clothing_sets(path))
                except Exception:  # noqa: BLE001
                    has_colours = False
                if has_colours:
                    acts.insert(4, ('Edit clothing colours…', lambda: self.edit_clothing_colours(path)))
                    acts.insert(5, ('Edit pattern textures…', lambda: self.edit_pattern_textures(path)))
        elif kind == 'anim':
            if res.get('model'):
                acts += [('Play in 3D', lambda: self.open_viewer(res['model'], path, embed=self.embed_pref())),
                         ('Play in 3D viewer window', lambda: self.open_viewer(res['model'], path)),
                         ('Export with model (.glb)…', lambda: self.export_model(res['model'], 0, [path]))]
        elif kind == 'data' and path.lower().endswith('.trmtr'):
            acts += [('Edit colours…', lambda: self.edit_materials(zt.find_model_for(path) or path, path))]
        elif kind == 'audio':
            acts += [('▶ Play', self.play_audio), ('■ Stop', self.stop_audio),
                     ('Export selected (.wav)…', lambda: self.export_audio(False)),
                     ('Export all (.wav)…', lambda: self.export_audio(True)),
                     ('Replace in Audio tab…', lambda: self.replace_audio(path))]
        elif kind == 'data':
            acts += [('Open in table editor', lambda: self.open_table_editor(path)),
                     ('Export JSON…', lambda: self.export_json(path)),
                     ('Import edited JSON into mod…', lambda: self.import_json(path))]
        elif kind == 'text' and path:
            acts += [('Edit text…', lambda: self.edit_text(path, res.get('hit_line'))),
                     ('Export text for editing…', lambda: self.export_text(path)),
                     ('Import edited text into mod…', lambda: self.import_text(path))]
        elif kind == 'texture' and path:
            acts += [('Import PNG into mod…', lambda: self.import_texture(path)),
                     ('Live edit in image editor', lambda: self.live_edit_texture(path))]
            mdl = zt.find_model_for(path) if '\ik_' in path or '/ik_' in path else None
            if mdl:
                acts += [('Show its model in 3D', lambda: self.open_viewer(mdl, embed=self.embed_pref())),
                         ('Its model in viewer window', lambda: self.open_viewer(mdl))]
        elif kind == 'texture' and res.get('iid', '').startswith('A|'):
            arc, inner = res['iid'][2:].split('|', 1)
            acts += [('Import PNG into mod…', lambda: self.import_texture(arc, inner)),
                     ('Live edit in image editor', lambda: self.live_edit_texture(arc, inner))]
        if kind == 'texture' and self.live:
            acts += [('Stop live edits (%d)' % len(self.live), self.stop_live_edits)]
        if path and self.rel_game_path(path):
            acts += [('Add original to mod', self.add_selected_to_mod)]
        for label, cmd in acts:
            ttk.Button(self.act_bar, text=label, command=cmd).pack(side='left', padx=(0, 4))

    def viewer_args(self, model_path, anim_path=None, with_mod=True, extra=None):
        args = viewer_command() + [model_path]
        if anim_path:
            args += ['--anim', anim_path]
        m = self.current_mod(ask=False) if with_mod else None
        if m:
            args += ['--mod', m.romfs, '--root', self.root_var.get()]
        if extra:
            args += extra
        if self.settings.get('viewer_texsize', 1024) in (2048, 4096):
            args += ['--texsize', str(self.settings['viewer_texsize'])]
        return args, m

    def open_viewer(self, model_path, anim_path=None, with_mod=True, extra=None, embed=False):
        """3D viewer for a model: in the Browse tab's preview pane (embed=True) or in its own window."""
        if embed:
            self.show_model_mode(model_path, anim_path, extra)
            return
        args, m = self.viewer_args(model_path, anim_path, with_mod, extra)
        subprocess.Popen(args, cwd=APP_DIR)
        self.status_var.set('Opening the 3D viewer%s…' % (' (with mod "%s")' % m.name if m else ''))

    # ---------------- 3D view in the preview pane ----------------
    def show_model_mode(self, model_path, anim_path=None, extra=None):
        """Show the 3D viewer inside the preview pane, where textures are shown."""
        self.stop_embedded()
        self.cur_img = None
        self.tex_bar.pack_forget()
        self.canvas_frame.pack_forget()
        self.text.pack_forget()
        self.audio_frame.pack_forget()
        self.pixel_var.set('')
        self.model_frame.pack(fill='both', expand=True)
        # a fresh host each time: a viewer still docked in the old one closes itself with it
        self.model_host = tk.Frame(self.model_frame, background='#1B1B1D', highlightthickness=0)
        self.model_host.pack(fill='both', expand=True)
        tk.Label(self.model_host, text='Loading 3D view…', background='#1B1B1D', foreground='#9a9a9a',
                 font=('Segoe UI', 10)).place(relx=0.5, rely=0.5, anchor='center')
        self.update_idletasks()
        args, m = self.viewer_args(model_path, anim_path, True, extra)
        self.embed_args = (model_path, anim_path, extra)
        what = 'Try-on: ' if extra and '--outfit' in extra else ''
        self.model_info_var.set('%s%s%s%s' % (what, os.path.basename(model_path),
                                              ('   ·   ' + os.path.basename(anim_path)) if anim_path else '',
                                              ('   ·   mod "%s"' % m.name) if m else ''))
        try:
            self.embed_proc = subprocess.Popen(args + ['--embed', str(self.model_host.winfo_id())], cwd=APP_DIR)
        except OSError as e:
            self.status_var.set('Could not start the 3D view: %s' % e)
            return
        self.status_var.set('Loading the 3D view%s…' % (' (with mod "%s")' % m.name if m else ''))
        self.after(300, self._watch_embed, self.model_host, self.embed_proc, os.path.basename(model_path), 0)

    def _watch_embed(self, host, proc, name, n):
        """Status bar: tell when the docked 3D view has appeared (or failed to start)."""
        if host is not self.model_host or proc is not self.embed_proc:
            return
        import ctypes
        u = ctypes.windll.user32
        u.FindWindowExW.restype = ctypes.c_void_p
        u.FindWindowExW.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p)
        u.GetWindowTextW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int)
        docked, child = False, None
        while True:                           # the viewer is the child titled "… 3D Viewer …"
            child = u.FindWindowExW(host.winfo_id(), child, None, None)
            if not child:
                break
            buf = ctypes.create_unicode_buffer(200)
            u.GetWindowTextW(child, buf, 200)
            if '3D Viewer' in buf.value:
                docked = True
                break
        if docked:
            self.status_var.set('3D view: %s - drag to orbit, scroll to zoom. "Pop out" opens it in its own window.'
                                % name)
            return
        if proc.poll() is not None:
            self.status_var.set('The 3D view closed unexpectedly - see error_log.txt, or use "Pop out".')
            return
        if n < 200:
            self.after(300, self._watch_embed, host, proc, name, n + 1)

    def hide_model_mode(self):
        if hasattr(self, 'model_frame') and (self.model_frame.winfo_ismapped() or self.embed_proc is not None):
            self.stop_embedded()
            self.model_frame.pack_forget()

    def stop_embedded(self):
        p, self.embed_proc = self.embed_proc, None
        if self.model_host is not None:
            self.model_host.destroy()          # the viewer notices its parent is gone and closes
            self.model_host = None
        if p is not None and p.poll() is None:
            try:
                p.kill()                       # nothing to save in the viewer; its browser parts exit with it
            except OSError:
                pass

    def embed_pref(self):
        """Preferences > 3D view: open Try on / Play / variants in the preview pane (True) or a window."""
        return self.settings.get('viewer_place', 'pane') != 'window'

    def pop_out_viewer(self):
        if self.embed_args:
            model_path, anim_path, extra = self.embed_args
            self.open_viewer(model_path, anim_path, extra=extra)

    def import_model(self, model_path):
        m = self.current_mod()
        if not m:
            return
        if not messagebox.askokcancel(
                'Import model (experimental)',
                'Replaces this model\'s meshes with the meshes of a glTF/GLB file.\n\n'
                '• Export this model first, edit it in Blender, export .glb again.\n'
                '• Keep the mesh object names, the armature and its bone names.\n'
                '• Materials are matched by name (new materials go into the first part).\n'
                '• Shape keys are dropped; all distance LODs show the new mesh.\n\n'
                'Check it in the 3D viewer (it shows the current mod) before trying it in-game.'):
            return
        p = filedialog.askopenfilename(title='Edited model for ' + os.path.basename(model_path),
                                       initialdir=self.out_var.get(), filetypes=[('glTF', '*.glb *.gltf')])
        if not p:
            return
        import za_import

        def done(report):
            self.mod_changed(m, os.path.basename(model_path), 'model imported')
            messagebox.showinfo('Model import', '\n'.join(report))
        self.run_bg('Importing model', lambda: za_import.import_gltf(p, model_path, m), done)

    def clothing_model(self, folder):
        """The .trmdl of a clothing model folder (ik_chara/model_pc/<folder>)."""
        d = os.path.join(self.root_var.get(), 'ik_chara', 'model_pc', folder)
        try:
            cands = sorted(f for f in os.listdir(d) if f.lower().endswith('.trmdl'))
        except OSError:
            return None
        stem = '_'.join(folder.split('_')[:3])                  # p2_upb0000_00
        best = next((c for c in cands if c.lower().startswith(stem.lower() + '.')), cands[0] if cands else None)
        return os.path.join(d, best) if best else None

    def new_clothing_item(self, src_type, price=None):
        """Game data > Clothing items > New item from this…: a new buyable item copied from this one."""
        try:
            import za_newitem
            za_newitem.open_dialog(self, src_type, price)
        except Exception as e:  # noqa: BLE001
            log_error(traceback.format_exc())
            messagebox.showerror('New clothing item', str(e))

    def open_clothing_variant(self, v, outfit=True, embed=False):
        """Open one shop variant (pattern + colours) in the 3D viewer, on the player or on its own."""
        model = self.clothing_model(v['folder'])
        if not model:
            messagebox.showerror('Lumiose Toolkit', 'Model folder not found: %s' % v['folder'])
            return
        extra = ['--pattern', v['set']]
        if v.get('main') is not None:
            extra += ['--main', str(v['main'])]
        if v.get('second') is not None:
            extra += ['--second', str(v['second'])]
        if outfit:
            extra.append('--outfit')
        self.open_viewer(model, extra=extra, embed=embed)

    def edit_clothing_colours(self, model_path, variant=None):
        if not model_path:
            messagebox.showerror('Lumiose Toolkit', 'No model found for this item.')
            return
        import za_colours
        try:
            za_colours.ClothingColourEditor(self, model_path, variant)
        except za_colours.NoColours as e:
            messagebox.showinfo('Lumiose Toolkit', str(e))

    def edit_pattern_textures(self, model_path, variant=None):
        """Textures of one clothing pattern (base, layer mask, palette, normal…) with live editing."""
        if not model_path:
            messagebox.showerror('Lumiose Toolkit', 'No model found for this item.')
            return
        import za_colours
        za_colours.PatternTextureWindow(self, model_path, variant)

    def try_on(self, model_path, embed=False):
        self.open_viewer(model_path, extra=['--outfit'], embed=embed)

    def edit_materials(self, model_path, trmtr=None):
        import za_mated
        if trmtr is None:
            mats = zt.material_variants(model_path)
            stem = os.path.splitext(model_path)[0]
            trmtr = stem + '.trmtr'
            if len(mats) > 1:
                from tkinter import simpledialog
                labels = list(mats)
                pick = simpledialog.askstring('Material set', 'Which material set? (%s)' % ', '.join(labels),
                                              initialvalue='Normal', parent=self)
                if pick is None:
                    return
                match = next((l for l in labels if l.lower() == pick.strip().lower()), 'Normal')
                trmtr = mats[match] or trmtr
            if not os.path.isfile(trmtr):
                m = zt.Model(model_path)
                trmtr = m.material_paths[0] if m.material_paths else None
        if not trmtr or not os.path.isfile(trmtr):
            messagebox.showerror('Lumiose Toolkit', 'No material file found for this model.')
            return
        if not self.rel_game_path(trmtr):
            messagebox.showerror('Lumiose Toolkit', 'The material file is not inside the game folder.')
            return
        za_mated.MaterialEditor(self, trmtr, model_path if model_path.lower().endswith('.trmdl') else None)

    def open_table_editor(self, path):
        if not hasattr(self, 'data_ui'):
            return
        self.nb.select(self.data_tab)
        self.data_ui._started = True
        self.data_ui.open_path(path)

    def about(self, path):
        """Plain-English description of a game texture ('' if none)."""
        tex = getattr(self, 'tex_desc', None)
        if not tex or not path:
            return ''
        try:
            rel = os.path.relpath(os.path.abspath(path), os.path.abspath(self.root_var.get())).lower()
        except ValueError:
            return ''
        return tex.get(rel, ('',))[0]

    def mod_copy_of(self, iid):
        """(mod name, path) when the current mod has its own copy of the previewed file (or of the archive a
        previewed texture is in), else None. Main thread only (reads tkinter variables)."""
        if iid.startswith('F|'):
            path = iid[2:]
        elif iid.startswith('A|'):
            path = iid[2:].split('|', 1)[0]
        else:
            return None
        m = self.current_mod(ask=False)
        rel = self.rel_game_path(path) if m else None
        if rel and os.path.isfile(m.path_for(rel)):
            return m.name, m.path_for(rel)
        return None

    def refresh_preview(self, keep=False, only_iid=None):
        """Preview the selected file again (e.g. after the mod's copy changed); keep=True keeps the texture,
        layer and mip shown. only_iid: only when that file is the one being previewed."""
        cur = (self.cur or {}).get('iid')
        if not cur or (only_iid and cur != only_iid):
            return
        sel = self.tree.selection()
        if not sel or self.split_iid(self.tree.focus() if self.tree.focus() in sel else sel[0])[1] != cur:
            return
        if keep and self.cur.get('kind') == 'texture':
            self._keep_view = (cur, self.tex_var.get(), self.layer_var.get(), self.mip_var.get())
        self.on_tree_select()

    def rel_game_path(self, path):
        root = os.path.normcase(os.path.abspath(self.root_var.get()))
        p = os.path.abspath(path)
        if not os.path.normcase(p).startswith(root + os.sep):
            return None
        return os.path.relpath(p, root).replace(os.sep, '/')

    def current_mod(self, ask=True):
        name = self.mod_var.get()
        for m in zm.list_mods():
            if m.name == name or os.path.basename(m.folder) == name:
                return m
        if ask:
            if messagebox.askyesno('Lumiose Toolkit', 'No mod is selected. Create a new mod now?'):
                return self.new_mod()
        return None

    def run_bg(self, label, fn, done=None):
        """Run fn() on a worker thread; show status; call done(result) on the main thread."""
        self.status_var.set(label + '…')

        def work():
            try:
                r = fn()
                self.q.put(lambda: (self.status_var.set(label + ' - done'), done(r) if done else None))
            except Exception as e:  # noqa: BLE001
                log_error(traceback.format_exc())
                msg = str(e)
                self.q.put(lambda: (self.status_var.set(label + ' - failed'),
                                    messagebox.showerror('Lumiose Toolkit', '%s failed:\n\n%s' % (label, msg))))
        threading.Thread(target=work, daemon=True).start()

    def export_model(self, model_path, n_anims, anim_paths=None):
        base = os.path.splitext(os.path.basename(model_path))[0]
        p = filedialog.asksaveasfilename(defaultextension='.glb', initialfile=base + '.glb',
                                         initialdir=self.out_var.get(), filetypes=[('glTF binary', '*.glb')])
        if not p:
            return
        if anim_paths is None:
            anim_paths = []
            if n_anims and messagebox.askyesno('Lumiose Toolkit', 'Include all %d animations of this model?\n'
                                               '(Makes the file larger; Blender imports each as an action.)'
                                               % n_anims):
                anim_paths = zt.related_animations(model_path)

        def job():
            m = zt.Model(model_path)
            return zt.export_glb(m, [zt.Animation(a) for a in anim_paths], p)
        self.run_bg('Exporting ' + os.path.basename(p), job,
                    lambda r: self.status_var.set('Saved %s (%s)' % (p, human_size(len(r)))))

    # audio
    def show_audio_mode(self, labels):
        self.hide_model_mode()
        self.cur_img = None
        self.tex_bar.pack_forget()
        self.canvas_frame.pack_forget()
        self.text.pack_forget()
        self.audio_list.delete(0, 'end')
        for i, l in enumerate(labels):
            self.audio_list.insert('end', '%5d  %s' % (i + 1, l))
        self.audio_frame.pack(fill='both', expand=True)

    def _audio_sel(self, all_=False):
        streams = (self.cur or {}).get('streams') or []
        idx = range(len(streams)) if all_ else self.audio_list.curselection()
        return [(i, streams[i]) for i in idx]

    def play_audio(self):
        sel = self._audio_sel()
        if not sel:
            self.status_var.set('Pick a sound in the list first.')
            return
        i, (label, src) = sel[0]
        out = os.path.join(tempfile.gettempdir(), 'za_extractor_preview.wav')

        def job():
            import winsound
            winsound.PlaySound(None, 0)
            zm.decode_audio(src, out)
            winsound.PlaySound(out, winsound.SND_FILENAME | winsound.SND_ASYNC)
        self.run_bg('Playing sound %d' % (i + 1), job)

    def stop_audio(self):
        import winsound
        winsound.PlaySound(None, 0)

    def export_audio(self, all_):
        sel = self._audio_sel(all_)
        if not sel:
            self.status_var.set('Pick sounds in the list first.')
            return
        d = filedialog.askdirectory(initialdir=self.out_var.get(), title='Save .wav files to')
        if not d:
            return
        stem = os.path.splitext(self.cur['name'])[0]

        def job():
            for k, (i, (label, src)) in enumerate(sel):
                name = re.sub(r'[^\w\-]+', '_', '%s_%04d_%s' % (stem, i + 1, label)).strip('_')
                zm.decode_audio(src, os.path.join(d, name + '.wav'))
                self.q.put(lambda k=k: self.extract_progress(k + 1, len(sel), 'audio'))
            return len(sel)
        self.run_bg('Exporting %d sound(s)' % len(sel), job, lambda n: self.status_var.set('Saved %d .wav to %s' % (n, d)))

    def replace_audio(self, path):
        """Open the selected sound in the Audio tab, where it can be replaced (any format, loops, volume)."""
        sel = self._audio_sel()
        if len(sel) != 1:
            self.status_var.set('Select exactly one sound in the list to replace.')
            return
        try:
            if path.lower().endswith('.pck'):
                mid = [e for e in zm.parse_akpk(path)][sel[0][0]][0]
            else:
                import za_soundbank
                with open(path, 'rb') as f:
                    mid = za_soundbank.Bank(f.read()).media()[sel[0][0]][0]
        except Exception:  # noqa: BLE001
            log_error(traceback.format_exc())
            self.status_var.set('Could not find that sound - open the Audio tab and search for it.')
            return
        ui = getattr(self, 'audio_ui', None)
        if ui is not None:
            ui.reveal(mid)
        return

    def replace_audio_old(self, path):
        """Pre-1.4 replacement (PCM straight into the .bnk/.pck). Kept for reference; not used."""
        sel = self._audio_sel()
        if len(sel) != 1:
            return
        rel = self.rel_game_path(path)
        m = self.current_mod()
        if not m or not rel:
            return
        wav = filedialog.askopenfilename(title='WAV to replace sound %d' % (sel[0][0] + 1),
                                         filetypes=[('WAV audio', '*.wav')])
        if not wav:
            return
        import za_audio
        idx = sel[0][0]
        src = m.path_for(rel) if os.path.isfile(m.path_for(rel)) else path
        dest = m.path_for(rel)

        def job():
            rate, samples = za_audio.read_wav(wav)
            wem = za_audio.make_pcm_wem(rate, samples)
            if path.lower().endswith('.pck'):
                tmp_src = src
                if os.path.normcase(src) == os.path.normcase(dest):      # rebuilding the mod's own copy
                    tmp_src = dest + '.old'
                    shutil.move(dest, tmp_src)
                note = za_audio.replace_in_pck(tmp_src, idx, wem, dest,
                                               progress=lambda a, b: self.q.put(lambda: self.extract_progress(a, b, 'package')))
                if tmp_src.endswith('.old'):
                    os.remove(tmp_src)
            else:
                with open(src, 'rb') as f:
                    new, note = za_audio.replace_in_bnk(f.read(), idx, wem)
                m.add_bytes(rel, new)
            return note + ' (%.1f s, %d Hz, %d ch)' % (len(samples) / rate, rate, samples.shape[1])
        self.run_bg('Replacing sound %d' % (idx + 1), job, lambda note: self.mod_changed(m, rel, note))

    # data (FlatBuffer <-> JSON)
    def export_json(self, path):
        p = filedialog.asksaveasfilename(defaultextension='.json', initialfile=os.path.basename(path) + '.json',
                                         initialdir=self.out_var.get(), filetypes=[('JSON', '*.json')])
        if p:
            self.run_bg('Exporting JSON', lambda: open(p, 'w', encoding='utf-8').write(zm.to_json(path)),
                        lambda r: self.status_var.set('Saved ' + p))

    def import_json(self, path):
        rel = self.rel_game_path(path)
        m = self.current_mod()
        if not m or not rel:
            return
        jp = filedialog.askopenfilename(title='Edited JSON for ' + os.path.basename(path),
                                        initialdir=self.out_var.get(), filetypes=[('JSON', '*.json')])
        if not jp:
            return

        def job():
            with open(jp, encoding='utf-8') as f:
                txt = f.read()
            json.loads(txt)     # clear error message for broken JSON
            zm.from_json(txt, zm.schema_for(path), m.path_for(rel))
            return rel
        self.run_bg('Building ' + os.path.basename(path), job, lambda r: self.mod_changed(m, r))

    # text tables
    def export_text(self, path):
        p = filedialog.asksaveasfilename(defaultextension='.txt',
                                         initialfile=os.path.splitext(os.path.basename(path))[0] + '.txt',
                                         initialdir=self.out_var.get(), filetypes=[('Text', '*.txt')])
        if not p:
            return
        with open(path, 'rb') as f:
            d = f.read()
        tp = os.path.splitext(path)[0] + '.tbl'
        tbl = open(tp, 'rb').read() if os.path.isfile(tp) else None
        with open(p, 'w', encoding='utf-8-sig', newline='\n') as f:
            f.write(zf.msg_to_text(d, tbl))
        self.status_var.set('Saved %s - edit the text after the TAB on each line, keep the lines in order.' % p)

    def edit_text(self, path, line=None):
        if not self.rel_game_path(path):
            return
        import za_textedit
        try:
            za_textedit.TextEditor(self, path, line)
        except Exception as e:  # noqa: BLE001
            log_error(traceback.format_exc())
            messagebox.showerror('Lumiose Toolkit', 'Could not open the text: %s' % e)

    def import_text(self, path):
        rel = self.rel_game_path(path)
        m = self.current_mod()
        if not m or not rel:
            return
        tp = filedialog.askopenfilename(title='Edited text for ' + os.path.basename(path),
                                        initialdir=self.out_var.get(), filetypes=[('Text', '*.txt')])
        if not tp:
            return

        def job():
            with open(path, 'rb') as f:
                orig = f.read()
            with open(tp, encoding='utf-8-sig') as f:
                new = zm.txt_to_dat(f.read(), orig)
            m.add_bytes(rel, new)
            changed = sum(a != b for a, b in zip(zf.decode_msg(orig), zf.decode_msg(new)))
            return rel, changed
        self.run_bg('Building ' + os.path.basename(path), job,
                    lambda r: self.mod_changed(m, r[0], '%d line(s) changed' % r[1]))

    # textures
    def import_texture(self, path, arc_member=None):
        """PNG -> texture in the current mod. path = .bntx file, or a .arc with arc_member = inner .bntx."""
        rel = self.rel_game_path(path)
        m = self.current_mod()
        if not m or not rel:
            return
        pp = filedialog.askopenfilename(title='PNG to put into ' + (arc_member or os.path.basename(path)),
                                        initialdir=self.out_var.get(), filetypes=[('PNG image', '*.png')])
        if not pp:
            return
        t = self.cur_texture()
        ti = int(self.tex_var.get().split(':', 1)[0]) if t else 0
        layer = self.layer_var.get()
        keep = True
        try:
            psize = Image.open(pp).size
        except OSError as e:
            messagebox.showerror('Lumiose Toolkit', 'Could not open the PNG: %s' % e)
            return
        if t and psize != (t.width, t.height):
            ans = messagebox.askyesnocancel(
                'Different size', 'The PNG is %dx%d, the game texture is %dx%d.\n\n'
                'Yes = keep the PNG\'s size (the texture is rebuilt at that size)\n'
                'No = scale the PNG to the original size' % (psize + (t.width, t.height)))
            if ans is None:
                return
            keep = not ans
        src = m.path_for(rel) if os.path.isfile(m.path_for(rel)) else path   # build on earlier edits

        def job():
            with open(src, 'rb') as f:
                orig = f.read()
            if arc_member:
                files = dict(zf.parse_sarc(orig))
                new_tex, note = zm.import_png(pp, files[arc_member], ti, layer, keep_size=keep)
                new = zm.rebuild_sarc(orig, {arc_member: new_tex})
            else:
                new, note = zm.import_png(pp, orig, ti, layer, keep_size=keep)
            m.add_bytes(rel, new)
            return rel, '; '.join(note)
        self.run_bg('Encoding texture (BC compression can take a few seconds)', job,
                    lambda r: (self.mod_changed(m, r[0], r[1]), self.tex_orig_var.set(False),
                               self.refresh_preview(keep=True)))

    # ---------------- live texture editing ----------------
    def live_edit_texture(self, path, arc_member=None, ti=None, layer=None):
        """Export the texture as PNG, open it in the default image editor and re-import it into the
        current mod every time it is saved (the 3D viewer reloads by itself). ti/layer default to the
        texture shown in the preview; pass them for a texture that isn't previewed (e.g. pattern textures)."""
        rel = self.rel_game_path(path)
        m = self.current_mod()
        if m and not rel:                    # a file only the mod has (e.g. a new clothing item's texture)
            r = os.path.normcase(os.path.abspath(path))
            if r.startswith(os.path.normcase(os.path.abspath(m.romfs)) + os.sep):
                rel = os.path.relpath(os.path.abspath(path), m.romfs).replace(os.sep, '/')
        if not m or not rel:
            return
        if ti is None:
            t = self.cur_texture()
            ti = int(self.tex_var.get().split(':', 1)[0]) if t else 0
        if layer is None:
            layer = self.layer_var.get()
        src = m.path_for(rel) if os.path.isfile(m.path_for(rel)) else path
        stem = os.path.splitext(arc_member or os.path.basename(path))[0]
        folder = os.path.join(self.out_var.get(), '_live_edit')
        png = os.path.join(folder, '%s%s%s.png' % (stem, '_%d' % ti if ti else '', '_layer%d' % layer if layer else ''))
        try:
            with open(src, 'rb') as f:
                data = f.read()
            if arc_member:
                data = dict(zf.parse_sarc(data))[arc_member]
            img = zf.Bntx(data).textures[ti].decode(layer, 0)
            os.makedirs(folder, exist_ok=True)
            img.save(png)
        except Exception as e:  # noqa: BLE001
            log_error(traceback.format_exc())
            messagebox.showerror('Lumiose Toolkit', 'Could not export the texture: %s' % e)
            return
        self.live[png] = dict(mtime=os.path.getmtime(png), path=path, rel=rel, arc=arc_member, ti=ti, layer=layer,
                              mod=m, busy=False, count=0)
        editor = self.open_in_editor(png)
        self.status_var.set('Live edit: %s opened in %s - every save goes into mod "%s" (keep this window open).'
                            % (os.path.basename(png), editor, m.name))
        if not self._live_after:
            self._live_after = self.after(1000, self.live_poll)
        self.set_actions(self.cur)

    def open_external(self, p):
        try:
            os.startfile(p)
        except OSError:
            subprocess.Popen(['explorer', p])

    def open_in_editor(self, png):
        """Open a PNG for editing with the editor chosen in Preferences: the built-in miniPaint, Photopea in
        a window, Windows' "Edit" program (Paint unless changed) or another program.
        Returns a name for the status bar."""
        import za_prefs
        mode = za_prefs.editor_mode(self.settings)
        if mode in ('minipaint', 'photopea'):
            try:
                subprocess.Popen(editor_command() + [mode, png, '--title', os.path.basename(png)], cwd=APP_DIR)
                return 'miniPaint' if mode == 'minipaint' else 'Photopea'
            except OSError as e:
                log_error('image editor: %s' % e)
        ed = self.settings.get('image_editor', '')
        if mode == 'program' and ed and os.path.isfile(ed):
            try:
                subprocess.Popen([ed, png])
                return os.path.splitext(os.path.basename(ed))[0]
            except OSError:
                pass
        try:
            os.startfile(png, 'edit')
            return 'your image editor'
        except OSError:
            self.open_external(png)
            return 'the default program'

    def open_preferences(self, tab=None):
        import za_prefs
        za_prefs.PreferencesWindow(self, tab)

    def live_poll(self):
        self._live_after = None
        for png, e in list(self.live.items()):
            try:
                mt = os.path.getmtime(png)
            except OSError:
                continue
            if e['busy'] or mt == e['mtime'] or time.time() - mt < 0.7:   # wait until the editor finished writing
                continue
            e['mtime'], e['busy'] = mt, True
            self.live_import(png, e)
        if self.live:
            self._live_after = self.after(1000, self.live_poll)

    def live_import(self, png, e):
        m, rel = e['mod'], e['rel']

        def job():
            src = m.path_for(rel) if os.path.isfile(m.path_for(rel)) else e['path']
            with open(src, 'rb') as f:
                orig = f.read()
            if e['arc']:
                files = dict(zf.parse_sarc(orig))
                new_tex, note = zm.import_png(png, files[e['arc']], e['ti'], e['layer'], keep_size=True)
                new = zm.rebuild_sarc(orig, {e['arc']: new_tex})
            else:
                new, note = zm.import_png(png, orig, e['ti'], e['layer'], keep_size=True)
            m.add_bytes(rel, new)
            return '; '.join(note)

        def done(note):
            e['busy'] = False
            e['count'] += 1
            self.status_var.set('Live edit: saved into mod "%s" (%d×) at %s - the preview shows it. %s' % (
                m.name, e['count'], time.strftime('%H:%M:%S'), note))
            self.refresh_mods()
            self.tex_orig_var.set(False)
            self.refresh_preview(keep=True, only_iid=('A|%s|%s' % (e['path'], e['arc'])) if e['arc']
                                 else 'F|' + e['path'])

        def work():
            try:
                note = job()
                self.q.put(lambda: done(note))
            except Exception as ex:  # noqa: BLE001
                log_error(traceback.format_exc())
                msg = str(ex)

                def fail():
                    e['busy'] = False
                    self.status_var.set('Live edit: could not import %s - %s' % (os.path.basename(png), msg))
                self.q.put(fail)
        threading.Thread(target=work, daemon=True).start()

    def stop_live_edits(self):
        n = len(self.live)
        self.live.clear()
        self.status_var.set('Stopped %d live edit(s). The PNGs stay in %s' % (
            n, os.path.join(self.out_var.get(), '_live_edit')))
        self.set_actions(self.cur)

    def add_selected_to_mod(self):
        m = self.current_mod()
        if not m:
            return
        added = 0
        for i in self.tree.selection():
            iid = self.split_iid(i)[1]
            if iid.startswith('F|'):
                rel = self.rel_game_path(iid[2:])
                if rel:
                    if not os.path.isfile(m.path_for(rel)):
                        m.add_file(rel, iid[2:])
                    added += 1
        self.mod_changed(m, None, '%d file(s) added' % added)

    def mod_changed(self, m, rel, note=''):
        self.status_var.set('Mod "%s": %s %s' % (m.name, rel or '', ('- ' + note) if note else ''))
        self.refresh_mods()

    def cur_texture(self):
        if not self.cur or self.cur.get('kind') != 'texture':
            return None
        try:
            i = int(self.tex_var.get().split(':', 1)[0])
        except ValueError:
            i = 0
        return self.cur['bntx'].textures[i]

    def update_tex_controls(self):
        t = self.cur_texture()
        if not t:
            return
        self.layer_spin.configure(to=t.array_len - 1)
        self.mip_spin.configure(to=t.mip_count - 1)
        self.layer_var.set(min(self.layer_var.get(), t.array_len - 1))
        self.mip_var.set(min(self.mip_var.get(), t.mip_count - 1))

    def on_tex_change(self):
        self.layer_var.set(0)
        self.mip_var.set(0)
        self.update_tex_controls()
        self.redecode()

    def redecode(self):
        t = self.cur_texture()
        if not t:
            return
        head = '%s   (%s)%s' % (self.cur.get('name', ''), human_size(self.cur.get('size', 0)),
                                self.cur.get('src_note', ''))
        desc = t.describe()
        if t.is_cubemap:
            desc += '   showing face %s' % CUBE_FACES[self.layer_var.get() % 6]
        self.info_var.set(head + '\n' + desc)
        if not t.supported:
            self.show_text_mode('Texture format %s is not supported for preview.' % t.format_name)
            return
        self.preview_token += 1
        token = self.preview_token
        layer, mip, nrm = self.layer_var.get(), self.mip_var.get(), self.nrm_var.get()

        def work():
            try:
                img = t.decode(layer, mip, rebuild_normal_z=nrm)
                self.q.put(lambda: self._set_img(token, img))
            except Exception as e:  # noqa: BLE001
                log_error(traceback.format_exc())
                msg = str(e)
                self.q.put(lambda: self.show_text_mode('Decode failed: ' + msg))

        threading.Thread(target=work, daemon=True).start()

    def _set_img(self, token, img):
        if token != self.preview_token:
            return
        self.cur_img = img
        self.show_image_mode()
        self.render()

    def show_image_mode(self):
        self.hide_model_mode()
        self.text.pack_forget()
        self.audio_frame.pack_forget()
        if not self.tex_bar.winfo_ismapped():
            self.tex_bar.pack(fill='x', padx=4, pady=2, before=self.stack)
        self.canvas_frame.pack(fill='both', expand=True)

    def show_text_mode(self, text, wrap='word'):
        self.hide_model_mode()
        self.cur_img = None
        self.tex_bar.pack_forget()
        self.canvas_frame.pack_forget()
        self.audio_frame.pack_forget()
        if hasattr(self, 'act_bar') and (self.cur is None or 'kind' not in (self.cur or {})):
            for w in self.act_bar.winfo_children():
                w.destroy()
        self.pixel_var.set('')
        self.text.configure(state='normal', wrap=wrap)
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', text)
        self.text.configure(state='disabled')
        self.text.pack(fill='both', expand=True)

    def checker(self, w, h):
        key = (w, h)
        if key not in self._checker_cache:
            if len(self._checker_cache) > 6:
                self._checker_cache.clear()
            yy, xx = np.indices((h, w))
            lo, hi = THEMES['dark' if self.dark_var.get() else 'light']['checker']
            c = np.where(((xx // 12) + (yy // 12)) % 2 == 0, lo, hi).astype(np.uint8)
            arr = np.stack([c, c, c, np.full_like(c, 255)], axis=-1)
            self._checker_cache[key] = Image.fromarray(arr, 'RGBA')
        return self._checker_cache[key]

    def view_image(self, img):
        mode = self.view_var.get()
        if mode == 'RGB':
            return img.convert('RGB')
        if mode in ('R', 'G', 'B', 'A'):
            return img.getchannel(mode)
        return img

    def schedule_render(self):
        if self._render_after:
            self.after_cancel(self._render_after)
        self._render_after = self.after(80, self.render)

    def render(self):
        self._render_after = None
        img = self.cur_img
        if img is None:
            return
        cw = max(50, self.canvas.winfo_width())
        ch = max(50, self.canvas.winfo_height())
        w, h = img.size
        z = self.zoom_var.get()
        if z == 'Fit':
            s = min(cw / w, ch / h)
            s = min(s, 16.0)
        else:
            s = int(z.rstrip('%')) / 100.0
        dw, dh = max(1, int(w * s)), max(1, int(h * s))
        resample = Image.LANCZOS if s < 1 else Image.NEAREST
        # per-channel resize: a plain RGBA resize premultiplies by alpha and blacks out masks
        disp = zt.resize_channels(img, (dw, dh), resample) if (dw, dh) != (w, h) else img
        disp = self.view_image(disp)
        if disp.mode == 'RGBA':
            disp = Image.alpha_composite(self.checker(dw, dh), disp).convert('RGB')
        self.photo = ImageTk.PhotoImage(disp)
        self.canvas.delete('all')
        self.disp_scale = s
        if z == 'Fit':
            self.img_origin = ((cw - dw) // 2, (ch - dh) // 2)
            self.canvas.create_image(cw // 2, ch // 2, image=self.photo, anchor='center')
            self.canvas.configure(scrollregion=(0, 0, cw, ch))
        else:
            ox, oy = max(0, (cw - dw) // 2), max(0, (ch - dh) // 2)
            self.img_origin = (ox, oy)
            self.canvas.create_image(ox, oy, image=self.photo, anchor='nw')
            self.canvas.configure(scrollregion=(0, 0, max(cw, dw), max(ch, dh)))

    def on_wheel(self, event):
        if event.state & 0x4:  # ctrl + wheel = zoom
            i = ZOOMS.index(self.zoom_var.get())
            i = min(len(ZOOMS) - 1, i + 1) if event.delta > 0 else max(1, i - 1)
            self.zoom_var.set(ZOOMS[i])
            self.render()
        elif event.state & 0x1:
            self.canvas.xview_scroll(-int(event.delta / 120) * 3, 'units')
        else:
            self.canvas.yview_scroll(-int(event.delta / 120) * 3, 'units')

    def on_canvas_motion(self, event):
        img = self.cur_img
        if img is None or not hasattr(self, 'img_origin'):
            return
        x = (self.canvas.canvasx(event.x) - self.img_origin[0]) / self.disp_scale
        y = (self.canvas.canvasy(event.y) - self.img_origin[1]) / self.disp_scale
        if 0 <= x < img.size[0] and 0 <= y < img.size[1]:
            r, g, b, a = img.getpixel((int(x), int(y)))
            self.pixel_var.set('x %d  y %d   R %d  G %d  B %d  A %d' % (x, y, r, g, b, a))
        else:
            self.pixel_var.set('%d x %d' % img.size)

    def save_view(self):
        if self.cur_img is None:
            return
        t = self.cur_texture()
        name = (t.name if t else 'texture')
        v = self.view_var.get()
        if v != 'RGBA':
            name += '_' + v
        p = filedialog.asksaveasfilename(defaultextension='.png', initialfile=name + '.png',
                                         initialdir=self.out_var.get(), filetypes=[('PNG image', '*.png')])
        if p:
            self.view_image(self.cur_img).save(p)
            self.status_var.set('Saved ' + p)

    # ---------------- extraction ----------------
    def start_extract(self):
        if self.extracting:
            return
        sel = [self.split_iid(i)[1] for i in self.tree.selection() if not i.endswith('|dummy')]
        sel = [i for i in sel if not i.startswith(('H|', 'U|'))]
        # files belonging to selected Pokémon / clothing name matches
        name_files = {i: [p for _, p in self.buckets.get(self.name_hits[i][0].pattern, [])]
                      for i in sel if i.startswith('N|') and i in self.name_hits}
        if not sel:
            messagebox.showinfo('Lumiose Toolkit', 'Select one or more files or folders first.')
            return
        if not (self.png_var.get() or self.dds_var.get()):
            if not messagebox.askyesno('Lumiose Toolkit', 'Neither PNG nor DDS is ticked, so textures will be '
                                       'skipped. Continue?'):
                return
        out = os.path.normpath(self.out_var.get())
        opts = {
            'png': self.png_var.get(), 'dds': self.dds_var.get(), 'keep_structure': self.keep_var.get(),
            'rebuild_nrm': self.nrm_var.get(), 'inc_tex': self.inc_tex.get(), 'inc_text': self.inc_text.get(),
            'inc_arc': self.inc_arc.get(), 'inc_other': self.inc_other.get(),
            'inc_model': self.inc_model.get(), 'inc_data': self.inc_data.get(), 'inc_audio': self.inc_audio.get(),
            'gltf': self.gltf_var.get(), 'gltf_anims': self.gltf_anims_var.get(), 'json': self.json_var.get(),
            'wav': self.wav_var.get(),
        }
        self.save_settings()
        self.extracting = True
        self.cancel_flag.clear()
        self.extract_btn.configure(state='disabled')
        self.cancel_btn.configure(state='normal')
        self.progress.configure(value=0, maximum=1)
        self.status_var.set('Collecting files…')
        root = self.root_var.get()

        def arc_loader(path):
            with open(path, 'rb') as f:
                return dict(zf.parse_sarc(f.read()))

        def work():
            ex = Extractor(root, out, opts, arc_loader)
            jobs = []
            app = os.path.normcase(APP_DIR)
            outc = os.path.normcase(os.path.abspath(out))
            for iid in sel:
                if iid.startswith('D|'):
                    for dp, dn, fn in os.walk(iid[2:]):
                        dn[:] = [d for d in dn if os.path.normcase(os.path.join(dp, d)) not in (app, outc)]
                        jobs.extend(('F', os.path.join(dp, f), False) for f in fn)
                elif iid.startswith('F|'):
                    jobs.append(('F', iid[2:], True))
                elif iid.startswith('A|'):
                    a, n = iid[2:].split('|', 1)
                    jobs.append(('A', a, n))
                elif iid in name_files:
                    jobs.extend(('F', p, False) for p in name_files[iid])
            seen = set()
            jobs = [j for j in jobs if not ((j[0], j[1], j[2] if j[0] == 'A' else '') in seen
                                            or seen.add((j[0], j[1], j[2] if j[0] == 'A' else '')))]
            total = len(jobs)
            last = 0
            for i, job in enumerate(jobs):
                if self.cancel_flag.is_set():
                    break
                try:
                    if job[0] == 'F':
                        ex.disk_file(job[1], job[2])
                    else:
                        ex.arc_member(job[1], job[2])
                except Exception as e:  # noqa: BLE001
                    ex.errors.append('%s: %s: %s' % (job[1] if job[0] == 'F' else job[1] + ' > ' + job[2],
                                                     type(e).__name__, e))
                now = time.time()
                if now - last > 0.1 or i == total - 1:
                    last = now
                    name = os.path.basename(job[1]) if job[0] == 'F' else job[2]
                    self.q.put(lambda i=i, name=name: self.extract_progress(i + 1, total, name))
            self.q.put(lambda: self.extract_done(ex, total, out))

        threading.Thread(target=work, daemon=True).start()

    def extract_progress(self, i, total, name):
        self.progress.configure(maximum=max(1, total), value=i)
        self.status_var.set('Extracting %d / %d   %s' % (i, total, name))

    def extract_done(self, ex, total, out):
        self.extracting = False
        self.extract_btn.configure(state='normal')
        self.cancel_btn.configure(state='disabled')
        cancelled = self.cancel_flag.is_set()
        msg = '%s: wrote %d file(s) from %d input(s) to %s' % (
            'Cancelled' if cancelled else 'Done', ex.outputs, total, out)
        if ex.errors:
            os.makedirs(out, exist_ok=True)
            ep = os.path.join(out, '_extract_errors.txt')
            with open(ep, 'w', encoding='utf-8') as f:
                f.write('\n'.join(ex.errors) + '\n')
            msg += '   -   %d problem(s), see _extract_errors.txt' % len(ex.errors)
        self.status_var.set(msg)
        if ex.outputs and not cancelled:
            if messagebox.askyesno('Lumiose Toolkit', msg + '\n\nOpen the output folder?'):
                self.open_out()

    # ---------------- queue ----------------
    def poll_queue(self):
        try:
            while True:
                fn = self.q.get_nowait()
                fn()
        except queue.Empty:
            pass
        self.after(30, self.poll_queue)


def main():
    if '--viewer' in sys.argv:          # the .exe started as the 3D viewer (see viewer_command)
        sys.argv.remove('--viewer')
        import za_viewer
        return za_viewer.main()
    if '--editor' in sys.argv:          # the .exe started as the image editor window (see editor_command)
        sys.argv.remove('--editor')
        import za_editor
        return za_editor.main()
    try:
        # own taskbar identity, so Windows shows our icon instead of Python's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ZAExtractor.App')
    except Exception:  # noqa: BLE001
        pass
    try:
        app = App()
        app.mainloop()
    except Exception:  # noqa: BLE001
        log_error(traceback.format_exc())
        raise


if __name__ == '__main__':
    main()
