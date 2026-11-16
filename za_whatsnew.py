"""What's new window: the patch notes (CHANGELOG.md), shown once after each update and from the "Patch notes" button.

CHANGELOG.md lists every version as "## x.y.z - date" with "### New / Improved / Fixed" sections.
settings.json remembers the last version whose notes were shown ("last_seen_version").
"""
import os
import re
import sys
import tkinter as tk
import webbrowser
from tkinter import ttk

import za_version

FROZEN = getattr(sys, 'frozen', False)
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
CHANGELOG = os.path.join(APP_DIR, 'CHANGELOG.md')
RELEASES_URL = 'https://github.com/%s/releases' % za_version.REPO


def _ver(v):
    return tuple(int(x) for x in re.findall(r'\d+', v or '')[:3]) or (0,)


def load(path=CHANGELOG):
    """[(version, date, markdown body)] newest first."""
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read()
    except OSError:
        return []
    out = []
    for m in re.finditer(r'(?m)^##\s+(\d+\.\d+\.\d+)\s*(?:[-–]\s*(.+?))?\s*$', text):
        end = text.find('\n## ', m.end())
        out.append((m.group(1), (m.group(2) or '').strip(), text[m.end():end if end >= 0 else len(text)].strip()))
    return out


def startup_check(settings):
    """Call once at startup. Returns (show, since): show the notes when this version is newer than the
    one last seen. A brand-new install (no settings yet) doesn't show them; an update from a version
    before 1.3.2 (settings but no last_seen_version) does."""
    last = settings.get('last_seen_version')
    if last is None:
        return (bool(settings), None)
    return (_ver(za_version.VERSION) > _ver(last), last)


_INLINE = re.compile(r'(\*\*.+?\*\*|`[^`]+`|(?<![\w*])\*[^*\s][^*]*?\*(?![\w*])|\[[^\]]+\]\([^)]+\))')


def render(text, md, base_tags=()):
    """Small Markdown renderer: ### headings, lists (nested by indent), bold, italic, code, links."""
    for raw in md.replace('\r\n', '\n').split('\n'):
        line = raw.rstrip()
        if not line:
            text.insert('end', '\n', base_tags + ('gap',))
            continue
        m = re.match(r'#{3,6}\s+(.*)', line)
        if m:
            text.insert('end', m.group(1).replace('**', '') + '\n', base_tags + ('sec',))
            continue
        tags = base_tags
        m = re.match(r'(\s*)[-*+]\s+(.*)', line)
        if m:
            line = '• ' + m.group(2)
            tags = base_tags + (('li2',) if len(m.group(1)) >= 2 else ('li',))
        for part in _INLINE.split(line):
            if not part:
                continue
            if part.startswith('**') and part.endswith('**') and len(part) > 4:
                text.insert('end', part[2:-2], tags + ('b',))
            elif part.startswith('`') and part.endswith('`'):
                text.insert('end', part[1:-1], tags + ('code',))
            elif part.startswith('[') and '](' in part:
                text.insert('end', part[1:part.index('](')], tags)
            elif part.startswith('*') and part.endswith('*') and len(part) > 2:
                text.insert('end', part[1:-1], tags + ('i',))
            else:
                text.insert('end', part, tags)
        text.insert('end', '\n', tags)


class WhatsNewWindow(tk.Toplevel):
    def __init__(self, app, since=None, after_update=False):
        super().__init__(app)
        self.app = app
        self.title("What's new in Lumiose Toolkit")
        self.transient(app)
        self.geometry('700x620')
        self.minsize(480, 360)
        try:
            self.iconbitmap(os.path.join(APP_DIR, 'lumiose_toolkit.ico'))
        except tk.TclError:
            pass
        bg = ttk.Style(self).lookup('TFrame', 'background') or None
        if bg:
            self.configure(background=bg)
        c = app.theme_colours() if callable(getattr(app, 'theme_colours', None)) else {}
        f = ttk.Frame(self, padding=10)
        f.pack(fill='both', expand=True)
        notes = load()
        new = [n for n in notes if since is None and n[0] == za_version.VERSION or
               since is not None and _ver(since) < _ver(n[0]) <= _ver(za_version.VERSION)]
        if after_update:
            head = 'Updated to %s' % za_version.VERSION
            sub = ('Here is what changed since %s.' % since) if since else "Here is what's new in this version."
        else:
            head, sub = "What's new", 'You have Lumiose Toolkit %s.' % za_version.VERSION
        ttk.Label(f, text=head, font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        ttk.Label(f, text=sub).pack(anchor='w', pady=(0, 8))
        box = ttk.Frame(f)
        box.pack(fill='both', expand=True)
        self.text = tk.Text(box, wrap='word', relief='flat', padx=12, pady=8, font=('Segoe UI', 10),
                            borderwidth=0, highlightthickness=0, spacing1=1, spacing3=1, cursor='arrow')
        sb = ttk.Scrollbar(box, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.text.pack(side='left', fill='both', expand=True)
        fg, accent, dim = c.get('fg', 'black'), c.get('accent', '#0063b1'), c.get('dim', '#6a6a6a')
        self.text.configure(background=c.get('field', 'white'), foreground=fg)
        t = self.text
        t.tag_configure('ver', font=('Segoe UI', 13, 'bold'), spacing1=14, spacing3=2)
        t.tag_configure('date', font=('Segoe UI', 9), foreground=dim)
        t.tag_configure('badge', font=('Segoe UI', 8, 'bold'), foreground='white', background=accent)
        t.tag_configure('sec', font=('Segoe UI', 10, 'bold'), foreground=accent, spacing1=8, spacing3=2)
        t.tag_configure('b', font=('Segoe UI', 10, 'bold'))
        t.tag_configure('i', font=('Segoe UI', 10, 'italic'))
        t.tag_configure('code', font=('Consolas', 9))
        t.tag_configure('li', lmargin1=8, lmargin2=22)
        t.tag_configure('li2', lmargin1=26, lmargin2=40)
        t.tag_configure('gap', font=('Segoe UI', 4))
        t.tag_configure('older', foreground=fg)
        t.tag_configure('rule', font=('Segoe UI', 10, 'bold'), foreground=dim, spacing1=16, spacing3=4)
        if not notes:
            t.insert('end', 'The patch notes (CHANGELOG.md) are missing from the program folder.\n'
                            'All release notes are on GitHub: %s' % RELEASES_URL)
        shown_rule = False
        for ver, date, body in notes:
            if new and ver not in [n[0] for n in new] and not shown_rule:
                t.insert('end', 'Earlier versions\n', ('rule',))
                shown_rule = True
            t.insert('end', ver + '  ', ('ver',))
            if ver in [n[0] for n in new]:
                t.insert('end', ' NEW ', ('ver', 'badge'))
                t.insert('end', '  ', ('ver',))
            t.insert('end', date + '\n', ('ver', 'date'))
            render(t, body)
        t.configure(state='disabled')
        bar = ttk.Frame(f)
        bar.pack(fill='x', pady=(8, 0))
        ttk.Button(bar, text='Close', command=self.destroy).pack(side='right')
        ttk.Button(bar, text='All releases on GitHub',
                   command=lambda: webbrowser.open(RELEASES_URL)).pack(side='right', padx=6)
        self.bind('<Escape>', lambda e: self.destroy())
