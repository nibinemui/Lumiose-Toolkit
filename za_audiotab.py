"""Audio tab: every song, jingle, sound effect, cry and ambience of the game - searchable by name, playable,
and replaceable in the current mod (any audio format, loop points, volume). Engine: za_soundbank."""
import os
import shutil
import tempfile
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

import za_modding as zm
import za_soundbank as sb

AUDIO_FILES = [('Audio files', '*.wav *.mp3 *.ogg *.flac *.opus *.m4a *.aac *.aif *.aiff *.wma *.mp2 *.ac3'),
               ('All files', '*.*')]
MAX_ROWS = 3000
MUSIC_LOOP_HELP = ('Songs: the game repeats the part between Loop start and Loop end. The part before Loop start '
                   'is the intro - Wwise may also play it as a lead-in into each repeat, so keep a long intro short '
                   'or start the loop at 0. Without loop points the whole file loops.')
SOUND_LOOP_HELP = ('Sounds: loop points are used when the game loops this sound (ambience and other "_LP" '
                   'sounds). A sound the game plays once ignores them.')


def _log(app):
    import sys
    mod = sys.modules.get('za_extractor') or sys.modules.get('__main__')
    return getattr(mod, 'log_error', lambda t: None)


def _purge_old_temp(hours=6):
    """Decoded sounds of earlier sessions (often tens of MB) left in the temp folder."""
    import shutil
    import time
    base = tempfile.gettempdir()
    try:
        names = [n for n in os.listdir(base) if n.startswith('lumiose_audio_')]
    except OSError:
        return
    for n in names:
        p = os.path.join(base, n)
        try:
            if os.path.isdir(p) and time.time() - os.path.getmtime(p) > hours * 3600:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


class AudioTab:
    def __init__(self, app, parent):
        self.app = app
        self.parent = parent
        self.index = None
        self.entries = []
        self.shown = []
        self.cur = None
        self.changed = set()
        self.loading = False
        _purge_old_temp()
        self.tmp = tempfile.mkdtemp(prefix='lumiose_audio_')
        self.decode_locks, self.locks_lock = {}, threading.Lock()
        self.src_info = None
        self.build()
        app.nb.bind('<<NotebookTabChanged>>', self.on_tab, add='+')

    # ------------------------------------------------------------------ UI
    def build(self):
        P = self.parent
        top = ttk.Frame(P)
        top.pack(fill='x', padx=4, pady=(4, 0))
        ttk.Label(top, text='Search:').pack(side='left')
        self.q = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.q, width=40)
        e.pack(side='left', padx=4)
        self.q.trace_add('write', lambda *a: self.schedule_fill())
        ttk.Label(top, text='Type:').pack(side='left', padx=(8, 0))
        self.type_var = tk.StringVar(value='All types')
        tb = ttk.Combobox(top, textvariable=self.type_var, state='readonly', width=14,
                          values=['All types'] + list(sb.TYPES))
        tb.pack(side='left', padx=4)
        tb.bind('<<ComboboxSelected>>', lambda e: self.fill())
        self.named_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text='Named only', variable=self.named_var, command=self.fill).pack(side='left', padx=6)
        self.changed_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text='Changed in current mod', variable=self.changed_var,
                        command=self.fill).pack(side='left', padx=6)
        ttk.Button(top, text='Rebuild list', command=lambda: self.load(rebuild=True)).pack(side='right')
        self.count = tk.StringVar()
        ttk.Label(top, textvariable=self.count, style='Dim.TLabel').pack(side='right', padx=8)

        paned = ttk.PanedWindow(P, orient='horizontal')
        paned.pack(fill='both', expand=True, padx=4, pady=4)
        left = ttk.Frame(paned)
        paned.add(left, weight=3)
        cols = ('type', 'category', 'length', 'changed')
        self.tree = ttk.Treeview(left, columns=cols, show='tree headings', selectmode='browse')
        self.tree.heading('#0', text='Name', anchor='w')
        for c, t, w in (('type', 'Type', 100), ('category', 'Group', 110), ('length', 'Length', 60),
                        ('changed', 'In mod', 55)):
            self.tree.heading(c, text=t, anchor='w')
            self.tree.column(c, width=w, stretch=False, anchor='w')
        self.tree.column('#0', width=430, stretch=True)
        sb_ = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb_.set)
        sb_.pack(side='right', fill='y')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.select())
        self.tree.bind('<Double-Button-1>', lambda e: self.play())
        self.tree.bind('<space>', lambda e: (self.play(), 'break')[1])

        right = ttk.Frame(paned, padding=(8, 0, 0, 0))
        paned.add(right, weight=2)
        self.title = tk.StringVar(value='Pick a sound or song.')
        ttk.Label(right, textvariable=self.title, style='Head.TLabel', wraplength=460).pack(anchor='w')
        self.details = tk.StringVar()
        ttk.Label(right, textvariable=self.details, justify='left', wraplength=460).pack(anchor='w', pady=(2, 6))
        pb = ttk.Frame(right)
        pb.pack(fill='x')
        self.play_btn = ttk.Button(pb, text='▶ Play', command=self.play)
        self.play_btn.pack(side='left')
        self.play_orig_btn = ttk.Button(pb, text='▶ Play original', command=lambda: self.play(original=True))
        self.play_orig_btn.pack(side='left', padx=4)
        ttk.Button(pb, text='■ Stop', command=self.stop).pack(side='left')
        ttk.Button(pb, text='Export WAV…', command=self.export).pack(side='left', padx=4)
        self.restore_btn = ttk.Button(pb, text='Restore original', command=self.restore)
        self.restore_btn.pack(side='left')

        rep = ttk.LabelFrame(right, text=' Replace in current mod ', padding=8)
        rep.pack(fill='x', pady=(10, 0))
        r1 = ttk.Frame(rep)
        r1.pack(fill='x')
        ttk.Label(r1, text='Audio file:').pack(side='left')
        self.file_var = tk.StringVar()
        ttk.Entry(r1, textvariable=self.file_var).pack(side='left', fill='x', expand=True, padx=4)
        ttk.Button(r1, text='Browse…', command=self.pick_file).pack(side='left')
        self.file_info = tk.StringVar(value='WAV, MP3, OGG, FLAC, M4A and more.')
        ttk.Label(rep, textvariable=self.file_info, style='Dim.TLabel').pack(anchor='w', pady=(2, 4))
        r2 = ttk.Frame(rep)
        r2.pack(fill='x')
        ttk.Label(r2, text='Volume:').pack(side='left')
        self.gain = tk.DoubleVar(value=0.0)
        self.gain_lbl = tk.StringVar(value='+0.0 dB')
        ttk.Scale(r2, from_=-24, to=12, variable=self.gain, length=180,
                  command=lambda v: self.gain_lbl.set('%+.1f dB' % float(v))).pack(side='left', padx=4)
        ttk.Label(r2, textvariable=self.gain_lbl, width=8).pack(side='left')
        ttk.Button(r2, text='0', width=3, command=lambda: (self.gain.set(0), self.gain_lbl.set('+0.0 dB'))).pack(side='left')
        self.match_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r2, text="Match the original's loudness", variable=self.match_var).pack(side='left', padx=8)
        r3 = ttk.Frame(rep)
        r3.pack(fill='x', pady=(6, 0))
        self.loop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r3, text='Loop points:', variable=self.loop_var).pack(side='left')
        ttk.Label(r3, text='start').pack(side='left', padx=(4, 2))
        self.loop_start = tk.StringVar(value='0.00')
        ttk.Spinbox(r3, from_=0, to=36000, increment=0.1, textvariable=self.loop_start, width=8).pack(side='left')
        ttk.Label(r3, text='s   end').pack(side='left', padx=(2, 2))
        self.loop_end = tk.StringVar(value='')
        ttk.Spinbox(r3, from_=0, to=36000, increment=0.1, textvariable=self.loop_end, width=8).pack(side='left')
        ttk.Label(r3, text='s').pack(side='left', padx=2)
        self.loop_help = tk.StringVar(value=SOUND_LOOP_HELP)
        ttk.Label(rep, textvariable=self.loop_help, style='Dim.TLabel', wraplength=440, justify='left').pack(
            anchor='w', pady=(4, 6))
        r4 = ttk.Frame(rep)
        r4.pack(fill='x')
        ttk.Button(r4, text='▶ Preview', command=self.preview).pack(side='left')
        self.replace_btn = ttk.Button(r4, text='Replace in current mod', command=self.replace)
        self.replace_btn.pack(side='right')
        ttk.Button(r4, text='Add as new battle music…', command=self.add_battle_music).pack(side='right', padx=6)

        self.report = tk.Text(right, height=7, wrap='word', relief='flat', borderwidth=0, highlightthickness=0,
                              font=('Segoe UI', 9), padx=6, pady=4)
        self.report.pack(fill='both', expand=True, pady=(8, 0))
        self.report.configure(state='disabled')
        self.status = tk.StringVar(value='The list loads when you open this tab.')
        ttk.Label(P, textvariable=self.status, style='Dim.TLabel').pack(anchor='w', padx=6, pady=(0, 2))
        self.recolour()

    def recolour(self):
        c = self.app.theme_colours() if callable(getattr(self.app, 'theme_colours', None)) else {}
        self.report.configure(background=c.get('field', 'white'), foreground=c.get('fg', 'black'))

    # ------------------------------------------------------------------ loading
    def on_tab(self, _e=None):
        try:
            if self.app.nb.select() != str(self.parent):
                return
        except tk.TclError:
            return
        self.recolour()
        if self.index is None and not self.loading:
            self.load()
        else:
            self.refresh_changed()

    def root(self):
        return self.app.root_var.get()

    def load(self, rebuild=False):
        root = self.root()
        if not root or not os.path.isdir(sb.bank_dir(root)):
            self.status.set('No sound banks found - import your game files first (Import game files…).')
            return
        idx = None if rebuild else sb.load_index(root)
        if idx is not None:
            self.loaded(idx)
            return
        self.loading = True
        self.status.set('Building the sound list (first time only, about a minute)…')

        def work():
            try:
                i = sb.build_index(root, progress=lambda m: self.app.q.put(lambda: self.status.set(m)))
                self.app.q.put(lambda: self.loaded(i))
            except Exception as ex:  # noqa: BLE001
                _log(self.app)(traceback.format_exc())
                msg = str(ex)
                self.app.q.put(lambda: (self.status.set('Could not build the sound list: ' + msg),
                                        setattr(self, 'loading', False)))
        threading.Thread(target=work, daemon=True).start()

    def loaded(self, idx):
        self.loading = False
        self.index = idx
        self.entries = idx['entries']
        self.by_id = {e['id']: e for e in self.entries}
        self.status.set('%d sounds and songs, %d with names. Double-click or Space plays. Names come from the '
                        "game's own sound events." % (len(self.entries), idx.get('named', 0)))
        self.refresh_changed(fill=False)
        self.fill()

    def refresh_changed(self, fill=True):
        try:
            self.changed = sb.changed_ids(self.root(), self.app.current_mod(ask=False))
        except Exception:  # noqa: BLE001
            self.changed = set()
        if fill and self.index is not None:
            self.fill()

    # ------------------------------------------------------------------ list
    def schedule_fill(self):
        if getattr(self, '_after', None):
            self.parent.after_cancel(self._after)
        self._after = self.parent.after(250, self.fill)

    def fill(self):
        self._after = None
        if self.index is None:
            return
        words = [w for w in self.q.get().lower().replace('_', ' ').split() if w]
        ty = self.type_var.get()
        rows = []
        for e in self.entries:
            if ty != 'All types' and e['type'] != ty:
                continue
            if self.named_var.get() and not e['name']:
                continue
            if self.changed_var.get() and e['id'] not in self.changed:
                continue
            if words and not sb.matches(e, words):
                continue
            rows.append(e)
        # named first, then by name and part
        rows.sort(key=lambda e: (not e['name'], e['name'], (e.get('part') or [0])[0], e['id']))
        self.shown = rows
        self.tree.delete(*self.tree.get_children())
        for e in rows[:MAX_ROWS]:
            ln = e.get('length')
            self.tree.insert('', 'end', iid=str(e['id']), text=sb.label(e),
                             values=(e['type'], e['category'], ('%.1f s' % ln) if ln else '',
                                     '✓' if e['id'] in self.changed else ''))
        more = ' (showing the first %d - search to narrow it down)' % MAX_ROWS if len(rows) > MAX_ROWS else ''
        self.count.set('%d found%s' % (len(rows), more))
        if self.cur and str(self.cur['id']) in self.tree.get_children():
            self.tree.selection_set(str(self.cur['id']))
            self.tree.see(str(self.cur['id']))

    def reveal(self, media_id):
        """Show one sound (e.g. from Browse): clears filters and selects it."""
        self.app.nb.select(self.parent)
        if self.index is None:
            self.parent.after(500, lambda: self.reveal(media_id))
            return
        self.q.set('')
        self.type_var.set('All types')
        self.named_var.set(False)
        self.changed_var.set(False)
        e = self.by_id.get(media_id)
        if e is not None:
            self.q.set(str(media_id))
            self.fill()
            self.tree.selection_set(str(media_id))

    # ------------------------------------------------------------------ details
    def select(self):
        sel = self.tree.selection()
        if not sel:
            return
        e = self.by_id.get(int(sel[0]))
        if e is None:
            return
        self.cur = e
        self.title.set(sb.label(e))
        stored = {'bank': 'in %s.bnk' % (e.get('bank_file') or e['bank']),
                  'stream': 'streamed from %s' % e.get('package', ''), 'missing': 'no audio data'}[e['where']]
        lines = ['%s · %s · %s, %s' % (e['type'], e['category'], e['codec'], stored),
                 'Audio id %d · used by %s.bnk' % (e['id'], e['bank'])]
        if e.get('length'):
            lines.append('Length %.2f s' % e['length'])
        if len(e.get('events', [])) > 1:
            lines.append('Also played by: ' + ', '.join(e['events'][1:6]) + (' …' if len(e['events']) > 6 else ''))
        if e['id'] in self.changed:
            lines.append('Your current mod has its own version of this sound.')
        if not e.get('replaceable', True):
            lines.append('This is a %s placeholder, not an audio file - it cannot be played or replaced.' % e['codec'])
        self.details.set('\n'.join(lines))
        self.loop_help.set(MUSIC_LOOP_HELP if e['music'] else SOUND_LOOP_HELP)
        changed = e['id'] in self.changed
        self.play_orig_btn.configure(state='normal' if changed else 'disabled')
        self.restore_btn.configure(state='normal' if changed else 'disabled')
        self.replace_btn.configure(state='normal' if e.get('replaceable', True) else 'disabled')
        if not e.get('length') and e.get('replaceable', True):
            self.probe(e)

    def probe(self, e):
        """Length and loop of the original, read in the background."""
        root = self.root()

        def work():
            try:
                wav = self.decoded(e, root, original=True)
                info = sb.info(wav)
                self.app.q.put(lambda: self.probed(e, info))
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=work, daemon=True).start()

    def probed(self, e, info):
        e['length'] = round(info['seconds'], 2)
        if self.cur is e:
            self.details.set(self.details.get().replace('Audio id', 'Length %.2f s, %d Hz, %d channel(s)\nAudio id'
                                                        % (info['seconds'], info['rate'], info['channels']), 1))
        if str(e['id']) in self.tree.get_children():
            self.tree.set(str(e['id']), 'length', '%.1f s' % info['seconds'])

    # ------------------------------------------------------------------ playback
    def decoded(self, e, root, mod=None, original=False):
        """WAV of a sound (runs on worker threads: root and mod are read on the main thread by the caller)."""
        mine = (not original) and mod is not None and e['id'] in self.changed
        p = os.path.join(self.tmp, '%d%s.wav' % (e['id'], '_mod' if mine else ''))
        with self.locks_lock:
            lock = self.decode_locks.setdefault(p, threading.Lock())
        with lock:                    # selecting (length probe) and playing may decode the same sound at once
            if mine or not os.path.isfile(p):
                part = p[:-4] + '.part.wav'
                sb.decode(root, e, part, mod if mine else None)
                os.replace(part, p)   # a half-written WAV is never played or kept
        return p

    def play(self, original=False):
        e = self.cur
        if e is None or not e.get('replaceable', True):
            return
        self.status.set('Loading %s…' % sb.label(e))
        root, mod = self.root(), self.app.current_mod(ask=False)

        def work():
            try:
                p = self.decoded(e, root, mod, original)
                self.app.q.put(lambda: self._play_file(p, '%s%s' % ('Playing original: ' if original else 'Playing: ',
                                                                    sb.label(e))))
            except Exception as ex:  # noqa: BLE001
                msg = str(ex)
                self.app.q.put(lambda: self.status.set('Could not play it: ' + msg))
        threading.Thread(target=work, daemon=True).start()

    def _play_file(self, path, note):
        import winsound
        winsound.PlaySound(None, 0)
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        self.status.set(note + '   (■ Stop to stop)')

    def stop(self):
        import winsound
        winsound.PlaySound(None, 0)
        self.status.set('Stopped.')

    def export(self):
        e = self.cur
        if e is None:
            return
        name = (e['name'] or 'sound_%d' % e['id']) + ('_part%d' % e['part'][0] if e.get('part') else '') + '.wav'
        p = filedialog.asksaveasfilename(defaultextension='.wav', initialfile=name, initialdir=self.app.out_var.get(),
                                         filetypes=[('WAV audio', '*.wav')])
        if p:
            root, mod = self.root(), self.app.current_mod(ask=False)
            self.app.run_bg('Exporting sound', lambda: shutil.copyfile(self.decoded(e, root, mod), p),
                            lambda r: self.status.set('Saved ' + p))

    # ------------------------------------------------------------------ replace
    def pick_file(self):
        p = filedialog.askopenfilename(title='Audio to use', filetypes=AUDIO_FILES)
        if not p:
            return
        self.file_var.set(p)
        self.file_info.set('Reading…')

        def work():
            try:
                rate, s = sb.load_audio(p)
                info = (rate, s.shape[1], len(s) / rate)
                self.app.q.put(lambda: self.file_read(info))
            except Exception as ex:  # noqa: BLE001
                msg = str(ex)
                self.app.q.put(lambda: self.file_info.set("Can't read this file: " + msg))
        threading.Thread(target=work, daemon=True).start()

    def file_read(self, info):
        rate, ch, secs = info
        self.src_info = info
        self.file_info.set('%.2f s, %d Hz, %d channel(s) - stored as 16-bit PCM (about %.0f MB)'
                           % (secs, rate, ch, secs * rate * ch * 2 / 1e6))
        self.loop_start.set('0.00')
        self.loop_end.set('%.2f' % secs)

    def options(self):
        loop = None
        if self.loop_var.get():
            try:
                loop = (float(self.loop_start.get()), float(self.loop_end.get()))
            except ValueError:
                raise sb.SoundError('Loop start and end must be numbers (seconds).')
            if loop[1] <= loop[0]:
                raise sb.SoundError('Loop end must be after loop start.')
        return dict(gain_db=float(self.gain.get()), match_loudness=self.match_var.get(), loop=loop)

    def preview(self):
        p = self.file_var.get()
        if not p:
            messagebox.showinfo('Preview', 'Pick an audio file first.')
            return
        try:
            opt = self.options()
        except sb.SoundError as ex:
            messagebox.showerror('Preview', str(ex))
            return
        e = self.cur
        root = self.root()

        def work():
            import numpy as np
            import za_audio
            rate, s = sb.load_audio(p)
            gain = opt['gain_db']
            if opt['match_loudness'] and e is not None:
                _, o = za_audio.read_wav(self.decoded(e, root, original=True))
                gain += sb.rms_db(o) - sb.rms_db(s)
                peak = int(np.abs(s.astype(np.int32)).max()) or 1
                gain = min(gain, 20 * np.log10(32767.0 / peak))
            s = sb.apply_gain(s, gain)
            if opt['loop']:                       # hear the loop seam: from 3 s before the loop end, twice
                a, b = int(opt['loop'][0] * rate), int(opt['loop'][1] * rate)
                lead = s[max(a, b - 3 * rate):b]
                s = np.concatenate([lead, s[a:b], s[a:min(b, a + 3 * rate)]])
            out = os.path.join(self.tmp, 'preview.wav')
            import wave
            with wave.open(out, 'wb') as w:              # plain WAV for Windows' player
                w.setnchannels(s.shape[1])
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(np.ascontiguousarray(s, '<i2').tobytes())
            return out, gain
        self.app.run_bg('Preparing preview', work,
                        lambda r: self._play_file(r[0], 'Preview (%+.1f dB%s)' % (r[1], ', around the loop seam'
                                                                                  if opt['loop'] else '')))

    def replace(self):
        e = self.cur
        p = self.file_var.get()
        if e is None:
            messagebox.showinfo('Replace', 'Pick a sound or song in the list first.')
            return
        if not p or not os.path.isfile(p):
            messagebox.showinfo('Replace', 'Pick an audio file first (Browse…).')
            return
        try:
            opt = self.options()
        except sb.SoundError as ex:
            messagebox.showerror('Replace', str(ex))
            return
        m = self.app.current_mod()
        if not m:
            return
        if e.get('part') and e['part'][1] > 1 and e['music'] and not getattr(self, '_parts_told', False):
            self._parts_told = True
            if not messagebox.askokcancel(
                    'Replace song part',
                    'This song is made of %d parts (intro, loop, layers or variations) and this replaces part %d '
                    'only. Play the parts to find the one you want. Continue?' % (e['part'][1], e['part'][0])):
                return
        root = self.root()

        def job():
            try:
                m.snapshot()
            except Exception:  # noqa: BLE001
                pass
            rep = sb.replace(root, m, e, p, **opt)
            try:
                m.snapshot('sound replaced: %s' % (e['name'] or e['id']))
            except Exception:  # noqa: BLE001
                pass
            return rep

        def done(rep):
            self.show_report(rep)
            self.refresh_changed()
            self.select()
            self.app.mod_changed(m, '%s/%s.bnk' % (sb.BANK_DIR, e['bank']), 'sound replaced')
        self.app.run_bg('Replacing sound', job, done)

    def add_battle_music(self):
        """The audio file as a new song in the battle music picker - no game song is replaced."""
        from tkinter import simpledialog
        import za_bgm
        p = self.file_var.get()
        if not p or not os.path.isfile(p):
            messagebox.showinfo('Add battle music', 'Pick an audio file first (Browse…). Volume and loop points '
                                'below are used too.')
            return
        try:
            opt = self.options()
        except sb.SoundError as ex:
            messagebox.showerror('Add battle music', str(ex))
            return
        m = self.app.current_mod()
        if not m:
            return
        title = simpledialog.askstring(
            'Add battle music', '%s\n\nName in the Private Battles "Battle BGM" list (all languages):' % zm.EXPERIMENTAL, parent=self.app,
            initialvalue=os.path.splitext(os.path.basename(p))[0][:40])
        if not title or not title.strip():
            return
        trainer = messagebox.askyesnocancel(
            'Add battle music', 'Also play it in trainer battles (Battle Zones and trainers who challenge you), '
            'instead of the normal trainer battle theme?\n\nThe game\'s own theme is kept in the game files; '
            'removing the mod brings it back. Wild Pokémon battles keep their music (it is part of the area '
            'music, not one song).', parent=self.app)
        if trainer is None:
            return
        root = self.root()
        ref = next((e for e in self.entries if any(n.startswith('PLAY_BGM_IK_VS_SELECT_') for n in e.get('events') or [])
                    and e.get('replaceable', True)), None)          # a battle song of the game's own

        def job():
            gain = opt['gain_db']
            if opt['match_loudness'] and ref is not None:     # as loud as the game's own battle music
                import numpy as np
                import za_audio
                _, o = za_audio.read_wav(self.decoded(ref, root, original=True))
                rate, s_ = sb.load_audio(p)
                gain += sb.rms_db(o) - sb.rms_db(s_)
                peak = int(np.abs(s_.astype(np.int32)).max()) or 1
                gain = min(gain, 20 * np.log10(32767.0 / peak))
            try:
                m.snapshot()
            except Exception:  # noqa: BLE001
                pass
            rep_ = za_bgm.add_battle_song(root, m, p, title, loop=opt['loop'], gain_db=gain, trainer=trainer)
            if gain:
                rep_.append('volume %+.1f dB' % gain)
            try:
                m.snapshot('new battle music: %s' % title)
            except Exception:  # noqa: BLE001
                pass
            return rep_

        def done(rep_):
            self.show_report(rep_ + ['The "Battle BGM" list is in online Private Battles (Create / Join a Room); '
                                     'it is only used there.' + (' Trainer battles play the song too.' if trainer else
                                                                ' Battle Zones and other battles keep their music.') +
                                     ' Test with a copy of your save.'])
            self.app.mod_changed(m, za_bgm.PICKER, 'new battle music')
        self.app.run_bg('Adding battle music', job, done)

    def restore(self):
        e = self.cur
        m = self.app.current_mod(ask=False)
        if e is None or m is None:
            return
        root = self.root()

        def job():
            m.snapshot()
            r = sb.restore(root, m, e)
            m.snapshot('sound restored: %s' % (e['name'] or e['id']))
            return r

        def done(r):
            self.show_report(['original restored in %s' % ', '.join(b + '.bnk' for b in r)])
            for f in os.listdir(self.tmp):
                if f.startswith('%d_mod' % e['id']):
                    os.remove(os.path.join(self.tmp, f))
            self.refresh_changed()
            self.select()
            self.app.mod_changed(m, '%s/%s.bnk' % (sb.BANK_DIR, e['bank']), 'sound restored')
        self.app.run_bg('Restoring sound', job, done)

    def show_report(self, lines):
        self.report.configure(state='normal')
        self.report.delete('1.0', 'end')
        self.report.insert('end', '\n'.join('• ' + l for l in lines))
        self.report.configure(state='disabled')
