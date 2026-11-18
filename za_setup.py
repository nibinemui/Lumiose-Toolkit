"""Import game files: extract a Pokémon Legends: Z-A RomFS dump into a game folder, inside the program.

Uses restore_missing.restore() (the same code as restore_missing.exe), then copies the dump's loose
folders (audio, system...) and points Lumiose Toolkit at the result. Opens by itself on first start when
no game files are set up.
"""
import os
import shutil
import string
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
GAME_MARKERS = ('ik_chara', 'ik_message', 'world')
OODLE_NAMES = ('oo2core_8_win64.dll', 'oo2core_9_win64.dll', 'oo2core_7_win64.dll')
_OODLE_WORDS = ('pknx', 'steam', 'epic', 'game', 'tool', 'download', 'program', 'switch', 'pokemon', 'oodle',
                'trinity', 'gftool', 'mod', 'unreal', 'common')


def looks_like_game_folder(path):
    return bool(path) and sum(os.path.isdir(os.path.join(path, m)) for m in GAME_MARKERS) >= 2


def find_arc(folder):
    """The 'arc' folder (with data.trpfd + data.trpfs) for a dump folder, its romfs folder or arc itself."""
    if not folder:
        return None
    for cand in (folder, os.path.join(folder, 'arc'), os.path.join(folder, 'romfs', 'arc')):
        if os.path.isfile(os.path.join(cand, 'data.trpfd')) and os.path.isfile(os.path.join(cand, 'data.trpfs')):
            return os.path.normpath(cand)
    return None


def find_oodle():
    """Look for an Oodle DLL (oo2core_*_win64.dll) in likely places: next to the program, the user's
    folders, and - a few levels deep - folders that look like games or modding tools."""
    found = []
    home = os.path.expanduser('~')
    roots = [APP_DIR, os.path.join(APP_DIR, 'tools'), home, os.path.join(home, 'Downloads'),
             os.path.join(home, 'Desktop'), os.path.join(home, 'Documents')]
    roots += [c + ':' + os.sep for c in string.ascii_uppercase if os.path.isdir(c + ':' + os.sep)]
    seen = set()

    def scan(d, depth):
        k = os.path.normcase(d)
        if k in seen or len(found) >= 5:
            return
        seen.add(k)
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            if e.name.lower() in OODLE_NAMES and e.is_file():
                found.append(e.path)
        if depth >= 4:
            return
        for e in entries:
            n = e.name.lower()
            if n.startswith(('$', '.')) or n in ('windows', 'appdata', 'node_modules', 'programdata'):
                continue
            if depth >= 1 and not any(w in n for w in _OODLE_WORDS) and \
                    not any(w in os.path.basename(d).lower() for w in _OODLE_WORDS):
                continue
            try:
                if e.is_dir():
                    scan(e.path, depth + 1)
            except OSError:
                continue
    for r in roots:
        scan(r, 0)
    return sorted(set(found), key=lambda p: (OODLE_NAMES.index(os.path.basename(p).lower()), len(p)))


class ImportWindow(tk.Toplevel):
    def __init__(self, app, first_start=False):
        super().__init__(app)
        self.app = app
        self.title('Import game files')
        self.geometry('760x560')
        self.transient(app)
        self.running = False
        self.stop = False
        P = ttk.Frame(self, padding=10)
        P.pack(fill='both', expand=True)
        intro = ('Lumiose Toolkit works on the files of your own Pokémon Legends: Z-A dump. Pick the dump\'s '
                 'RomFS folder (the one with an "arc" folder inside) and a folder to put the files in '
                 '(needs about 25 GB). This only has to be done once.')
        if first_start:
            intro = 'Welcome! ' + intro
        ttk.Label(P, text=intro, wraplength=720, justify='left').pack(anchor='w', pady=(0, 8))
        g = ttk.Frame(P)
        g.pack(fill='x')
        self.dump = tk.StringVar(value=app.game_romfs_var.get() if hasattr(app, 'game_romfs_var') else '')
        self.dest = tk.StringVar()
        self.oodle = tk.StringVar()
        rows = (('Game dump (RomFS):', self.dump, self.pick_dump),
                ('Put the game files in:', self.dest, self.pick_dest),
                ('Oodle DLL:', self.oodle, self.pick_oodle))
        for r, (label, var, cmd) in enumerate(rows):
            ttk.Label(g, text=label).grid(row=r, column=0, sticky='w', pady=2)
            ttk.Entry(g, textvariable=var).grid(row=r, column=1, sticky='ew', padx=4, pady=2)
            ttk.Button(g, text='Browse…', command=cmd).grid(row=r, column=2, pady=2)
        g.columnconfigure(1, weight=1)
        self.oodle_note = ttk.Label(P, text='Searching this PC for the Oodle DLL (oo2core_8_win64.dll)…',
                                    style='Dim.TLabel', wraplength=720, justify='left')
        self.oodle_note.pack(anchor='w', pady=(2, 8))
        self.dump.trace_add('write', lambda *a: self.suggest_dest())
        bf = ttk.Frame(P)
        bf.pack(fill='x')
        self.go_btn = ttk.Button(bf, text='Import', command=self.start)
        self.go_btn.pack(side='left')
        self.stop_btn = ttk.Button(bf, text='Stop', command=self.request_stop, state='disabled')
        self.stop_btn.pack(side='left', padx=4)
        ttk.Button(bf, text='I already have the files - choose the folder…', command=self.use_existing).pack(side='right')
        self.bar = ttk.Progressbar(P, mode='determinate')
        self.bar.pack(fill='x', pady=8)
        st = app.style
        self.log = tk.Text(P, height=12, wrap='word', font=('Consolas', 9), borderwidth=0,
                           background=st.lookup('Treeview', 'fieldbackground') or 'white',
                           foreground=st.lookup('Treeview', 'foreground') or 'black')
        self.log.pack(fill='both', expand=True)
        self.suggest_dest()
        self.protocol('WM_DELETE_WINDOW', self.close)
        threading.Thread(target=self._search_oodle, daemon=True).start()

    # ---------------- fields ----------------
    def write(self, text):
        self.log.insert('end', text + '\n')
        self.log.see('end')

    def suggest_dest(self):
        if self.dest.get():
            return
        arc = find_arc(self.dump.get())
        if arc:
            dump_root = os.path.dirname(os.path.dirname(arc))           # the folder that contains romfs
            self.dest.set(os.path.join(os.path.dirname(dump_root) if os.path.dirname(dump_root) else dump_root,
                                       'ZA Game Files'))

    def pick_dump(self):
        d = filedialog.askdirectory(parent=self, title='Your game dump: the RomFS folder (or the folder containing it)')
        if d:
            self.dump.set(os.path.normpath(d))
            if not find_arc(d):
                messagebox.showwarning('Import game files', 'No arc\\data.trpfd + data.trpfs found in that folder. '
                                       'Pick the dump\'s "romfs" folder.', parent=self)

    def pick_dest(self):
        d = filedialog.askdirectory(parent=self, title='Folder for the game files (about 25 GB)')
        if d:
            self.dest.set(os.path.normpath(d))

    def pick_oodle(self):
        f = filedialog.askopenfilename(parent=self, title='Oodle DLL', filetypes=[('Oodle DLL', 'oo2core_*_win64.dll'),
                                                                                  ('DLL', '*.dll')])
        if f:
            self.oodle.set(os.path.normpath(f))
            self.oodle_note.configure(text='Using the DLL you picked.')

    def _search_oodle(self):
        try:
            found = find_oodle()
        except Exception:  # noqa: BLE001
            found = []

        def done():
            if not self.winfo_exists():
                return
            if self.oodle.get():
                return
            if found:
                self.oodle.set(found[0])
                self.oodle_note.configure(text='Found the Oodle DLL on this PC.')
            else:
                self.oodle_note.configure(text=(
                    'The Oodle DLL (oo2core_8_win64.dll) wasn\'t found. It decompresses the game archive and '
                    'cannot be shipped with Lumiose Toolkit. It comes with several Pokémon modding tools (for '
                    'example pkNX) and many PC games - pick it with Browse….'))
        self.app.q.put(done)

    # ---------------- import ----------------
    def start(self):
        arc = find_arc(self.dump.get())
        dest, dll = self.dest.get().strip(), self.oodle.get().strip()
        if not arc:
            messagebox.showerror('Import game files', 'Pick your dump\'s RomFS folder first (it contains '
                                 'arc\\data.trpfd and arc\\data.trpfs).', parent=self)
            return
        if not dest:
            messagebox.showerror('Import game files', 'Choose where to put the game files.', parent=self)
            return
        if not os.path.isfile(dll):
            messagebox.showerror('Import game files', 'Pick the Oodle DLL (oo2core_8_win64.dll).', parent=self)
            return
        try:
            free = shutil.disk_usage(os.path.splitdrive(os.path.abspath(dest))[0] + os.sep).free
            if free < 26 * 1024 ** 3 and not messagebox.askyesno(
                    'Import game files', 'Only %.0f GB free on that drive; the game files need about 25 GB. '
                    'Continue anyway?' % (free / 1024 ** 3), parent=self):
                return
        except OSError:
            pass
        self.running, self.stop = True, False
        self.go_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        romfs = os.path.dirname(arc)
        q = self.app.q

        def log(t):
            q.put(lambda: self.write(t))

        def progress(done, total, written):
            q.put(lambda: (self.bar.configure(maximum=max(total, 1), value=done),
                           self.app.status_var.set('Importing game files: %d / %d packs, %d files' % (done, total, written))))

        def work():
            try:
                import za_deps
                import restore_missing
                if za_deps.missing():
                    log('Downloading the file-name list from PokeDocs…')
                    probs = za_deps.ensure(log=log)
                    if probs:
                        raise RuntimeError('; '.join(probs))
                listing = os.path.join(APP_DIR, 'names', 'za_hashes_inside_fd.txt')
                os.makedirs(dest, exist_ok=True)
                log('Extracting the game archive (this takes a while; you can Stop and continue later)…')
                res = restore_missing.restore(arc, dest, dll, listing, log=log, progress=progress,
                                              cancel=lambda: self.stop)
                errors = res[2] if isinstance(res, tuple) and len(res) > 2 else 0
                if not self.stop:
                    for name in sorted(os.listdir(romfs)):
                        src = os.path.join(romfs, name)
                        if name.lower() == 'arc' or not os.path.isdir(src):
                            continue
                        log('Copying %s…' % name)
                        _copy_missing(src, os.path.join(dest, name))
                q.put(lambda: self.finished(dest, romfs, not self.stop, errors))
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                q.put(lambda: self.failed(msg))
        threading.Thread(target=work, daemon=True).start()

    def request_stop(self):
        self.stop = True
        self.write('Stopping after the current pack…')

    def finished(self, dest, romfs, complete, errors=0):
        self.running = False
        self.go_btn.configure(state='normal')
        self.stop_btn.configure(state='disabled')
        if not complete:
            self.app.status_var.set('Import stopped - press Import again to continue.')
            return
        if errors:
            self.write('%d part(s) of the game archive could not be extracted (see above).' % errors)
            messagebox.showwarning('Import game files', '%d part(s) of the game archive could not be extracted - '
                                   'is the disk full? Free some space and press Import again: it continues where it '
                                   'stopped and only fills in what is missing.' % errors, parent=self)
            return
        self.write('Done. Lumiose Toolkit now uses %s' % dest)
        self.app.game_romfs_var.set(romfs)
        self.use_folder(dest)
        messagebox.showinfo('Import game files', 'The game files are ready in:\n%s' % dest, parent=self)
        self.destroy()

    def failed(self, msg):
        self.running = False
        self.go_btn.configure(state='normal')
        self.stop_btn.configure(state='disabled')
        self.write('Failed: ' + msg)
        messagebox.showerror('Import game files', 'The import failed:\n\n%s' % msg, parent=self)

    def use_existing(self):
        d = filedialog.askdirectory(parent=self, title='Folder with the extracted game files')
        if d:
            if not looks_like_game_folder(d) and not messagebox.askyesno(
                    'Import game files', 'That folder doesn\'t look like extracted Z-A files (no ik_chara / '
                    'ik_message / world folders). Use it anyway?', parent=self):
                return
            self.use_folder(os.path.normpath(d))
            self.destroy()

    def use_folder(self, folder):
        self.app.root_var.set(folder)
        self.app.save_settings()
        self.app.reload_root()

    def close(self):
        if self.running:
            if not messagebox.askyesno('Import game files', 'Stop the import and close? Files already written '
                                       'are kept; Import continues where it stopped.', parent=self):
                return
            self.stop = True
        self.destroy()


def _copy_missing(src, dst):
    for dp, dn, fn in os.walk(src):
        rel = os.path.relpath(dp, src)
        os.makedirs(os.path.join(dst, rel), exist_ok=True)
        for f in fn:
            t = os.path.join(dst, rel, f)
            if not os.path.exists(t):
                shutil.copy2(os.path.join(dp, f), t + '.part')    # a cut-off copy never looks finished
                os.replace(t + '.part', t)
