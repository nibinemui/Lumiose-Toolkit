"""Updater: checks GitHub for a newer Lumiose Toolkit release and installs it.

How it works
  * check()     asks the GitHub API for the latest release of za_version.REPO and compares its tag
                (v1.2.0) with za_version.VERSION.
  * download()  fetches the release zip (Lumiose-Toolkit-<version>-windows.zip) from GitHub, then checks
                its size and - when GitHub publishes it - its SHA-256.
  * stage()     unzips it into a temporary folder.
  * install()   writes a small PowerShell script that waits for the toolkit to close, copies the new
                files over the program folder and starts the new version. Your mods, packages, settings
                and downloaded data files are not in the zip, so they are kept.

Only the packaged .exe can update itself. When running from source, the update window opens the
release page instead (or use git pull).

Developers: LUMIOSE_UPDATE_URL overrides the release API URL (for testing against a local server).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from tkinter import messagebox, ttk

import za_version

FROZEN = getattr(sys, 'frozen', False)
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
EXE_NAME = 'Lumiose Toolkit.exe'
API_URL = 'https://api.github.com/repos/%s/releases/latest' % za_version.REPO
PAGE_URL = 'https://github.com/%s/releases/latest' % za_version.REPO
TRUSTED_HOSTS = ('github.com', 'api.github.com', 'objects.githubusercontent.com',
                 'release-assets.githubusercontent.com', 'github-releases.githubusercontent.com')
UA = 'Lumiose-Toolkit-updater/' + za_version.VERSION
LOG = os.path.join(APP_DIR, 'update_log.txt')


class UpdateError(Exception):
    pass


def parse_version(v):
    """'v1.10.2' -> (1, 10, 2); anything that isn't a version -> ()."""
    m = re.match(r'v?(\d+(?:\.\d+)*)', (v or '').strip())
    return tuple(int(x) for x in m.group(1).split('.')) if m else ()


def is_newer(v, than=za_version.VERSION):
    a, b = parse_version(v), parse_version(than)
    n = max(len(a), len(b))
    return bool(a) and a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _api_url():
    return os.environ.get('LUMIOSE_UPDATE_URL') or API_URL


def _trusted(url):
    if os.environ.get('LUMIOSE_UPDATE_URL'):          # developer testing against a local server
        return True
    u = urllib.parse.urlparse(url)
    return u.scheme == 'https' and (u.hostname or '').lower() in TRUSTED_HOSTS


def _open(url, timeout, accept=None):
    if not _trusted(url):
        raise UpdateError('Refusing to download from an unexpected address: %s' % url)
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': accept or '*/*'})
    r = urllib.request.urlopen(req, timeout=timeout)
    if not _trusted(r.geturl()):                       # after redirects
        r.close()
        raise UpdateError('The download was redirected to an unexpected address: %s' % r.geturl())
    return r


def check(timeout=8):
    """Latest release on GitHub -> dict(version, tag, name, notes, page, asset_url, asset_name, size, sha256)."""
    try:
        with _open(_api_url(), timeout, 'application/vnd.github+json') as r:
            rel = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError('No published release was found on GitHub (%s). The repository may be private '
                              'or have no releases yet.' % za_version.REPO) from None
        if e.code in (403, 429):
            raise UpdateError('GitHub is limiting requests right now - try again in a while.') from None
        raise UpdateError('GitHub answered with error %d.' % e.code) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise UpdateError("Couldn't reach GitHub (offline?): %s" % getattr(e, 'reason', e)) from None
    asset = next((a for a in rel.get('assets', []) if a.get('name', '').lower().endswith('-windows.zip')), None)
    digest = (asset or {}).get('digest') or ''
    return {
        'version': '.'.join(map(str, parse_version(rel.get('tag_name', '')))) or rel.get('tag_name', ''),
        'tag': rel.get('tag_name', ''),
        'name': rel.get('name') or rel.get('tag_name', ''),
        'notes': (rel.get('body') or '').strip(),
        'page': rel.get('html_url') or PAGE_URL,
        'asset_url': asset and asset.get('browser_download_url'),
        'asset_name': asset and asset.get('name'),
        'size': asset and asset.get('size'),
        'sha256': digest[7:].lower() if digest.startswith('sha256:') else None,
    }


def download(rel, dest, progress=None, cancel=None):
    """Download the release zip to dest, checking size and SHA-256. progress(done, total)."""
    if not rel.get('asset_url'):
        raise UpdateError('This release has no Windows zip to download.')
    h = hashlib.sha256()
    done = 0
    try:
        with _open(rel['asset_url'], 30) as r, open(dest, 'wb') as f:
            total = rel.get('size') or int(r.headers.get('Content-Length') or 0)
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateError('Download cancelled.')
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError('Download failed: %s' % getattr(e, 'reason', e)) from None
    if rel.get('size') and done != rel['size']:
        raise UpdateError('The download is incomplete (%d of %d bytes).' % (done, rel['size']))
    if rel.get('sha256') and h.hexdigest() != rel['sha256']:
        raise UpdateError('The download is damaged (checksum mismatch).')
    return dest


def stage(zip_path, work):
    """Unzip the release into work/ and return the folder that holds Lumiose Toolkit.exe."""
    out = os.path.join(work, 'new')
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():                         # no absolute paths or ../ in a release zip
            p = os.path.normpath(n)
            if os.path.isabs(p) or p.startswith('..'):
                raise UpdateError('The release zip contains an unsafe path: %s' % n)
        z.extractall(out)
    for d, _dirs, files in os.walk(out):
        if EXE_NAME in files:
            return d
    raise UpdateError('The release zip has no %s in it.' % EXE_NAME)


def _ps(s):
    return "'" + s.replace("'", "''") + "'"


def install(src, app_dir=APP_DIR, pid=None, restart=True, work=None):
    """Start the background script that copies src over app_dir once process pid has exited.

    Returns the script path. The caller should close the program right after."""
    exe = os.path.join(app_dir, EXE_NAME)
    work = work or os.path.dirname(src)
    script = os.path.join(work, 'apply_update.ps1')
    lines = [
        '$ErrorActionPreference = "Continue"',
        '$log = %s' % _ps(LOG if app_dir == APP_DIR else os.path.join(app_dir, 'update_log.txt')),
        'function Log($m) { Add-Content -LiteralPath $log -Value ("{0}  {1}" -f (Get-Date -Format s), $m) }',
        'Log "Update: waiting for the toolkit to close"',
    ]
    if pid:
        lines.append('try { Wait-Process -Id %d -Timeout 120 -ErrorAction Stop } catch {}' % int(pid))
    lines += [
        # 3D viewer windows run from the same exe - give them a moment to close too
        '$exe = %s' % _ps(exe),
        'for ($i = 0; $i -lt 60; $i++) {',
        '  $busy = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }',
        '  if (-not $busy) { break }',
        '  Start-Sleep -Milliseconds 500',
        '}',
        'function Tell($m) { Add-Type -AssemblyName System.Windows.Forms; '
        '[System.Windows.Forms.MessageBox]::Show($m, "Lumiose Toolkit update") | Out-Null }',
        # never copy over files a running toolkit or 3D viewer still has open: that mixes two versions
        'if ($busy) { Log "Update NOT applied: the toolkit is still running"; '
        'Tell "The update could not be installed because Lumiose Toolkit (or a 3D viewer window) was still '
        'running. Close it and use Updates... again."; exit 1 }',
        'Log "Update: copying new files"',
        '& robocopy %s %s /E /R:5 /W:2 /NP /NFL /NDL /NJH /NJS | Out-Null' % (_ps(src), _ps(app_dir)),
        '$rc = $LASTEXITCODE',
        'if ($rc -ge 8) { Log "Update FAILED: robocopy exit code $rc"; '
        'Tell "The update could not copy all files (code $rc - is the program folder write-protected, e.g. in '
        'Program Files?). Download the release zip from the Updates... page and unzip it over the program '
        'folder." } else { Log "Update: done" }',
    ]
    if restart:
        lines.append('Start-Process -FilePath $exe -WorkingDirectory %s' % _ps(app_dir))
    lines.append('if ($rc -lt 8) { Remove-Item -LiteralPath %s -Recurse -Force -ErrorAction SilentlyContinue }'
                 % _ps(work))
    with open(script, 'w', encoding='utf-8-sig') as f:
        f.write('\n'.join(lines) + '\n')          # text mode writes CRLF
    subprocess.Popen(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                      '-File', script],
                     creationflags=0x08000000 | 0x00000200,   # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
                     close_fds=True)                          # (not DETACHED_PROCESS: PowerShell needs a console)
    return script


# ---------------------------------------------------------------------------------------------------------
# window
# ---------------------------------------------------------------------------------------------------------

class UpdateWindow(tk.Toplevel):
    """Shows a release found by check() and installs it."""

    def __init__(self, app, rel=None, error=None):
        super().__init__(app)
        self.app = app
        self.rel = rel
        self.cancel = threading.Event()
        self.busy = False
        self.title('Lumiose Toolkit updates')
        self.transient(app)
        self.geometry('620x460')
        self.minsize(460, 300)
        try:
            self.iconbitmap(os.path.join(APP_DIR, 'lumiose_toolkit.ico'))
        except tk.TclError:
            pass
        bg = ttk.Style(self).lookup('TFrame', 'background') or None
        if bg:
            self.configure(background=bg)
        f = ttk.Frame(self, padding=10)
        f.pack(fill='both', expand=True)
        self.head = tk.StringVar()
        ttk.Label(f, textvariable=self.head, font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        self.sub = tk.StringVar()
        ttk.Label(f, textvariable=self.sub, wraplength=580, justify='left').pack(anchor='w', pady=(2, 6))
        box = ttk.Frame(f)
        box.pack(fill='both', expand=True)
        self.notes = tk.Text(box, wrap='word', height=12, relief='flat', padx=8, pady=6, font=('Segoe UI', 9),
                             borderwidth=0, highlightthickness=0, spacing1=1, spacing3=1)
        self.notes.tag_configure('h', font=('Segoe UI', 10, 'bold'), spacing1=6, spacing3=2)
        self.notes.tag_configure('b', font=('Segoe UI', 9, 'bold'))
        self.notes.tag_configure('i', font=('Segoe UI', 9, 'italic'))
        self.notes.tag_configure('li', lmargin1=6, lmargin2=18)
        sb = ttk.Scrollbar(box, command=self.notes.yview)
        self.notes.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.notes.pack(side='left', fill='both', expand=True)
        th = getattr(app, 'theme_colours', None)
        if callable(th):
            c = th()
            self.notes.configure(background=c.get('field', 'white'), foreground=c.get('fg', 'black'),
                                 insertbackground=c.get('fg', 'black'))
        self.pb = ttk.Progressbar(f, mode='determinate')
        self.pb.pack(fill='x', pady=(8, 2))
        self.status = tk.StringVar()
        ttk.Label(f, textvariable=self.status).pack(anchor='w')
        self.auto = tk.BooleanVar(value=app.settings.get('check_updates', True))
        ttk.Checkbutton(f, text='Check for updates when the program starts', variable=self.auto,
                        command=self.save_auto).pack(anchor='w', pady=(6, 0))
        bar = ttk.Frame(f)
        bar.pack(fill='x', pady=(8, 0))
        self.close_btn = ttk.Button(bar, text='Close', command=self.on_close)
        self.close_btn.pack(side='right')
        self.skip_btn = ttk.Button(bar, text='Skip this version', command=self.skip)
        self.page_btn = ttk.Button(bar, text='Release page', command=self.open_page)
        self.go_btn = ttk.Button(bar, text='Download and install', command=self.start)
        self.check_btn = ttk.Button(bar, text='Check again', command=self.recheck)
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.show(rel, error)

    # ---- display ----
    def show(self, rel, error=None):
        self.rel = rel
        for b in (self.go_btn, self.page_btn, self.skip_btn, self.check_btn):
            b.pack_forget()
        self.notes.configure(state='normal')
        self.notes.delete('1.0', 'end')
        self.pb['value'] = 0
        self.status.set('')
        if error:
            self.head.set("Couldn't check for updates")
            self.sub.set(str(error))
            self.notes.insert('end', 'You have Lumiose Toolkit %s.\n\nReleases are published at\n%s'
                             % (za_version.VERSION, PAGE_URL))
            self.check_btn.pack(side='right', padx=(0, 6))
            self.page_btn.pack(side='right', padx=(0, 6))
        elif rel is None:
            self.head.set('Checking for updates…')
            self.sub.set('')
        elif is_newer(rel['version']):
            self.head.set('Lumiose Toolkit %s is available' % rel['version'])
            if FROZEN:
                self.sub.set('You have %s. The update downloads from GitHub, then the toolkit closes, '
                             'updates itself and starts again. Your mods, packages and settings are kept.'
                             % za_version.VERSION)
                if rel.get('asset_url'):
                    self.go_btn.pack(side='right', padx=(0, 6))
            else:
                self.sub.set('You have %s and are running from source: update with "git pull" or download '
                             'the new version from the release page.' % za_version.VERSION)
            self.page_btn.pack(side='right', padx=(0, 6))
            self.skip_btn.pack(side='left')
            self.render_notes(rel['notes'] or '(no release notes)')
        else:
            self.head.set('Lumiose Toolkit is up to date')
            self.sub.set('You have %s, the latest release.' % za_version.VERSION)
            self.render_notes(rel['notes'] or '')
            self.check_btn.pack(side='right', padx=(0, 6))
            self.page_btn.pack(side='right', padx=(0, 6))
        self.notes.configure(state='disabled')

    _INLINE = re.compile(r'(\*\*.+?\*\*|`[^`]+`|(?<![\w*])\*[^*\s][^*]*?\*(?![\w*])|\[[^\]]+\]\([^)]+\))')

    def render_notes(self, md):
        """Very small Markdown renderer for GitHub release notes: headings, lists, bold, italic, links, code."""
        for raw in md.replace('\r\n', '\n').split('\n'):
            line = raw.rstrip()
            m = re.match(r'#{1,6}\s+(.*)', line)
            if m:
                self.notes.insert('end', self._plain(m.group(1)) + '\n', 'h')
                continue
            tags = ()
            m = re.match(r'(\s*)[-*+]\s+(.*)', line)
            if m:
                line, tags = '• ' + m.group(2), ('li',)
            elif re.match(r'\s*\d+\.\s+', line):
                tags = ('li',)
            for part in self._INLINE.split(line):
                if not part:
                    continue
                if part.startswith('**') and part.endswith('**') and len(part) > 4:
                    self.notes.insert('end', part[2:-2], tags + ('b',))
                elif part.startswith('`') and part.endswith('`'):
                    self.notes.insert('end', part[1:-1], tags)
                elif part.startswith('[') and '](' in part:
                    self.notes.insert('end', part[1:part.index('](')], tags)
                elif part.startswith('*') and part.endswith('*') and len(part) > 2:
                    self.notes.insert('end', part[1:-1], tags + ('i',))
                else:
                    self.notes.insert('end', part, tags)
            self.notes.insert('end', '\n', tags)

    @staticmethod
    def _plain(s):
        return re.sub(r'\*\*|`', '', s)

    def recheck(self):
        self.show(None)

        def work():
            try:
                rel, err = check(), None
            except UpdateError as e:
                rel, err = None, e
            self.app.q.put(lambda: self.winfo_exists() and self.show(rel, err))
            if rel is not None:
                self.app.q.put(lambda: self.app.update_found(rel))
        threading.Thread(target=work, daemon=True).start()

    # ---- actions ----
    def save_auto(self):
        self.app.settings['check_updates'] = self.auto.get()
        self.app.save_settings()

    def skip(self):
        self.app.settings['skip_version'] = self.rel['version']
        self.app.save_settings()
        self.app.update_found(None)
        self.destroy()

    def open_page(self):
        import webbrowser
        webbrowser.open((self.rel or {}).get('page') or PAGE_URL)

    def start(self):
        if self.busy:
            return
        busy = [w for w in getattr(self.app, 'extract_busy', lambda: [])()]
        if busy:
            messagebox.showinfo('Lumiose Toolkit', 'Please wait until %s has finished.' % busy[0], parent=self)
            return
        self.busy = True
        self.go_btn.configure(state='disabled')
        self.skip_btn.configure(state='disabled')
        self.close_btn.configure(text='Cancel')
        rel = self.rel
        work = tempfile.mkdtemp(prefix='lumiose_update_')
        zpath = os.path.join(work, rel['asset_name'] or 'update.zip')

        def prog(done, total):
            self.app.q.put(lambda: self._prog(done, total))

        def run():
            try:
                download(rel, zpath, prog, self.cancel)
                self.app.q.put(lambda: self.status.set('Unpacking…'))
                src = stage(zpath, work)
                os.remove(zpath)
                self.app.q.put(lambda: self.ready(src, work))
            except Exception as e:  # noqa: BLE001
                shutil.rmtree(work, ignore_errors=True)
                msg = str(e) if isinstance(e, UpdateError) else 'Update failed: %r' % e
                self.app.q.put(lambda: self.failed(msg))
        threading.Thread(target=run, daemon=True).start()

    def _prog(self, done, total):
        if not self.winfo_exists():
            return
        if total:
            self.pb['value'] = 100.0 * done / total
            self.status.set('Downloading… %.1f of %.1f MB' % (done / 1e6, total / 1e6))
        else:
            self.status.set('Downloading… %.1f MB' % (done / 1e6))

    def failed(self, msg):
        self.busy = False
        if not self.winfo_exists():
            return
        self.status.set(msg)
        self.go_btn.configure(state='normal')
        self.skip_btn.configure(state='normal')
        self.close_btn.configure(text='Close')
        self.pb['value'] = 0

    def ready(self, src, work):
        self.pb['value'] = 100
        self.status.set('Ready to install %s.' % self.rel['version'])
        if not messagebox.askokcancel(
                'Lumiose Toolkit', 'Lumiose Toolkit will now close, install %s and start again.\n\n'
                'Close any open 3D viewer windows. Edits are saved into your mod as you make them; '
                'anything still unsaved in an open editor window will be lost.' % self.rel['version'],
                parent=self):
            shutil.rmtree(work, ignore_errors=True)
            self.failed('Install cancelled - nothing was changed.')
            return
        try:
            install(src, pid=os.getpid(), work=work)
        except Exception as e:  # noqa: BLE001
            shutil.rmtree(work, ignore_errors=True)
            self.failed("Couldn't start the installer: %s" % e)
            return
        self.app.on_close()

    def on_close(self):
        if self.busy:
            self.cancel.set()
        self.destroy()
