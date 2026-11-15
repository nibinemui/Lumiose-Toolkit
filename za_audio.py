"""EXPERIMENTAL: replace sounds in Wwise sound banks (.bnk) and stream packages (.pck).

The replacement is stored as a PCM .wem (16-bit, the WAV's own rate/channels). Wwise can play PCM
on every platform, but the game's own sounds use Platinum ADPCM / Wwise Vorbis, so whether the game
accepts PCM in a given bank can only be confirmed in-game. In .bnk files the matching sound object
in HIRC gets its codec (PCM) and in-memory size updated.
"""
import os
import shutil
import struct

import numpy as np

PCM_PLUGIN = 0x00010001


class AudioError(Exception):
    pass


# ---------------------------------------------------------------------------
# WAV in, PCM WEM out
# ---------------------------------------------------------------------------

def read_wav(path):
    """-> (rate, int16 array of shape (frames, channels)). Accepts PCM 8/16/24/32-bit and float WAV."""
    with open(path, 'rb') as f:
        d = f.read()
    if d[:4] != b'RIFF' or d[8:12] != b'WAVE':
        raise AudioError('Not a WAV file')
    p, fmt, data = 12, None, None
    while p + 8 <= len(d):
        tag, n = d[p:p + 4], struct.unpack_from('<I', d, p + 4)[0]
        if tag == b'fmt ':
            fmt = struct.unpack_from('<HHIIHH', d, p + 8)
            if fmt[0] == 0xFFFE and n >= 40:                 # extensible: real format in the GUID
                fmt = (struct.unpack_from('<H', d, p + 8 + 24)[0],) + fmt[1:]
        elif tag == b'data':
            data = d[p + 8:p + 8 + n]
        p += 8 + n + (n & 1)
    if not fmt or data is None:
        raise AudioError('WAV has no fmt/data chunk')
    tagf, ch, rate, _, align, bits = fmt
    if tagf == 1 and bits == 16:
        a = np.frombuffer(data[:len(data) // 2 * 2], '<i2')
    elif tagf == 1 and bits == 8:
        a = ((np.frombuffer(data, np.uint8).astype(np.int16) - 128) << 8)
    elif tagf == 1 and bits == 24:
        b = np.frombuffer(data[:len(data) // 3 * 3], np.uint8).reshape(-1, 3).astype(np.int32)
        a = ((b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)) << 8 >> 16).astype(np.int16)
    elif tagf == 1 and bits == 32:
        a = (np.frombuffer(data[:len(data) // 4 * 4], '<i4') >> 16).astype(np.int16)
    elif tagf == 3:
        f = np.frombuffer(data[:len(data) // (bits // 8) * (bits // 8)], '<f4' if bits == 32 else '<f8')
        a = np.clip(f * 32767, -32768, 32767).astype(np.int16)
    else:
        raise AudioError('Unsupported WAV format (tag %d, %d-bit). Save it as 16-bit PCM WAV.' % (tagf, bits))
    return rate, a[:len(a) // ch * ch].reshape(-1, ch)


def make_pcm_wem(rate, samples):
    """RIFF/WAVE with a Wwise-style extensible PCM fmt chunk."""
    frames, ch = samples.shape
    mask = {1: 0x4, 2: 0x3, 4: 0x33, 6: 0x3F}.get(ch, 0)
    data = np.ascontiguousarray(samples, '<i2').tobytes()
    fmt = struct.pack('<HHIIHHHHI', 0xFFFE, ch, rate, rate * ch * 2, ch * 2, 16, 6, 16, mask)
    body = b'WAVE' + b'fmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(data)) + data
    return b'RIFF' + struct.pack('<I', len(body)) + body


# ---------------------------------------------------------------------------
# .bnk
# ---------------------------------------------------------------------------

def _sections(d):
    out, p = [], 0
    while p + 8 <= len(d):
        tag, n = d[p:p + 4], struct.unpack_from('<I', d, p + 4)[0]
        out.append([tag, bytearray(d[p + 8:p + 8 + n])])
        p += 8 + n
    return out


def bnk_media(d):
    """[(media id, offset in DATA, size)] in DIDX order (= vgmstream's sound order)."""
    for tag, body in _sections(d):
        if tag == b'DIDX':
            return [struct.unpack_from('<III', body, 12 * i) for i in range(len(body) // 12)]
    return []


def replace_in_bnk(bnk, index, wem):
    """Return (new bank bytes, note). index = 0-based position in DIDX (sound N-1 in the list)."""
    secs = _sections(bnk)
    tags = [s[0] for s in secs]
    if b'DIDX' not in tags or b'DATA' not in tags:
        raise AudioError('This bank has no embedded sounds (DIDX/DATA) - its sounds are streamed from a .pck.')
    didx = secs[tags.index(b'DIDX')][1]
    data = secs[tags.index(b'DATA')][1]
    ents = [list(struct.unpack_from('<III', didx, 12 * i)) for i in range(len(didx) // 12)]
    if not 0 <= index < len(ents):
        raise AudioError('Sound %d does not exist in this bank' % (index + 1))
    media_id = ents[index][0]
    orig_size = ents[index][2]
    blobs = [bytes(data[o:o + s]) for _, o, s in ents]
    blobs[index] = wem
    new_data, pos = bytearray(), 0
    for e, b in zip(ents, blobs):
        while len(new_data) % 16:
            new_data += b'\0'
        e[1], e[2] = len(new_data), len(b)
        new_data += b
    secs[tags.index(b'DATA')][1] = new_data
    secs[tags.index(b'DIDX')][1] = bytearray(b''.join(struct.pack('<III', *e) for e in ents))
    note = 'media %d replaced (%d bytes PCM)' % (media_id, len(wem))
    if b'HIRC' in tags:
        h = secs[tags.index(b'HIRC')][1]
        patched = 0
        key = struct.pack('<I', media_id)
        i = h.find(key)
        while i != -1:
            # AkBankSourceData: [plugin u32][stream type u8][source id u32][in-memory size u32]
            if i >= 5 and i + 8 <= len(h) and struct.unpack_from('<I', h, i + 4)[0] == orig_size:
                struct.pack_into('<I', h, i - 5, PCM_PLUGIN)
                struct.pack_into('<I', h, i + 4, len(wem))
                patched += 1
            i = h.find(key, i + 4)
        note += '; %d sound object(s) switched to PCM' % patched
    out = bytearray()
    for tag, body in secs:
        out += tag + struct.pack('<I', len(body)) + body
    return bytes(out), note


# ---------------------------------------------------------------------------
# .pck (AKPK)
# ---------------------------------------------------------------------------

def pck_entries(path):
    """[(header position, id, block, size, offset_in_blocks, kind)] and header bytes."""
    with open(path, 'rb') as f:
        head = f.read(28)
        if head[:4] != b'AKPK':
            raise AudioError('Not a Wwise package')
        hsize = struct.unpack_from('<I', head, 4)[0]
        f.seek(0)
        hdr = bytearray(f.read(8 + hsize))
    ver, lang, banks, streams = struct.unpack_from('<IIII', hdr, 8)
    rest = hsize - 20 - lang - banks - streams
    pos = 8 + 20 + lang
    out = []

    def table(p, size, kind, id64):
        if size < 4:
            return
        n = struct.unpack_from('<I', hdr, p)[0]
        ent = (size - 4) // n if n else 0
        for i in range(n):
            q = p + 4 + i * ent
            # entry: [id][block size][size][offset in blocks][language]; store where size starts
            if id64 and ent >= 24:
                fid, block, sz, off, _ = struct.unpack_from('<QIIII', hdr, q)
                out.append((q + 12, fid, block, sz, off, kind))
            else:
                fid, block, sz, off, _ = struct.unpack_from('<IIIII', hdr, q)
                out.append((q + 8, fid, block, sz, off, kind))
    table(pos, banks, 'bnk', False)
    table(pos + banks, streams, 'wem', False)
    table(pos + banks + streams, rest, 'wem', True)
    return out, hdr


def replace_in_pck(src_path, index, wem, out_path, progress=None):
    """Write a copy of the package with entry `index` (order of za_modding.audio_streams) replaced."""
    ents, hdr = pck_entries(src_path)
    if not 0 <= index < len(ents):
        raise AudioError('Entry %d does not exist' % (index + 1))
    order = sorted(range(len(ents)), key=lambda k: ents[k][4] * max(ents[k][2], 1))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + '.part'
    with open(src_path, 'rb') as src, open(tmp, 'wb') as out:
        out.write(hdr)
        for n, k in enumerate(order):
            hp, fid, block, size, off, kind = ents[k]
            block = max(block, 1)
            while out.tell() % block:
                out.write(b'\0')
            new_off = out.tell() // block
            if k == index:
                out.write(wem)
                new_size = len(wem)
            else:
                src.seek(off * block)
                remaining = size
                while remaining:
                    chunk = src.read(min(remaining, 1 << 22))
                    if not chunk:
                        break
                    out.write(chunk)
                    remaining -= len(chunk)
                new_size = size
            struct.pack_into('<II', hdr, hp, new_size, new_off)
            if progress and n % 200 == 0:
                progress(n, len(order))
        out.seek(0)
        out.write(hdr)
    shutil.move(tmp, out_path)
    return 'entry %d (id %d) replaced with %d bytes PCM' % (index + 1, ents[index][1], len(wem))
