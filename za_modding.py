"""Modding back end for Lumiose Toolkit: data <-> JSON, text tables, audio, texture import, mods.

Helper programs (in tools/): flatc (Google, Apache-2.0), texconv (Microsoft DirectXTex, MIT),
vgmstream-cli (vgmstream, ISC-style). Trinity knowledge from pkZukan/gftool (GPL-2.0).
"""
import json
import os
import sys
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile

import numpy as np
from PIL import Image

import za_formats as zf

APP_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))   # the .exe's folder when frozen
TOOLS = os.path.join(APP_DIR, 'tools')
FLATC = os.path.join(TOOLS, 'flatc', 'flatc.exe')
TEXCONV = os.path.join(TOOLS, 'texconv', 'texconv.exe')
VGMSTREAM = os.path.join(TOOLS, 'vgmstream', 'vgmstream-cli.exe')
SCHEMAS = os.path.join(TOOLS, 'schemas')
MODS_DIR = os.path.join(APP_DIR, 'mods')
TITLE_ID = '0100F43008C44000'
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


class ModError(Exception):
    pass


def _run(args, timeout=600):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
    if r.returncode != 0:
        msg = '\n'.join(l for l in (r.stderr + '\n' + r.stdout).splitlines() if 'warning' not in l.lower())
        raise ModError(msg.strip()[:2000] or 'tool failed (%d)' % r.returncode)
    return r


def fnv1a64(s):
    h = 0xCBF29CE484222645
    for b in s.encode('utf-8'):
        h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


# ---------------------------------------------------------------------------
# FlatBuffer data <-> JSON (flatc)
# ---------------------------------------------------------------------------

TRINITY_EXTS = ('trmdl', 'trmsh', 'trmbf', 'trmtr', 'trskl', 'tranm', 'tracm', 'trscn', 'trins')
NAMED_SCHEMAS = {'dress_up_data.bin': 'dress_up_data.fbs', 'poke_resource_table.trpmcatalog': 'trpmcatalog.fbs',
                 'personal_array.bin': 'za_personal.fbs'}
# schemas reverse-engineered for this tool (tools/schemas/za); every one rebuilds the game file byte-for-byte
ZA_SCHEMAS = {'waza_array.bin': 'za_waza.fbs', 'item_data.bin': 'za_item.fbs',
              'shop_item_lineup.bin': 'za_shop_item_lineup.fbs', 'shop_dress_up_lineup.bin': 'za_shop_dressup_lineup.fbs',
              'shop_hair_make_lineup.bin': 'za_shop_dressup_lineup.fbs',
              'dress_up_group_data.bin': 'za_dress_up_group_data.fbs', 'hair_make_data.bin': 'za_hair_make_data.fbs',
              'pokedex_contents_data.bin': 'za_pokedex_contents.fbs', 'wild_single.data.bin': 'za_wild_single_catalog.fbs'}
# player customisation files, by extension (faces use another layout and are left out)
ZA_EXT_SCHEMAS = {'ccdata': 'za_ccdata.fbs', 'ccdatam': 'za_ccdatam.fbs'}


def schema_for(path):
    """Schema file (.bfbs or .fbs) that describes `path`, or None."""
    stem, ext = os.path.splitext(path)
    if os.path.isfile(stem + '.bfbs'):
        return stem + '.bfbs'
    e = ext.lower().lstrip('.')
    if e in TRINITY_EXTS:
        p = os.path.join(SCHEMAS, 'trinity', e + '.fbs')
        if os.path.isfile(p):
            return p
    n = NAMED_SCHEMAS.get(os.path.basename(path).lower())
    if n:
        p = os.path.join(SCHEMAS, 'pokedocs', n)
        if not os.path.isfile(p):
            try:          # downloaded from PokeDocs on demand (not shipped)
                import za_deps
                za_deps.ensure(log=lambda m: None)
            except Exception:  # noqa: BLE001
                pass
        if os.path.isfile(p):
            return p
    n = ZA_SCHEMAS.get(os.path.basename(path).lower())
    if n is None and not os.path.basename(path).lower().startswith('p0_face'):
        n = ZA_EXT_SCHEMAS.get(e)
    if n and os.path.isfile(os.path.join(SCHEMAS, 'za', n)):
        return os.path.join(SCHEMAS, 'za', n)
    return None


def to_json(path, schema=None, defaults=False):
    """Decode a FlatBuffer file to JSON text using its schema. defaults=True also lists scalar fields
    that are not stored, with the schema's default value."""
    schema = schema or schema_for(path)
    if not schema:
        raise ModError('No schema known for ' + os.path.basename(path))
    with tempfile.TemporaryDirectory() as td:
        # flatc can't open very long paths, so work on short copies
        src = os.path.join(td, 'in' + os.path.splitext(path)[1])
        shutil.copyfile(path, src)
        sch = os.path.join(td, 'schema' + os.path.splitext(schema)[1])
        shutil.copyfile(schema, sch)
        _run([FLATC, '--json', '--strict-json', '--raw-binary', '--natural-utf8']
             + (['--defaults-json'] if defaults else []) + ['-o', td, sch, '--', src])
        with open(os.path.join(td, 'in.json'), encoding='utf-8') as f:
            return f.read()


def from_json(json_text, schema, out_path):
    """Build a FlatBuffer binary from JSON text. The output keeps the original extension."""
    with tempfile.TemporaryDirectory() as td:
        jp = os.path.join(td, 'data.json')
        with open(jp, 'w', encoding='utf-8') as f:
            f.write(json_text)
        sch = os.path.join(td, 'schema' + os.path.splitext(schema)[1])
        shutil.copyfile(schema, sch)
        # --force-defaults: write every field the JSON has, even when it equals the schema's default.
        # The game's real defaults differ for some fields (e.g. vertexDeclarationIndex and
        # vertexElementSizeIndex in .trmsh default to -1), so a stored 0 must stay stored or the game
        # reads -1 and crashes. Fields missing from the JSON stay missing, so it round-trips exactly.
        _run([FLATC, '-b', '--force-defaults', '-o', td, sch, jp])
        built = [p for p in os.listdir(td) if p not in ('data.json', os.path.basename(sch))]
        if not built:
            raise ModError('flatc produced no output')
        os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
        shutil.copyfile(os.path.join(td, built[0]), out_path)
    return out_path


# ---------------------------------------------------------------------------
# text tables (.dat): text -> binary
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r'\\n|\\r|\\c|\[VAR ([0-9A-Fa-f]{4})(?:\(([0-9A-Fa-f,]*)\))?\]|\[([0-9A-Fa-f]{4})\]')


def _units(text):
    """UTF-16 code units: a character beyond U+FFFF (an emoji) becomes a surrogate pair."""
    out = []
    for c in text:
        o = ord(c)
        if o > 0xFFFF:
            o -= 0x10000
            out += [0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF)]
        else:
            out.append(o)
    return out


def _encode_text(s):
    chars, pos = [], 0
    for m in _TOKEN.finditer(s):
        chars.extend(_units(s[pos:m.start()]))
        t = m.group(0)
        if t == '\\n':
            chars.append(0x0A)
        elif t == '\\r':
            chars += [0x10, 1, 0xBE00]
        elif t == '\\c':
            chars += [0x10, 1, 0xBE01]
        elif m.group(1):
            args = [int(a, 16) for a in m.group(2).split(',')] if m.group(2) else []
            chars += [0x10, 1 + len(args), int(m.group(1), 16)] + args
        else:
            chars.append(int(m.group(3), 16))
        pos = m.end()
    chars.extend(_units(s[pos:]))
    chars.append(0)
    return chars


def encode_msg(texts, template):
    """Rebuild a message .dat from a list of line texts, copying layout details from `template`
    (the original .dat bytes). The number of lines must stay the same."""
    ns, nl, total, ik, so = struct.unpack_from('<HHIII', template, 0)
    if len(texts) != nl:
        raise ModError('Line count changed: the file has %d lines, the text has %d. '
                       'Keep every line (edit only the text after the TAB).' % (nl, len(texts)))
    entries = [struct.unpack_from('<IHH', template, so + 4 + i * 8) for i in range(nl)]
    original = zf.decode_msg(template)
    table, data = bytearray(), bytearray()
    base = 4 + 8 * nl
    key = 0x7C89
    for i, t in enumerate(texts):
        if t == original[i]:
            # unchanged line: copy the original encrypted bytes (keeps padding/extra data exactly)
            off, ln, flag = entries[i]
            enc = template[so + off:so + off + ln * 2]
            table += struct.pack('<IHH', base + len(data), ln, flag)
            data += enc
            while len(data) % 4:
                data += b'\0'
            key = (key + 0x2983) & 0xFFFF
            continue
        chars = _encode_text(t)
        k = key
        enc = bytearray()
        for c in chars:
            enc += struct.pack('<H', (c ^ k) & 0xFFFF)
            k = ((k << 3) | (k >> 13)) & 0xFFFF
        table += struct.pack('<IHH', base + len(data), len(chars), entries[i][2])
        data += enc
        while len(data) % 4:
            data += b'\0'
        key = (key + 0x2983) & 0xFFFF
    section = struct.pack('<I', base + len(data)) + table + data
    head = struct.pack('<HHIII', 1, nl, len(section) - 0, 0, 0x10)
    return head + section


def txt_to_dat(txt, template):
    """Parse 'label<TAB>text' lines (as exported by Lumiose Toolkit) and rebuild the .dat."""
    lines = [l for l in txt.replace('﻿', '').split('\n')]
    while lines and lines[-1] == '':
        lines.pop()
    # split on the LAST tab: labels can contain tabs, decoded text never does (it shows [0009])
    texts = [l.rsplit('\t', 1)[1] if '\t' in l else l for l in lines]
    return encode_msg(texts, template)


# ---------------------------------------------------------------------------
# audio (Wwise .bnk / .pck / .wem)
# ---------------------------------------------------------------------------

def parse_akpk(path):
    """Wwise AKPK package -> list of (id, offset, size, kind). kind: 'bnk' or 'wem'."""
    with open(path, 'rb') as f:
        head = f.read(32)
        if head[:4] != b'AKPK':
            raise ModError('Not a Wwise package')
        hsize, ver, lang, banks, streams = struct.unpack_from('<IIIII', head, 4)
        f.seek(8)
        hdr = f.read(hsize)
    rest = hsize - 20 - lang - banks - streams
    pos = 20 + lang
    out = []

    def table(p, size, kind, id64):
        n = struct.unpack_from('<I', hdr, p)[0] if size >= 4 else 0
        ent = (size - 4) // n if n else 0
        for i in range(n):
            q = p + 4 + i * ent
            if id64 and ent >= 24:
                fid, block, sz, off, _ = struct.unpack_from('<QIIII', hdr, q)
            else:
                fid, block, sz, off, _ = struct.unpack_from('<IIIII', hdr, q)
            out.append((fid, off * (block or 1), sz, kind))
    table(pos, banks, 'bnk', False)
    table(pos + banks, streams, 'wem', False)
    if rest >= 4:
        table(pos + banks + streams, rest, 'wem', True)
    return out


def audio_streams(path):
    """List playable sounds: [(label, source)] where source is (path, subsong) or (path, offset, size)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pck':
        out = []
        for fid, off, size, kind in parse_akpk(path):
            out.append(('%s %d  (%d KB)' % (kind, fid, size // 1024), (path, off, size, kind)))
        return out
    r = subprocess.run([VGMSTREAM, '-m', path], capture_output=True, text=True, creationflags=NO_WINDOW)
    m = re.search(r'stream count:\s*(\d+)', r.stdout)
    n = int(m.group(1)) if m else (1 if 'sample rate' in r.stdout else 0)
    return [('sound %d' % (i + 1), (path, i + 1)) for i in range(n)]


def decode_audio(source, out_wav):
    """Decode one sound (from audio_streams) to a .wav file."""
    if len(source) == 4:                                    # entry inside a .pck
        path, off, size, kind = source
        with open(path, 'rb') as f:
            f.seek(off)
            blob = f.read(size)
        with tempfile.TemporaryDirectory() as td:
            tmp = os.path.join(td, 'sound.' + kind)
            with open(tmp, 'wb') as f:
                f.write(blob)
            _run([VGMSTREAM, '-o', out_wav, tmp])
    else:
        path, sub = source
        _run([VGMSTREAM, '-s', str(sub), '-o', out_wav, path])
    return out_wav


# ---------------------------------------------------------------------------
# texture import (PNG -> existing BNTX, same size/format)
# ---------------------------------------------------------------------------

TEXCONV_FMT = {(0x1A, 1): 'BC1_UNORM', (0x1A, 6): 'BC1_UNORM_SRGB', (0x1C, 1): 'BC3_UNORM', (0x1C, 6): 'BC3_UNORM_SRGB',
               (0x1D, 1): 'BC4_UNORM', (0x1E, 1): 'BC5_UNORM', (0x20, 1): 'BC7_UNORM', (0x20, 6): 'BC7_UNORM_SRGB',
               (0x0B, 1): 'R8G8B8A8_UNORM', (0x0B, 6): 'R8G8B8A8_UNORM_SRGB'}


def swizzle_block_linear(lin, width_units, height_units, bpp, block_height, out_size):
    """Inverse of za_formats.deswizzle_block_linear."""
    row_bytes = width_units * bpp
    gobs_x = (row_bytes + 63) // 64
    padded_w = gobs_x * 64
    gob_rows = 8 * block_height
    padded_h = -(-height_units // gob_rows) * gob_rows
    total = gobs_x * 512 * block_height * (padded_h // gob_rows)
    src = np.zeros((height_units, padded_w), np.uint8)
    src[:, :row_bytes] = np.frombuffer(lin, np.uint8, count=row_bytes * height_units).reshape(height_units, row_bytes)
    xb = np.arange(padded_w // 16, dtype=np.int64) * 16
    xaddr = (xb // 64) * 512 * block_height + ((xb % 64) // 32) * 256 + ((xb % 32) // 16) * 32
    y = np.arange(height_units, dtype=np.int64)
    yaddr = ((y // gob_rows) * 512 * block_height * gobs_x
             + ((y % gob_rows) // 8) * 512 + ((y % 8) // 2) * 64 + (y % 2) * 16)
    idx = (yaddr[:, None] + xaddr[None, :]) // 16
    out = np.zeros((total // 16, 16), np.uint8)
    out[idx.reshape(-1)] = src.reshape(-1, 16)
    b = out.tobytes()
    return (b + bytes(max(0, out_size - len(b))))[:out_size]


def _dds_mips(dds, w, h, bw, bh, bpp, mips):
    if dds[:4] != b'DDS ':
        raise ModError('texconv did not produce a DDS file')
    off = 128 + (20 if dds[84:88] == b'DX10' else 0)
    out = []
    for lv in range(mips):
        mw, mh = max(1, w >> lv), max(1, h >> lv)
        n = -(-mw // bw) * -(-mh // bh) * bpp
        out.append(dds[off:off + n])
        off += n
    return out


def _brti_offset(bntx_bytes, texture_index):
    info = struct.unpack_from('<Q', bntx_bytes, 0x28)[0]
    return struct.unpack_from('<Q', bntx_bytes, info + 8 * texture_index)[0]


def _block_height_log2(height_units):
    bh = 1
    while bh < 16 and bh * 8 < height_units:
        bh *= 2
    return bh.bit_length() - 1


def _mip_geometry(w0, h0, bw, bh_unit, bhl2, level):
    """Same rules as za_formats.BntxTexture._mip_geom, for an arbitrary size."""
    w, h = max(1, w0 >> level), max(1, h0 >> level)
    wu, hu = -(-w // bw), -(-h // bh_unit)
    lines = (1 << bhl2) * 8
    shift = 0
    for lv in range(1, level + 1):
        hl = -(-max(1, h0 >> lv) // bh_unit)
        if (1 << (max(1, hl) - 1).bit_length()) < lines:
            shift += 1
    return wu, hu, 1 << max(0, bhl2 - shift)


def resize_bntx(bntx_bytes, new_w, new_h, mips_linear_fn):
    """Rebuild a single-texture, single-layer BNTX at a new size.
    mips_linear_fn(mip_count) -> list of linear (deswizzled) mip data for the new size."""
    b = zf.Bntx(bntx_bytes)
    if len(b.textures) != 1 or b.textures[0].array_len != 1:
        raise ModError('Changing the size only works for files with one texture and one layer '
                       '(this one has %d texture(s)). Import at the original size instead.' % len(b.textures))
    t = b.textures[0]
    _, bw, bh_unit, bpp, _ = zf.FORMAT_TYPES[t.ftype]
    mips = max(1, min(t.mip_count, max(new_w, new_h).bit_length()))
    bhl2 = _block_height_log2(-(-new_h // bh_unit))
    linear = mips_linear_fn(mips)
    d = bytearray(bntx_bytes)
    brti = _brti_offset(d, 0)
    brtd = struct.unpack_from('<Q', d, 0x30)[0]
    rlt = struct.unpack_from('<I', d, 0x18)[0]
    align = max(struct.unpack_from('<I', d, brti + 0x54)[0], 0x200)
    data_start = brtd + 0x10
    blob, offsets = bytearray(), []
    for lv in range(mips):
        while len(blob) % align:
            blob += b'\0'
        wu, hu, bh = _mip_geometry(new_w, new_h, bw, bh_unit, bhl2, lv)
        gobs_x = -(-(wu * bpp) // 64)
        size = gobs_x * 512 * bh * (-(-hu // (8 * bh)))
        offsets.append(data_start + len(blob))
        blob += swizzle_block_linear(linear[lv], wu, hu, bpp, bh, size)
    image_size = len(blob)
    while len(blob) % 0x1000:
        blob += b'\0'
    # BRTI fields
    struct.pack_into('<H', d, brti + 0x16, mips)
    struct.pack_into('<ii', d, brti + 0x24, new_w, new_h)
    layout = struct.unpack_from('<I', d, brti + 0x34)[0]
    struct.pack_into('<I', d, brti + 0x34, (layout & ~7) | bhl2)
    struct.pack_into('<I', d, brti + 0x50, image_size)
    ptrs = struct.unpack_from('<Q', d, brti + 0x70)[0]
    for lv in range(t.mip_count):
        struct.pack_into('<Q', d, ptrs + 8 * lv, offsets[lv] if lv < mips else 0)
    head = d[:brtd]
    brtd_hdr = d[brtd:brtd + 8] + struct.pack('<Q', 0x10 + len(blob))
    new_rlt = brtd + 0x10 + len(blob)
    rl = bytearray(d[rlt:])
    struct.pack_into('<I', rl, 4, new_rlt)
    nsec = struct.unpack_from('<I', rl, 8)[0]
    for i in range(nsec):
        so = 0x10 + i * 0x18
        sec_off = struct.unpack_from('<I', rl, so + 8)[0]
        if sec_off == brtd:
            struct.pack_into('<I', rl, so + 12, 0x10 + len(blob))
    out = bytearray(head + brtd_hdr + blob + rl)
    struct.pack_into('<I', out, 0x18, new_rlt)
    struct.pack_into('<I', out, 0x1C, len(out))
    return bytes(out), mips


def rebuild_sarc(sarc_bytes, replacements):
    """SARC archive with some files replaced (by name). Keeps names, order and alignments."""
    d = sarc_bytes
    if d[:4] != b'SARC':
        raise ModError('Not a SARC archive')
    hdr = struct.unpack_from('<H', d, 4)[0]
    data_off = struct.unpack_from('<I', d, 0xC)[0]
    count = struct.unpack_from('<H', d, hdr + 6)[0]
    nodes_at = hdr + 0xC
    files = zf.parse_sarc(d)
    out_data = bytearray()
    nodes = bytearray(d[nodes_at:nodes_at + 16 * count])
    for i in range(count):
        h, attr, start, end = struct.unpack_from('<IIII', d, nodes_at + 16 * i)
        name, orig = files[i]
        blob = replacements.get(name, orig)
        a = 1
        while a < 0x2000 and start % (a * 2) == 0:
            a *= 2
        a = max(a, 4)
        while len(out_data) % a:
            out_data += b'\0'
        ns = len(out_data)
        out_data += blob
        struct.pack_into('<II', nodes, 16 * i + 8, ns, ns + len(blob))
    out = bytearray(d[:data_off])
    out[nodes_at:nodes_at + 16 * count] = nodes
    out += out_data
    struct.pack_into('<I', out, 8, len(out))
    check = dict(zf.parse_sarc(bytes(out)))
    for k, v in replacements.items():
        if check.get(k) != v:
            raise ModError('SARC rebuild check failed for ' + k)
    return bytes(out)


def _unmap_channels(img, t):
    """PNG pixels -> the channels the texture stores (undoes the BNTX channel map)."""
    png = np.asarray(img).copy()
    src = np.zeros_like(png)
    src[..., 3] = 255
    filled = set()
    for i, s in enumerate(t.comp_sel):     # output channel i shows source channel sel[i]-2
        if 2 <= s <= 5 and s not in filled:
            src[..., s - 2] = png[..., i]
            filled.add(s)
    if t.ftype == 0x1E:                    # normal maps: only red/green are stored
        src[..., 2] = 0
    return src


def _encode_mips(src, t, fmt, mip_count):
    """Compress an RGBA array with texconv; returns linear data for each mip level."""
    _, bw, bh, bpp, _ = zf.FORMAT_TYPES[t.ftype]
    h, w = src.shape[:2]
    with tempfile.TemporaryDirectory() as td:
        sp = os.path.join(td, 'tex.png')
        Image.fromarray(src).save(sp)
        args = [TEXCONV, '-nologo', '-y', '-o', td, '-f', fmt, '-m', str(mip_count), '--ignore-srgb', '-sepalpha']
        if fmt.endswith('_SRGB'):
            args += ['-srgb']
        _run(args + [sp])
        with open(os.path.join(td, 'tex.dds'), 'rb') as f:
            dds = f.read()
    return _dds_mips(dds, w, h, bw, bh, bpp, mip_count)


def _import_resized(img, bntx_bytes, t, fmt, note, log, raw_channels=False):
    w, h = img.size
    src = np.asarray(img).copy() if raw_channels else _unmap_channels(img, t)
    out, mips = resize_bntx(bntx_bytes, w, h, lambda n: _encode_mips(src, t, fmt, n))
    nt = zf.Bntx(out).textures[0]
    check = np.asarray(nt.decode(0, 0, apply_comp_sel=False)).astype(np.int32)
    stored = {0x1D: [0], 0x1E: [0, 1]}.get(t.ftype, [0, 1, 2])
    err = float(np.abs(check[..., stored] - src[..., stored].astype(np.int32)).mean())
    note.append('texture size changed from %dx%d to %dx%d (%d mips)' % (t.width, t.height, w, h, mips))
    note.append('average colour error after compression: %.1f / 255' % err)
    if log:
        log('; '.join(note))
    return out, note


def import_png(png_path, bntx_bytes, texture_index=0, layer=0, log=None, keep_size=True, raw_channels=False):
    """Return new BNTX bytes with `png_path` encoded into texture `texture_index` / `layer`.

    If keep_size is False and the PNG size differs, the texture is rebuilt at the PNG's size
    (single-texture files only); otherwise the PNG is resized to the original size.
    Channel remaps (UI textures) are undone."""
    b = zf.Bntx(bntx_bytes)
    t = b.textures[texture_index]
    fmt = TEXCONV_FMT.get((t.ftype, t.variant))
    if not fmt or not t.supported:
        raise ModError('Importing into %s textures is not supported.' % t.format_name)
    img = Image.open(png_path).convert('RGBA')
    note = []
    if img.size != (t.width, t.height):
        if not keep_size:
            return _import_resized(img, bntx_bytes, t, fmt, note, log, raw_channels)
        note.append('resized from %dx%d to %dx%d' % (img.size + (t.width, t.height)))
        from za_trinity import resize_channels
        img = resize_channels(img, (t.width, t.height))
    src = np.asarray(img).copy() if raw_channels else _unmap_channels(img, t)
    mips = _encode_mips(src, t, fmt, t.mip_count)
    out = bytearray(bntx_bytes)
    for lv, lin in enumerate(mips):
        w, h, wu, hu, bpp_, bhgt = t._mip_geom(lv)
        start = t.mip_offsets[lv] + layer * t.layer_stride
        end = (t.mip_offsets[lv + 1] if lv + 1 < t.mip_count else t.mip_offsets[0] + t.layer_stride) + layer * t.layer_stride
        out[start:end] = swizzle_block_linear(lin, wu, hu, bpp_, bhgt, end - start)
    # check the result decodes to what was imported
    nt = zf.Bntx(bytes(out)).textures[texture_index]
    check = np.asarray(nt.decode(layer, 0, apply_comp_sel=False)).astype(np.int32)
    stored = {0x1D: [0], 0x1E: [0, 1]}.get(t.ftype, [0, 1, 2])     # channels the format really keeps
    err = float(np.abs(check[..., stored] - src[..., stored].astype(np.int32)).mean())
    note.append('average colour error after compression: %.1f / 255' % err)
    if log:
        log('; '.join(note))
    return bytes(out), note


# ---------------------------------------------------------------------------
# DDS import
# ---------------------------------------------------------------------------

DDS_FOURCC = {b'DXT1': 71, b'DXT3': 74, b'DXT5': 77, b'ATI1': 80, b'BC4U': 80, b'ATI2': 83, b'BC5U': 83}
DXGI_NAMES = {71: 'BC1', 72: 'BC1 sRGB', 74: 'BC2', 75: 'BC2 sRGB', 77: 'BC3', 78: 'BC3 sRGB', 80: 'BC4', 81: 'BC4 signed',
              83: 'BC5', 84: 'BC5 signed', 95: 'BC6H', 96: 'BC6H signed', 98: 'BC7', 99: 'BC7 sRGB', 28: 'RGBA8',
              29: 'RGBA8 sRGB', 87: 'BGRA8', 91: 'BGRA8 sRGB', 61: 'R8', 49: 'RG8'}
_SRGB_BASE = {72: 71, 75: 74, 78: 77, 99: 98, 29: 28, 91: 87}     # same block data, only read differently


def read_dds(data):
    """DDS header: {'width', 'height', 'mips', 'dxgi' (None if unknown), 'layers', 'offset'}."""
    if data[:4] != b'DDS ' or len(data) < 128:
        raise ModError('Not a DDS file')
    h, w = struct.unpack_from('<II', data, 12)
    mips = max(1, struct.unpack_from('<I', data, 28)[0])
    pf_flags, fourcc, bits, rmask = struct.unpack_from('<I4sII', data, 80)
    caps2 = struct.unpack_from('<I', data, 112)[0]
    off, layers, dxgi = 128, 6 if caps2 & 0x200 else 1, None
    if fourcc == b'DX10':
        dxgi, _dim, misc, arr = struct.unpack_from('<IIII', data, 128)
        off = 148
        layers = max(1, arr) * (6 if misc & 0x4 else 1)
    elif pf_flags & 0x4 and fourcc in DDS_FOURCC:
        dxgi = DDS_FOURCC[fourcc]
    elif pf_flags & 0x40 and bits == 32:
        dxgi = 28 if rmask == 0x000000FF else 87
    return {'width': w, 'height': h, 'mips': mips, 'dxgi': dxgi, 'layers': layers, 'offset': off}


def _dds_layer_mips(data, info, layer, count, bw, bh, bpp):
    """Linear data of the first `count` mips of one layer of a DDS."""
    def level_size(lv):
        mw, mh = max(1, info['width'] >> lv), max(1, info['height'] >> lv)
        return -(-mw // bw) * -(-mh // bh) * bpp
    per_layer = sum(level_size(lv) for lv in range(info['mips']))
    off = info['offset'] + per_layer * layer
    out = []
    for lv in range(count):
        n = level_size(lv)
        if off + n > len(data):
            raise ModError('The DDS file is shorter than its header says.')
        out.append(data[off:off + n])
        off += n
    return out


def import_dds(dds_path, bntx_bytes, texture_index=0, layer=0, log=None, keep_size=True):
    """Like import_png, from a DDS. A DDS in the texture's own format and size (with enough mip levels)
    is copied in exactly - no recompression. Any other DDS is decoded and compressed like a PNG.
    A DDS holds the channels the texture stores (as the toolkit's own DDS export writes them)."""
    with open(dds_path, 'rb') as f:
        data = f.read()
    info = read_dds(data)
    b = zf.Bntx(bntx_bytes)
    t = b.textures[texture_index]
    target = zf.DXGI.get((t.ftype, t.variant))
    if target is None or not t.supported:
        raise ModError('Importing into %s textures is not supported.' % t.format_name)
    same_fmt = info['dxgi'] is not None and _SRGB_BASE.get(info['dxgi'], info['dxgi']) == _SRGB_BASE.get(target, target)
    _, bw, bh, bpp, _ = zf.FORMAT_TYPES[t.ftype]
    src_layer = layer if info['layers'] == t.array_len else 0
    note = []
    if same_fmt and (info['width'], info['height']) == (t.width, t.height) and info['mips'] >= t.mip_count:
        mips = _dds_layer_mips(data, info, src_layer, t.mip_count, bw, bh, bpp)
        out = bytearray(bntx_bytes)
        for lv, lin in enumerate(mips):
            w, h, wu, hu, bpp_, bhgt = t._mip_geom(lv)
            start = t.mip_offsets[lv] + layer * t.layer_stride
            end = (t.mip_offsets[lv + 1] if lv + 1 < t.mip_count else t.mip_offsets[0] + t.layer_stride) + layer * t.layer_stride
            out[start:end] = swizzle_block_linear(lin, wu, hu, bpp_, bhgt, end - start)
        nt = zf.Bntx(bytes(out)).textures[texture_index]
        if any(nt.linear_mip(layer, lv)[0][:len(mips[lv])] != mips[lv] for lv in range(t.mip_count)):
            raise ModError('Check failed: the texture does not hold the DDS data after importing.')
        note.append('DDS %s copied in exactly (%d mip level(s), no recompression)' % (
            DXGI_NAMES.get(info['dxgi'], info['dxgi']), t.mip_count))
        if log:
            log('; '.join(note))
        return bytes(out), note
    if (same_fmt and not keep_size and (info['width'], info['height']) != (t.width, t.height)
            and info['layers'] == 1):
        try:
            def from_dds(n):
                if info['mips'] < n:
                    raise ModError('not enough mips')
                return _dds_layer_mips(data, info, 0, n, bw, bh, bpp)
            out, n = resize_bntx(bntx_bytes, info['width'], info['height'], from_dds)
            note.append('texture size changed from %dx%d to %dx%d; DDS copied in exactly (%d mips)'
                        % (t.width, t.height, info['width'], info['height'], n))
            if log:
                log('; '.join(note))
            return out, note
        except ModError:
            pass                                      # too few mips in the DDS: decode and compress instead
    # different format, size or mip count: decode the DDS and compress it like a PNG
    with tempfile.TemporaryDirectory() as td:
        sp = os.path.join(td, 'in.dds')
        shutil.copyfile(dds_path, sp)
        _run([TEXCONV, '-nologo', '-y', '-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-m', '1', '-o', td, sp])
        png = os.path.join(td, 'in.png')
        if not os.path.isfile(png):
            raise ModError('Could not read this DDS (format %s).' % DXGI_NAMES.get(info['dxgi'], info['dxgi']))
        why = ('a different format (%s, the texture is %s)' % (DXGI_NAMES.get(info['dxgi'], info['dxgi']),
                                                              DXGI_NAMES.get(target, t.format_name))
               if not same_fmt else 'fewer mip levels (%d of %d)' % (info['mips'], t.mip_count)
               if info['mips'] < t.mip_count else 'a different size')
        out, n2 = import_png(png, bntx_bytes, texture_index, layer, None, keep_size, raw_channels=True)
    note = ['DDS has %s - decoded and compressed again' % why] + list(n2)
    if log:
        log('; '.join(note))
    return out, note


# ---------------------------------------------------------------------------
# mods
# ---------------------------------------------------------------------------

def file_stamp(path):
    """(mtime, size) of a file, or None if it doesn't exist - to notice a file that changed since it was read."""
    try:
        st = os.stat(path)
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


class Mod:
    """A mod project: mods/<name>/mod.json + mods/<name>/romfs/<game paths>."""

    def __init__(self, folder):
        self.folder = folder
        self.info = {'name': os.path.basename(folder), 'author': '', 'version': '1.0', 'description': ''}
        p = os.path.join(folder, 'mod.json')
        self.info_broken = False
        if os.path.isfile(p):
            try:
                with open(p, encoding='utf-8-sig') as f:
                    d = json.load(f)
                if not isinstance(d, dict):
                    raise ValueError('not an object')
                self.info.update(d)
            except (OSError, ValueError):
                self.info_broken = True      # kept as mod.json.bak before it is ever overwritten

    @property
    def name(self):
        return self.info.get('name') or os.path.basename(self.folder)

    @property
    def romfs(self):
        return os.path.join(self.folder, 'romfs')

    def save(self):
        os.makedirs(self.romfs, exist_ok=True)
        p = os.path.join(self.folder, 'mod.json')
        if getattr(self, 'info_broken', False) and os.path.isfile(p) and not os.path.isfile(p + '.bak'):
            shutil.copy2(p, p + '.bak')
        with open(p + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(self.info, f, indent=2)
        os.replace(p + '.tmp', p)
        self.info_broken = False

    def files(self):
        """Relative game paths (forward slashes) of every file in this mod."""
        out = []
        for dp, dn, fn in os.walk(self.romfs):
            for f in fn:
                out.append(os.path.relpath(os.path.join(dp, f), self.romfs).replace(os.sep, '/'))
        return sorted(out)

    def path_for(self, rel):
        return os.path.join(self.romfs, *rel.split('/'))

    def add_bytes(self, rel, data):
        p = self.path_for(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(data)
        return p

    def add_file(self, rel, src):
        p = self.path_for(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        shutil.copyfile(src, p)
        return p

    # ---- history: every version of every mod file is kept in <mod>/.history ----
    @property
    def history_dir(self):
        return os.path.join(self.folder, '.history')

    def _hist_index(self):
        try:
            with open(os.path.join(self.history_dir, 'index.json'), encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _hist_last_number(self):
        """Highest version file number in the history folder (0 if none)."""
        try:
            return max([int(f[:6]) for f in os.listdir(self.history_dir) if f[:6].isdigit()] + [0])
        except OSError:
            return 0

    def _hist_save(self, idx):
        os.makedirs(self.history_dir, exist_ok=True)
        tmp = os.path.join(self.history_dir, 'index.json.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(idx, f, indent=1)
        os.replace(tmp, os.path.join(self.history_dir, 'index.json'))

    def snapshot(self, note=''):
        """Record a new version of every mod file that changed since the last snapshot.
        Returns the list of game paths that got a new version."""
        import hashlib
        import time as _t
        idx = self._hist_index()
        new = []
        for rel in self.files():
            p = self.path_for(rel)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_size > HISTORY_MAX_SIZE:
                continue
            vers = idx.setdefault(rel, [])
            if vers and vers[-1]['mtime'] == st.st_mtime_ns and vers[-1]['size'] == st.st_size:
                continue
            with open(p, 'rb') as f:
                data = f.read()
            h = hashlib.sha1(data).hexdigest()
            if vers and vers[-1]['sha1'] == h:
                vers[-1]['mtime'] = st.st_mtime_ns
                continue
            if not isinstance(idx.get('_counter'), int):
                idx['_counter'] = self._hist_last_number()
            n = idx['_counter'] + 1
            idx['_counter'] = n
            name = '%06d%s' % (n, os.path.splitext(rel)[1])
            os.makedirs(self.history_dir, exist_ok=True)
            with open(os.path.join(self.history_dir, name), 'wb') as f:
                f.write(data)
            vers.append({'file': name, 'time': _t.time(), 'size': st.st_size, 'mtime': st.st_mtime_ns,
                         'sha1': h, 'note': note})
            while len(vers) > HISTORY_MAX:
                old = vers.pop(0)
                try:
                    os.remove(os.path.join(self.history_dir, old['file']))
                except OSError:
                    pass
            new.append(rel)
        if new or not os.path.isfile(os.path.join(self.history_dir, 'index.json')):
            self._hist_save(idx)
        return new

    def history(self, rel):
        """Saved versions of `rel`, oldest first: dicts with 'path', 'time', 'size', 'sha1'."""
        out = []
        for v in self._hist_index().get(rel, []):
            v = dict(v)
            v['path'] = os.path.join(self.history_dir, v['file'])
            if os.path.isfile(v['path']):
                out.append(v)
        return out

    def history_files(self):
        return sorted(k for k in self._hist_index() if not k.startswith('_'))

    def restore(self, rel, version_path):
        self.add_file(rel, version_path)

    def remove(self, rel):
        p = self.path_for(rel)
        if os.path.isfile(p):
            os.remove(p)
        d = os.path.dirname(p)
        while os.path.normcase(d) != os.path.normcase(self.romfs) and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)


HISTORY_MAX = 30                  # versions kept per file


# ---------------------------------------------------------------------------
# repair: versions up to 1.3.1 rebuilt FlatBuffer files without --force-defaults, which dropped every
# stored field whose value equalled the schema default (0). Where the game's own default differs
# (e.g. .trmsh vertexDeclarationIndex = -1), the game then read the wrong value and could crash.
# ---------------------------------------------------------------------------

def _missing_scalars(orig, mod, full, path=()):
    """Fields stored in the game's file but missing from the mod's file, with the value the mod's file
    means for them (its schema default, from `full` = the mod file decoded with --defaults-json).
    Lists are only compared where they have the same length, so changed structures are left alone."""
    out = []
    if isinstance(orig, dict) and isinstance(mod, dict) and isinstance(full, dict):
        for k, v in orig.items():
            if k not in mod:
                if not isinstance(v, (dict, list)) and k in full and not isinstance(full[k], (dict, list)):
                    out.append((path + (k,), full[k]))
            else:
                out += _missing_scalars(v, mod[k], full.get(k), path + (k,))
    elif isinstance(orig, list) and isinstance(mod, list) and isinstance(full, list)             and len(orig) == len(mod) == len(full):
        for i, (a, b, c) in enumerate(zip(orig, mod, full)):
            out += _missing_scalars(a, b, c, path + (i,))
    return out


def dropped_defaults(mod_file, game_file, schema=None):
    """[(field path, value)] that an older version dropped from mod_file; [] when nothing is missing."""
    schema = schema or schema_for(game_file)
    if not schema:
        return []
    orig = json.loads(to_json(game_file, schema))
    mod = json.loads(to_json(mod_file, schema))
    full = json.loads(to_json(mod_file, schema, defaults=True))
    return _missing_scalars(orig, mod, full)


def repair_dropped_defaults(mod_file, game_file, schema=None):
    """Write the dropped fields back into mod_file (same values the file already meant, now stored
    explicitly like the game does). Returns how many fields were restored."""
    schema = schema or schema_for(game_file)
    missing = dropped_defaults(mod_file, game_file, schema)
    if not missing:
        return 0
    mod = json.loads(to_json(mod_file, schema))
    for path, value in missing:
        o = mod
        for k in path[:-1]:
            o = o[k]
        o[path[-1]] = value
    from_json(json.dumps(mod), schema, mod_file)
    return len(missing)


REPAIR_EXTS = ('.trmsh', '.trmdl', '.trmbf', '.trmtr', '.trskl', '.tracm', '.tranm', '.trscn', '.trins', '.bin',
               '.trpmcatalog', '.trmmt')


def find_files_to_repair(game_root, mods=None):
    """[(mod, rel, number of dropped fields)] over all mods. Skips files the game doesn't have."""
    out = []
    for m in (mods if mods is not None else list_mods()):
        for rel in m.files():
            if os.path.splitext(rel)[1].lower() not in REPAIR_EXTS:
                continue
            game = os.path.join(game_root, *rel.split('/'))
            if not os.path.isfile(game) or not schema_for(game):
                continue
            try:
                n = len(dropped_defaults(m.path_for(rel), game))
            except Exception:  # noqa: BLE001 - unreadable or not a FlatBuffer: leave it alone
                continue
            if n:
                out.append((m, rel, n))
    return out
HISTORY_MAX_SIZE = 96 << 20       # bigger files (sound packages) are not versioned


def list_mods():
    os.makedirs(MODS_DIR, exist_ok=True)
    return [Mod(os.path.join(MODS_DIR, d)) for d in sorted(os.listdir(MODS_DIR))
            if os.path.isdir(os.path.join(MODS_DIR, d))]


def folder_name(name):
    """Folder name for a mod name (characters Windows doesn't allow become _)."""
    return re.sub(r'[<>:"/\\|?*]+', '_', name).strip().rstrip('.') or 'New Mod'


def rename_mod(mod, new_name):
    """Rename a mod: its name in mod.json and its folder. Returns the Mod at its new place."""
    new_name = new_name.strip()
    if not new_name:
        raise ModError('A mod needs a name.')
    old = mod.folder
    new = os.path.join(os.path.dirname(old), folder_name(new_name))
    if new != old:
        if os.path.normcase(new) != os.path.normcase(old) and os.path.exists(new):
            raise ModError('There is already a mod folder called "%s".' % os.path.basename(new))
        try:
            os.rename(old, new)
        except OSError as e:
            raise ModError('Could not rename the folder "%s" (%s). Close anything that has a file of this mod '
                           'open, e.g. a 3D viewer or Explorer window, and try again.' % (os.path.basename(old), e))
    m = Mod(new)
    m.info['name'] = new_name
    m.save()
    return m


def create_mod(name, author='', description=''):
    safe = folder_name(name)
    folder = os.path.join(MODS_DIR, safe)
    if os.path.exists(folder):
        raise ModError('A mod called "%s" already exists.' % safe)
    m = Mod(folder)
    m.info.update({'name': name, 'author': author, 'description': description})
    m.save()
    return m


class ExternalMod:
    """A mod made elsewhere: a folder or .zip that contains a romfs folder (any depth)."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.splitext(os.path.basename(path.rstrip('\\/')))[0]
        self.entries = {}                      # rel game path -> (kind, source)
        if os.path.isdir(path):
            for dp, dn, fn in os.walk(path):
                for f in fn:
                    full = os.path.join(dp, f)
                    rel = self._rel(os.path.relpath(full, path).replace(os.sep, '/'))
                    if rel:
                        self.entries[rel] = ('file', full)
        elif zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                for n in z.namelist():
                    if not n.endswith('/'):
                        rel = self._rel(n)
                        if rel:
                            self.entries[rel] = ('zip', n)
        else:
            raise ModError('Not a folder or .zip: ' + path)

    @staticmethod
    def _rel(p):
        parts = p.replace('\\', '/').split('/')
        low = [x.lower() for x in parts]
        if 'romfs' in low:
            i = len(low) - 1 - low[::-1].index('romfs')
            rest = parts[i + 1:]
            # a path that would leave the mod folder ('..', a drive or an absolute path) is not a game file
            if not rest or any(x in ('', '.', '..') or ':' in x for x in rest):
                return None
            return '/'.join(rest)
        return None

    def files(self):
        return sorted(self.entries)

    def read(self, rel):
        kind, src = self.entries[rel]
        if kind == 'file':
            with open(src, 'rb') as f:
                return f.read()
        with zipfile.ZipFile(self.path) as z:
            return z.read(src)


def _mod_source(m):
    if isinstance(m, Mod):
        return m.name, m.files(), (lambda rel, m=m: open(m.path_for(rel), 'rb').read())
    return m.name, m.files(), m.read


def merge_mods(mods, game_root=None, log=None):
    """mods: highest priority first. Returns ({rel: (mod name, reader)}, conflicts {rel: [names]}).
    With game_root, data tables and text that several mods change are merged row by row (za_merge):
    those files come back as ('merged: A + B', reader) and are not in conflicts unless a field was changed
    differently by two mods. Byte-identical copies are never a conflict."""
    winners, conflicts = {}, {}
    sources = [_mod_source(m) for m in mods]
    for name, files, reader in sources:
        for rel in files:
            if rel.lower() == 'arc/data.trpfd':
                continue                        # never ship someone else's index; we build our own
            if rel in winners:
                conflicts.setdefault(rel, [winners[rel][0]]).append(name)
            else:
                winners[rel] = (name, reader)
    if conflicts:
        try:
            import za_merge
            merged, report = za_merge.merge_all(
                [(n, [f for f in fs if f.lower() != 'arc/data.trpfd'], r) for n, fs, r in sources], game_root, log)
        except Exception as e:  # noqa: BLE001 - merging is a bonus; never block packaging
            merged, report = {}, {}
            if log:
                log('mod merging skipped: %s' % e)
        same = {}
        for rel, rep in report.items():
            if rep['how'] == 'identical':
                conflicts.pop(rel, None)
                same.setdefault(' and '.join('"%s"' % n for n in rep['mods']), []).append(rel)
                continue
            data = merged[rel]
            winners[rel] = ('merged: ' + ' + '.join(rep['mods']), lambda r, d=data: d)
            if rep['conflicts']:
                conflicts[rel] = rep['mods']
            else:
                conflicts.pop(rel, None)
            if log:
                log('merged %s from %s%s' % (rel, ' + '.join('"%s"' % n for n in rep['mods']),
                                            ('; %d field(s) changed differently: %s' % (
                                                len(rep['conflicts']), '; '.join(rep['conflicts'][:3])))
                                            if rep['conflicts'] else ' (no clashes)'))
        if log:
            for who, rels in same.items():
                log('%d file(s) are the same in %s - no conflict (e.g. %s)' % (len(rels), who, rels[0]))
    return winners, conflicts


TRPFD_FBS = '''namespace Trinity;
table FileInfo { pack_index: ulong (id: 0); unused_table: uint (id: 1); }
table PackInfo { file_size: ulong (id: 0); file_count: ulong (id: 1); }
table CustomFileDescriptor {
  file_hashes: [ulong] (id: 0); pack_names: [string] (id: 1); file_info: [FileInfo] (id: 2);
  pack_info: [PackInfo] (id: 3); unused_hashes: [ulong] (id: 4); unused_file_info: [FileInfo] (id: 5);
}
root_type CustomFileDescriptor;
'''


def patch_trpfd(trpfd_path, rel_paths):
    """data.trpfd with the given game files moved to the 'unused' list, so the game reads them
    from the mod's romfs instead of data.trpfs (TrinityModLoader's method). Returns (bytes, count)."""
    schema_dir = os.path.join(SCHEMAS, 'trinity')
    os.makedirs(schema_dir, exist_ok=True)
    schema = os.path.join(schema_dir, 'trpfd.fbs')
    with open(schema, 'w', encoding='utf-8') as f:
        f.write(TRPFD_FBS)
    d = json.loads(to_json(trpfd_path, schema))
    if d.get('unused_hashes'):
        raise ModError('This data.trpfd was already modified. Point the tool at the game\'s original romfs.')
    hashes = d['file_hashes']
    infos = d['file_info']
    remove = {fnv1a64(r) for r in rel_paths}
    keep_h, keep_i, un_h, un_i = [], [], [], []
    for h, inf in zip(hashes, infos):
        if h in remove:
            un_h.append(h)
            un_i.append(inf)
        else:
            keep_h.append(h)
            keep_i.append(inf)
    d['file_hashes'], d['file_info'] = keep_h, keep_i
    d['unused_hashes'], d['unused_file_info'] = un_h, un_i
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 'data.trpfd')
        from_json(json.dumps(d), schema, out)
        with open(out, 'rb') as f:
            return f.read(), len(un_h)


_RECOMP_WORDS = ('recomp', 'lumi', 'z-a', 'za', 'pok', 'game', 'emu', 'switch')


def find_recomp_installs():
    """Folders that contain lasrecomp.exe (ZA Recomp and Lumiose Online installs). Looks at drive
    roots, the user's home/Downloads/Desktop and, deeper, only inside folders whose names suggest a
    game (recomp, lumi, pok, game…), so it stays quick."""
    import string
    roots = [c + ':' + os.sep for c in string.ascii_uppercase if os.path.isdir(c + ':' + os.sep)]
    home = os.path.expanduser('~')
    roots += [home, os.path.join(home, 'Downloads'), os.path.join(home, 'Desktop'), os.path.join(home, 'Documents')]
    out, seen = [], set()

    def scan(d, depth):
        key = os.path.normcase(d)
        if key in seen:
            return
        seen.add(key)
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        if any(e.name.lower() == 'lasrecomp.exe' and e.is_file() for e in entries):
            out.append(d)
            return
        if depth >= 3:
            return
        for e in entries:
            n = e.name.lower()
            if n.startswith(('$', '.')) or n in ('windows', 'appdata', 'programdata', 'node_modules'):
                continue
            if depth >= 1 and not any(w in n for w in _RECOMP_WORDS) and                     not any(w in os.path.basename(d).lower() for w in _RECOMP_WORDS):
                continue
            try:
                if e.is_dir():
                    scan(e.path, depth + 1)
            except OSError:
                continue
    for r in roots:
        scan(r, 0)
    return out


def install_recomp(mods, recomp_dir, name, log=print, game_root=None):
    """Copy the merged mod files into ZA Recomp's mods folder (mods/<name>/romfs). Recomp handles
    the file index itself; order / on-off is managed on its Mods page. Replaces an earlier copy."""
    exe = os.path.join(recomp_dir, 'lasrecomp.exe')
    if not os.path.isfile(exe):
        raise ModError('ZA Recomp not found in %s (lasrecomp.exe missing).' % recomp_dir)
    winners, conflicts = merge_mods(mods, game_root, log)
    if not winners:
        raise ModError('The selected mod(s) contain no files.')
    safe = re.sub(r'[<>:"/\\|?*]+', '_', name).strip().strip('.')
    if not safe:
        raise ModError('Pick a package name with letters or numbers in it.')
    dest = os.path.join(recomp_dir, 'mods', safe)
    if os.path.isdir(dest):
        marker = os.path.join(dest, 'za_mod_info.json')
        if not os.path.isfile(marker):
            raise ModError('mods\\%s already exists and was not made by Lumiose Toolkit - not touching it. '
                           'Pick another package name.' % safe)
    # written next to the old install first, so a failure halfway leaves the old one working
    tmp = dest + '.installing'
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    try:
        for rel, (mname, reader) in winners.items():
            p = os.path.join(tmp, 'romfs', *rel.split('/'))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'wb') as f:
                f.write(reader(rel))
        with open(os.path.join(tmp, 'za_mod_info.json'), 'w', encoding='utf-8') as f:
            json.dump({'name': name, 'mods': [_mod_source(m)[0] for m in mods], 'files': len(winners),
                       'conflicts': conflicts}, f, indent=2)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.replace(tmp, dest)
    log('installed %d file(s) into %s' % (len(winners), dest))
    return dest


TARGETS = {
    'recomp': 'ZA Recomp / Lumiose Online',
    'switch': 'Switch (Atmosphere)',
    'emulator': 'Emulator (Ryujinx-style)',
}


def build_package(mods, out_dir, targets, game_romfs=None, package_name=None, log=print, game_root=None):
    """Merge `mods` (highest priority first) and write one .zip per target into out_dir.
    Returns list of written zip paths. Needs game_romfs (with arc/data.trpfd) for switch/emulator."""
    winners, conflicts = merge_mods(mods, game_root, log)
    if not winners:
        raise ModError('The selected mod(s) contain no files.')
    name = package_name or (mods[0].name if len(mods) == 1 else 'Combined mods')
    safe = re.sub(r'[<>:"/\\|?*]+', '_', name).strip()
    os.makedirs(out_dir, exist_ok=True)
    for rel, names in sorted(conflicts.items()):
        log('conflict: %s  ->  "%s" wins over %s' % (rel, names[0], ', '.join('"%s"' % n for n in names[1:])))
    trpfd = None
    if {'switch', 'emulator'} & set(targets):
        src = os.path.join(game_romfs or '', 'arc', 'data.trpfd')
        if not os.path.isfile(src):
            raise ModError('Switch/emulator packages need the original game romfs (with arc\\data.trpfd). '
                           'Set it in the Mods tab.')
        trpfd, n = patch_trpfd(src, list(winners))
        log('data.trpfd: %d of %d mod files redirected to romfs (others are new files)' % (n, len(winners)))
    written = []
    for tgt in targets:
        if tgt == 'recomp':
            prefix = '%s/romfs/' % safe
        elif tgt == 'switch':
            prefix = 'atmosphere/contents/%s/romfs/' % TITLE_ID
        elif tgt == 'emulator':
            prefix = '%s/%s/romfs/' % (TITLE_ID, safe)
        else:
            continue
        zp = os.path.join(out_dir, '%s (%s).zip' % (safe, TARGETS[tgt].split(' (')[0].replace('/', '+')))
        with zipfile.ZipFile(zp, 'w', zipfile.ZIP_DEFLATED) as z:
            for rel, (mname, reader) in sorted(winners.items()):
                z.writestr(prefix + rel, reader(rel))
            if tgt in ('switch', 'emulator') and trpfd:
                z.writestr(prefix + 'arc/data.trpfd', trpfd)
            info = {'name': name, 'mods': [_mod_source(m)[0] for m in mods], 'files': len(winners),
                    'target': TARGETS[tgt], 'conflicts': {k: v for k, v in conflicts.items()}}
            z.writestr(prefix.split('romfs/')[0] + 'za_mod_info.json', json.dumps(info, indent=2))
        written.append(zp)
        log('wrote %s (%d files)' % (zp, len(winners)))
    return written, conflicts
