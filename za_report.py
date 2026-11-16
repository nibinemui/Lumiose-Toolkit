"""Report a bug: collects the details a bug report needs and opens the GitHub bug report form.

The details are shown in an editable box before anything leaves the computer. Nothing is sent
automatically: the user copies the text and pastes it into the form on GitHub themselves.

Privacy: the Windows user name and the folders set in the toolkit (game, output, mods, ZA Recomp...)
are replaced with placeholders like <user> and <game folder>. No file contents are included.
"""
import os
import platform
import re
import sys
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import za_version

FROZEN = getattr(sys, 'frozen', False)
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
LOG_PATH = os.path.join(APP_DIR, 'error_log.txt')
UPDATE_LOG = os.path.join(APP_DIR, 'update_log.txt')
# only the version goes into the address - never paths, names or log text
ISSUE_URL = ('https://github.com/%s/issues/new?template=bug_report.yml&version=%s'
             % (za_version.REPO, za_version.VERSION))

MAX_ERRORS = 4               # most recent error_log.txt entries included
MAX_ERROR_CHARS = 9000       # GitHub form fields hold 65k, but long reports get skimmed

# settings that say how the program is set up, without paths or names
SAFE_SETTINGS = ('theme', 'ui_scale', 'image_editor_mode', 'auto_3d', 'viewer_texsize', 'viewer_place',
                 'history_max', 'check_updates', 'filter', 'text_lang', 'targets', 'png', 'dds', 'gltf',
                 'gltf_anims', 'json', 'wav', 'keep_structure', 'rebuild_nrm')


def windows_version():
    try:
        v = sys.getwindowsversion()
        name = 'Windows 11' if v.build >= 22000 else 'Windows %s' % platform.release()
        return '%s (build %d)' % (name, v.build)
    except AttributeError:
        return platform.platform()


def anonymize(text, paths=()):
    """Replace known folders with placeholders, then any remaining user-profile path."""
    pairs = [(p, tag) for p, tag in paths if p and len(p) > 3]
    pairs.append((APP_DIR, '<toolkit folder>'))
    home = os.path.expanduser('~')
    if home and home != '~':
        pairs.append((home, '<home>'))
    pairs.sort(key=lambda pt: -len(pt[0]))           # longest first: a game folder inside <home> stays <game folder>
    for p, tag in pairs:
        for form in {p, p.replace('\\', '/'), p.replace('/', '\\'), p.replace('\\', '\\\\')}:
            text = re.sub(re.escape(form), lambda m, t=tag: t, text, flags=re.IGNORECASE)
    text = re.sub(r'(?i)([a-z]:[\\/]+users[\\/]+)[^\\/\s"\']+', lambda m: m.group(1) + '<user>', text)
    user = os.environ.get('USERNAME', '')
    if len(user) >= 3:
        text = re.sub(r'(?i)\b%s\b' % re.escape(user), '<user>', text)
    return text


def recent_errors(path=LOG_PATH, count=MAX_ERRORS, limit=MAX_ERROR_CHARS):
    """The last few entries of error_log.txt (each entry starts with [date] or [viewer]/[editor])."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError:
        return ''
    starts = [m.start() for m in re.finditer(r'(?m)^\[(?:\d{4}-\d\d-\d\d [\d:]+|viewer|editor)\] ', text)]
    if not starts:
        return text[-limit:].strip()
    entries = [text[a:b].strip() for a, b in zip(starts, starts[1:] + [len(text)])]
    out = '\n\n'.join(entries[-count:])
    if len(out) > limit:
        out = '…' + out[-limit:]
    return out


def gather(app=None):
    """The report text: version, system, setup and recent errors (anonymized)."""
    lines = ['Lumiose Toolkit %s (%s)' % (za_version.VERSION, 'release .exe' if FROZEN else 'running from source'),
             '%s, Python %s' % (windows_version(), platform.python_version())]
    paths = []
    if app is not None:
        def var(name):
            v = getattr(app, name, None)
            try:
                return v.get() if v is not None else ''
            except Exception:  # noqa: BLE001
                return ''
        paths = [(var('root_var'), '<game folder>'), (var('out_var'), '<output folder>'),
                 (var('recomp_var'), '<ZA Recomp folder>'), (var('game_romfs_var'), '<romfs folder>'),
                 (var('pkg_out_var'), '<packages folder>'), (var('mod_var'), '<mod name>')]
        try:
            lines.append('Screen %dx%d, window %dx%d, interface scale %s' % (
                app.winfo_screenwidth(), app.winfo_screenheight(), app.winfo_width(), app.winfo_height(),
                round(float(app.tk.call('tk', 'scaling')), 2)))
        except Exception:  # noqa: BLE001
            pass
        try:
            tab = app.nb.tab(app.nb.select(), 'text')
            lines.append('Open tab: %s' % tab.strip())
        except Exception:  # noqa: BLE001
            pass
        lines.append('Game folder set: %s, files indexed: %s' % (
            'yes' if var('root_var') else 'no', var('index_var') or 'not yet'))
        settings = getattr(app, 'settings', {}) or {}
        safe = ['%s=%s' % (k, settings[k]) for k in SAFE_SETTINGS if k in settings]
        if settings.get('image_editor'):
            safe.append('image_editor=<custom program>')
        if safe:
            lines.append('Settings: ' + ', '.join(safe))
    errors = recent_errors()
    lines += ['', 'Recent errors (error_log.txt):', errors or '(none)']
    return anonymize('\n'.join(lines), paths)


class ReportWindow(tk.Toplevel):
    def __init__(self, app, extra=None):
        super().__init__(app)
        self.app = app
        self.title('Report a bug')
        self.transient(app)
        self.geometry('720x560')
        self.minsize(520, 380)
        try:
            self.iconbitmap(os.path.join(APP_DIR, 'lumiose_toolkit.ico'))
        except tk.TclError:
            pass
        bg = ttk.Style(self).lookup('TFrame', 'background') or None
        if bg:
            self.configure(background=bg)
        f = ttk.Frame(self, padding=10)
        f.pack(fill='both', expand=True)
        ttk.Label(f, text='Report a bug', font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        ttk.Label(f, wraplength=680, justify='left', text=(
            '1. Click "Copy details".   2. Click "Open bug report on GitHub" (you need a GitHub account).   '
            '3. Paste the details into the form and describe what happened.\n'
            'Your user name and folders are replaced with placeholders. You can edit the text below '
            'before copying - nothing is sent automatically.')).pack(anchor='w', pady=(2, 8))
        box = ttk.Frame(f)
        box.pack(fill='both', expand=True)
        self.text = tk.Text(box, wrap='none', relief='flat', padx=8, pady=6, font=('Consolas', 9),
                            borderwidth=0, highlightthickness=0, undo=True)
        ys = ttk.Scrollbar(box, command=self.text.yview)
        xs = ttk.Scrollbar(box, orient='horizontal', command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side='right', fill='y')
        xs.pack(side='bottom', fill='x')
        self.text.pack(side='left', fill='both', expand=True)
        th = getattr(app, 'theme_colours', None)
        if callable(th):
            c = th()
            self.text.configure(background=c.get('field', 'white'), foreground=c.get('fg', 'black'),
                                insertbackground=c.get('fg', 'black'), selectbackground=c.get('sel', '#cce4f7'),
                                selectforeground=c.get('selfg', 'black'))
        self.status = tk.StringVar()
        ttk.Label(f, textvariable=self.status).pack(anchor='w', pady=(6, 0))
        bar = ttk.Frame(f)
        bar.pack(fill='x', pady=(6, 0))
        ttk.Button(bar, text='Copy details', command=self.copy).pack(side='left')
        ttk.Button(bar, text='Open bug report on GitHub', command=self.open_form).pack(side='left', padx=6)
        ttk.Button(bar, text='Close', command=self.destroy).pack(side='right')
        ttk.Button(bar, text='Clear error log', command=self.clear_log).pack(side='right', padx=6)
        ttk.Button(bar, text='Open error log', command=self.open_log).pack(side='right')
        self.fill(extra)
        self.bind('<Escape>', lambda e: self.destroy())

    def fill(self, extra=None):
        try:
            body = gather(self.app)
        except Exception as e:  # noqa: BLE001 - the report window must open even if gathering fails
            body = 'Lumiose Toolkit %s\n(could not collect details: %s)' % (za_version.VERSION, e)
        if extra:
            body = 'Error just now: %s\n\n%s' % (anonymize(str(extra)), body)
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', body)

    def copy(self):
        body = self.text.get('1.0', 'end-1c')
        self.clipboard_clear()
        self.clipboard_append(body)        # the form's field already shows it as plain text (render: text)
        self.status.set('Copied - paste it into "Details from the toolkit" on the GitHub form.')

    def open_form(self):
        webbrowser.open(ISSUE_URL)
        self.status.set('The form opened in your browser. Paste the details there (Ctrl+V).')

    def open_log(self):
        if os.path.isfile(LOG_PATH):
            os.startfile(LOG_PATH)          # noqa: S606 - opens the log in the user's text editor
        else:
            self.status.set('There is no error log yet - no errors were recorded.')

    def clear_log(self):
        if not os.path.isfile(LOG_PATH):
            self.status.set('The error log is already empty.')
            return
        if not messagebox.askyesno('Clear error log', 'Delete all recorded errors from error_log.txt?\n\n'
                                   'Do this after reporting, so old errors are not reported again.', parent=self):
            return
        try:
            os.remove(LOG_PATH)
        except OSError as e:
            self.status.set('Could not clear it: %s' % e)
            return
        self.fill()
        self.status.set('Error log cleared.')
