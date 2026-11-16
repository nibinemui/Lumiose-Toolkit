"""Smoke test for a built release folder: does the program start, open its window at the right size and
keep running without errors? Used by the "Test build" GitHub workflow (.github/workflows/test-build.yml)
and before each release.

    python tools/smoke_test.py "dist/Lumiose Toolkit"

Run it on a fresh copy of the release folder (no settings.json): it starts the program like a first-time
user would. No game files are needed. Exits with 0 when everything passed, 1 otherwise.
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import za_version  # noqa: E402

START_TIMEOUT = 90       # seconds for the main window to appear (first start unpacks Python, cold disk)
STAY_ALIVE = 15          # seconds it must keep running after the window appeared
MIN_SIZE = (900, 520)    # App.minsize: a window stuck at exactly this size is the 1.3.0 start-up bug

u32 = ctypes.windll.user32
try:                     # measure in real pixels, like the program does (Windows display scaling)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):
    pass
EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def windows_of(pid):
    """Visible top-level windows of a process: [(title, (width, height))]."""
    found = []

    def cb(hwnd, _):
        p = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(hwnd, buf, 256)
            r = wt.RECT()
            u32.GetWindowRect(hwnd, ctypes.byref(r))
            found.append((buf.value, (r.right - r.left, r.bottom - r.top)))
        return True
    u32.EnumWindows(EnumProc(cb), 0)
    return found


def main(folder):
    folder = os.path.abspath(folder)
    exe = os.path.join(folder, 'Lumiose Toolkit.exe')
    log = os.path.join(folder, 'error_log.txt')
    problems = []

    def check(ok, what):
        print(('  ok    ' if ok else '  FAIL  ') + what, flush=True)
        if not ok:
            problems.append(what)
        return ok

    print('Smoke test of %s' % folder, flush=True)
    if not check(os.path.isfile(exe), 'Lumiose Toolkit.exe exists'):
        return 1
    for rel in ('restore_missing.exe', 'tools/texconv/texconv.exe', 'tools/flatc/flatc.exe',
                'tools/vgmstream/vgmstream-cli.exe', 'tools/viewer/viewer.html', 'LICENSE', 'source/za_extractor.py',
                'licenses/bundled-python/README.txt'):
        check(os.path.isfile(os.path.join(folder, rel)), '%s is included' % rel)
    if os.path.isfile(log):
        os.remove(log)            # only errors from this run count

    # restore_missing.exe (command line): without arguments it prints its usage
    try:
        r = subprocess.run([os.path.join(folder, 'restore_missing.exe')], capture_output=True, text=True, timeout=60)
        check('Usage:' in r.stdout, 'restore_missing.exe runs and prints its usage')
    except (OSError, subprocess.TimeoutExpired) as e:
        check(False, 'restore_missing.exe runs (%s)' % e)

    # the program: main window appears, at its normal size, and it keeps running
    title = 'Lumiose Toolkit %s' % za_version.VERSION
    sw, sh = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
    expected = (min(1400, sw - 40), min(860, sh - 110))         # as App.__init__ sizes it (logical pixels)
    p = subprocess.Popen([exe], cwd=folder)
    try:
        main_win, t0 = None, time.time()
        while time.time() - t0 < START_TIMEOUT and p.poll() is None:
            main_win = next((w for w in windows_of(p.pid) if w[0] == title), None)
            if main_win:
                break
            time.sleep(0.5)
        if not check(main_win is not None, 'main window "%s" appears (%.0f s)' % (title, time.time() - t0)):
            check(p.poll() is None, 'program still running (exit code %s)' % p.poll())
        else:
            time.sleep(3)             # the dark title bar fix and first layout run just after the window appears
            size = next((w[1] for w in windows_of(p.pid) if w[0] == title), main_win[1])
            print('        screen %dx%d, window %dx%d (expected about %dx%d)' % (sw, sh, size[0], size[1], *expected))
            # GetWindowRect includes the border and may be in scaled pixels: allow some room, but never the minimum
            check(size[0] >= expected[0] * 0.9 and size[1] >= expected[1] * 0.85 and
                  (size[0], size[1]) != MIN_SIZE, 'window opens at its normal size, not shrunk')
            time.sleep(STAY_ALIVE)
            check(p.poll() is None, 'still running after %d s' % STAY_ALIVE)
            others = [w[0] for w in windows_of(p.pid) if w[0] != title]
            if others:
                print('        other windows: %s' % ', '.join(repr(o) for o in others))
    finally:
        if p.poll() is None:
            subprocess.call(['taskkill', '/F', '/T', '/PID', str(p.pid)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            p.wait(30)
    errors = ''
    if os.path.isfile(log):
        with open(log, encoding='utf-8', errors='replace') as f:
            errors = f.read().strip()
    if not check(not errors, 'no errors in error_log.txt'):
        print(errors[-4000:])
    print('\n%s' % ('PASSED' if not problems else 'FAILED: ' + '; '.join(problems)), flush=True)
    return 0 if not problems else 1


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
