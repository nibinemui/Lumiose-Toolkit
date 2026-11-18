"""Extract files that the original export skipped, straight from the game's data.trpfs.

Usage:
    python restore_missing.py <romfs\\arc folder> <export folder> <oo2core dll> <hash list> [--dry-run]

Only files that do not exist yet in the export folder are written; nothing is overwritten.
Every written file is listed in restored_files.txt (next to this script) so it can be undone.
Files whose name is not in the hash list are written as <HASH>.bin in the export root,
the same way the original export did.
"""
import ctypes
import os
import re
import struct
import sys
import time


def fnv(s):
    h = 0xCBF29CE484222645
    for b in s.encode():
        h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def fields(d, t):
    vt = t - struct.unpack_from('<i', d, t)[0]
    vl = struct.unpack_from('<H', d, vt)[0]
    return [struct.unpack_from('<H', d, vt + 4 + 2 * i)[0] for i in range((vl - 4) // 2)]


def u32(d, o):
    return struct.unpack_from('<I', d, o)[0]


def vec(d, t, i):
    fo = fields(d, t)
    p = t + fo[i]
    v = p + u32(d, p)
    return v + 4, u32(d, v)


class Oodle:
    def __init__(self, dll):
        self.lib = ctypes.WinDLL(dll)
        f = self.lib.OodleLZ_Decompress
        f.restype = ctypes.c_ssize_t
        f.argtypes = [ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int,
                      ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p,
                      ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int]
        self.f = f

    def decompress(self, src, size):
        out = ctypes.create_string_buffer(size)
        r = self.f(src, len(src), out, size, 1, 0, 0, None, 0, None, None, None, 0, 3)
        if r != size:
            raise RuntimeError('Oodle returned %d bytes, expected %d' % (r, size))
        return out.raw


def main():
    args = [a for a in sys.argv[1:] if a != '--dry-run']
    if len(args) < 4:
        print('Extracts every file of Pokemon Legends: Z-A from the game archive (data.trpfs) into a folder.\n\n'
              'Usage:\n'
              '  restore_missing <romfs\\arc folder> <export folder> <oo2core_8_win64.dll> <hash list> [--dry-run]\n\n'
              '  romfs\\arc folder   the folder of your dump that contains data.trpfd and data.trpfs\n'
              '  export folder      where the files go (existing files are never overwritten)\n'
              '  oo2core_8_win64.dll the Oodle decompression DLL (not included; e.g. from pkNX)\n'
              '  hash list          names\\za_hashes_inside_fd.txt (Lumiose Toolkit downloads it on first start)\n'
              '  --dry-run          only count what would be written\n\n'
              'Example:\n'
              '  restore_missing "D:\\Dump\\romfs\\arc" "D:\\ZA EXPORT" "C:\\pkNX\\oo2core_8_win64.dll" '
              'names\\za_hashes_inside_fd.txt')
        return 1
    arc, export, dll, listing = args[:4]
    restore(arc, export, dll, listing, dry='--dry-run' in sys.argv)
    return 0


def restore(arc, export, dll, listing, dry=False, log=None, progress=None, cancel=None):
    """Write every game file from <arc>/data.trpfs that is missing in <export>.
    log(text), progress(done_packs, total_packs, files_written), cancel() -> True to stop.
    Returns (files written, bytes written, pack errors)."""
    if log is None:
        def log(text):
            print(text)
            sys.stdout.flush()
    t0 = time.time()
    # index: file hashes -> pack index, pack names
    fd = open(os.path.join(arc, 'data.trpfd'), 'rb').read()
    root = u32(fd, 0)
    s0, n0 = vec(fd, root, 0)
    file_hashes = [struct.unpack_from('<Q', fd, s0 + 8 * k)[0] for k in range(n0)]
    s1, n1 = vec(fd, root, 1)
    pack_names = []
    for k in range(n1):
        p = s1 + 4 * k
        q = p + u32(fd, p)
        pack_names.append(fd[q + 4:q + 4 + u32(fd, q)].decode())
    s2, n2 = vec(fd, root, 2)
    file_pack = []
    for k in range(n2):
        e = s2 + 4 * k
        t = e + u32(fd, e)
        tf = fields(fd, t)
        file_pack.append(struct.unpack_from('<Q', fd, t + tf[0])[0] if tf and tf[0] else 0)

    # pack directory inside data.trpfs
    trpfs = open(os.path.join(arc, 'data.trpfs'), 'rb')
    trpfs.seek(8)
    toff = struct.unpack('<Q', trpfs.read(8))[0]
    trpfs.seek(0, 2)
    size = trpfs.tell()
    trpfs.seek(toff)
    tab = trpfs.read(size - toff)
    r = u32(tab, 0)
    a, na = vec(tab, r, 0)
    b, nb = vec(tab, r, 1)
    pos = {struct.unpack_from('<Q', tab, a + 8 * k)[0]: struct.unpack_from('<Q', tab, b + 8 * k)[0] for k in range(na)}
    ends = sorted(pos.values())
    nxt = {o: (ends[k + 1] if k + 1 < len(ends) else toff) for k, o in enumerate(ends)}

    # names
    names = {}
    with open(listing, encoding='utf-8', errors='replace') as f:
        for line in f:
            m = re.match(r'0x([0-9A-Fa-f]{16})\s+(\S.*)', line.strip())
            if m:
                names[int(m.group(1), 16)] = m.group(2).strip()

    def target(h):
        n = names.get(h)
        return os.path.join(export, *n.split('/')) if n else os.path.join(export, '%016X.bin' % h)

    wanted = {}
    for h, pk in zip(file_hashes, file_pack):
        if not os.path.exists(target(h)):
            wanted.setdefault(pk, []).append(h)
    nfiles = sum(len(v) for v in wanted.values())
    unnamed = sum(1 for v in wanted.values() for h in v if h not in names)
    log('game files: %d in %d packs' % (n0, n1))
    log('missing from export: %d files in %d packs (%d without a known name)' % (nfiles, len(wanted), unnamed))
    if dry:
        return 0, 0, 0

    oodle = Oodle(dll)
    here = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    manifest = open(os.path.join(here, 'restored_files.txt'), 'a', encoding='utf-8')
    written = errors = 0
    total_bytes = 0
    for i, (pk, hs) in enumerate(sorted(wanted.items())):
        try:
            o = pos[fnv(pack_names[pk])]
            trpfs.seek(o)
            d = trpfs.read(nxt[o] - o)
            rr = u32(d, 0)
            hp, hn = vec(d, rr, 0)
            ph = [struct.unpack_from('<Q', d, hp + 8 * k)[0] for k in range(hn)]
            fp, fn = vec(d, rr, 1)
            need = set(hs)
            for k in range(fn):
                if ph[k] not in need:
                    continue
                e = fp + 4 * k
                t = e + u32(d, e)
                tf = fields(d, t) + [0] * 5
                comp = struct.unpack_from('<b', d, t + tf[1])[0] if tf[1] else -1
                dsize = struct.unpack_from('<Q', d, t + tf[3])[0] if tf[3] else 0
                dp = t + tf[4]
                dv = dp + u32(d, dp)
                raw = d[dv + 4:dv + 4 + u32(d, dv)]
                data = oodle.decompress(raw, dsize) if comp == 3 else raw
                out = target(ph[k])
                if os.path.exists(out):
                    continue
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out + '.part', 'wb') as fh:      # a cut-off write never looks like a finished file
                    fh.write(data)
                os.replace(out + '.part', out)
                manifest.write(os.path.relpath(out, export) + '\n')
                written += 1
                total_bytes += len(data)
        except Exception as e:  # noqa: BLE001
            errors += 1
            log('ERROR pack %s: %s' % (pack_names[pk], e))
        if progress:
            progress(i + 1, len(wanted), written)
        if i % 500 == 0:
            log('  %d / %d packs, %d files written' % (i, len(wanted), written))
            manifest.flush()
        if cancel and cancel():
            log('Stopped. Run it again to continue - files already written are kept.')
            break
    manifest.close()
    log('DONE: wrote %d files (%.1f MB), %d pack errors, %.0fs'
        % (written, total_bytes / 1048576, errors, time.time() - t0))
    trpfs.close()
    return written, total_bytes, errors


if __name__ == '__main__':
    sys.exit(main())
