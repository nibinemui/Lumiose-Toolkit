"""File format readers for the ZA export.

Supports:
  * BNTX textures (Switch block-linear, BC1-BC7, RGBA8, float formats) -> PIL images / DDS
  * Message text tables (.dat + .tbl labels)                           -> text
  * SARC archives (.arc)                                                -> inner files
"""
import math
import struct

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _u16(d, o):
    return struct.unpack_from('<H', d, o)[0]


def _u32(d, o):
    return struct.unpack_from('<I', d, o)[0]


def _u64(d, o):
    return struct.unpack_from('<Q', d, o)[0]


def _ceil_div(a, b):
    return (a + b - 1) // b


def _pow2_roundup(x):
    x = max(1, x)
    return 1 << (x - 1).bit_length()


# ---------------------------------------------------------------------------
# BNTX
# ---------------------------------------------------------------------------

# format type byte -> (name, block w, block h, bytes per block/pixel, kind)
FORMAT_TYPES = {
    0x02: ('R8', 1, 1, 1, 'raw'),
    0x09: ('R8G8', 1, 1, 2, 'raw'),
    0x0B: ('R8G8B8A8', 1, 1, 4, 'raw'),
    0x0C: ('B8G8R8A8', 1, 1, 4, 'raw'),
    0x14: ('R32', 1, 1, 4, 'raw'),
    0x15: ('R16G16B16A16', 1, 1, 8, 'raw'),
    0x19: ('R32G32B32A32', 1, 1, 16, 'raw'),
    0x1A: ('BC1', 4, 4, 8, 'bc'),
    0x1B: ('BC2', 4, 4, 16, 'bc'),
    0x1C: ('BC3', 4, 4, 16, 'bc'),
    0x1D: ('BC4', 4, 4, 8, 'bc'),
    0x1E: ('BC5', 4, 4, 16, 'bc'),
    0x1F: ('BC6H', 4, 4, 16, 'bc'),
    0x20: ('BC7', 4, 4, 16, 'bc'),
}

VARIANTS = {1: 'UNORM', 2: 'SNORM', 3: 'UINT', 4: 'SINT', 5: 'FLOAT', 6: 'SRGB', 0xA: 'UFLOAT'}

# (type, variant) -> DXGI_FORMAT for DDS export
DXGI = {
    (0x1A, 1): 71, (0x1A, 6): 72, (0x1B, 1): 74, (0x1B, 6): 75,
    (0x1C, 1): 77, (0x1C, 6): 78, (0x1D, 1): 80, (0x1D, 2): 81,
    (0x1E, 1): 83, (0x1E, 2): 84, (0x1F, 0xA): 95, (0x1F, 5): 96,
    (0x20, 1): 98, (0x20, 6): 99, (0x0B, 1): 28, (0x0B, 6): 29,
    (0x0C, 1): 87, (0x0C, 6): 91, (0x02, 1): 61, (0x09, 1): 49,
    (0x14, 5): 41, (0x15, 5): 10, (0x19, 5): 2,
}

# Pillow "bcn" decoder settings: type -> (mode, n, pixel_format)
_PIL_BCN = {
    0x1A: ('RGBA', 1, 'BC1'),
    0x1B: ('RGBA', 2, 'BC2'),
    0x1C: ('RGBA', 3, 'BC3'),
    0x1D: ('L', 4, 'BC4'),
    0x1E: ('RGB', 5, 'BC5'),
    0x1F: ('RGB', 6, 'BC6H'),
    0x20: ('RGBA', 7, 'BC7'),
}


class FormatError(Exception):
    pass


def deswizzle_block_linear(data, width_units, height_units, bpp, block_height):
    """Convert Tegra X1 block-linear data to linear rows.

    width_units/height_units are in blocks (BCn) or pixels (raw formats).
    Returns a bytes object of height_units rows of width_units*bpp bytes.
    """
    row_bytes = width_units * bpp
    gobs_x = _ceil_div(row_bytes, 64)
    padded_w = gobs_x * 64
    gob_rows = 8 * block_height
    padded_h = _ceil_div(height_units, gob_rows) * gob_rows
    total = gobs_x * 512 * block_height * (padded_h // gob_rows)

    buf = np.frombuffer(data, dtype=np.uint8)
    if buf.size < total:
        buf = np.concatenate([buf, np.zeros(total - buf.size, np.uint8)])
    else:
        buf = buf[:total]
    units = buf.reshape(-1, 16)

    xb = np.arange(padded_w // 16, dtype=np.int64) * 16
    xaddr = (xb // 64) * 512 * block_height + ((xb % 64) // 32) * 256 + ((xb % 32) // 16) * 32
    y = np.arange(height_units, dtype=np.int64)
    yaddr = ((y // gob_rows) * 512 * block_height * gobs_x
             + ((y % gob_rows) // 8) * 512 + ((y % 8) // 2) * 64 + (y % 2) * 16)
    idx = (yaddr[:, None] + xaddr[None, :]) // 16
    out = units[idx].reshape(height_units, padded_w)[:, :row_bytes]
    return np.ascontiguousarray(out).tobytes()


class BntxTexture:
    def __init__(self, data, off):
        if data[off:off + 4] != b'BRTI':
            raise FormatError('Bad BRTI block')
        self.tile_mode = _u16(data, off + 0x12)
        self.mip_count = max(1, _u16(data, off + 0x16))
        self.format = _u32(data, off + 0x1C)
        self.width, self.height, self.depth, self.array_len = struct.unpack_from('<iiii', data, off + 0x24)
        self.array_len = max(1, self.array_len)
        self.depth = max(1, self.depth)
        self.block_height_log2 = _u32(data, off + 0x34) & 7
        self.image_size = _u32(data, off + 0x50)
        comp = _u32(data, off + 0x58)
        self.comp_sel = [(comp >> (8 * i)) & 0xFF for i in range(4)]
        self.dim_type = _u32(data, off + 0x5C)
        name_off = _u64(data, off + 0x60)
        nlen = _u16(data, name_off)
        self.name = data[name_off + 2:name_off + 2 + nlen].decode('utf-8', 'replace')
        ptrs = _u64(data, off + 0x70)
        self.mip_offsets = [_u64(data, ptrs + 8 * i) for i in range(self.mip_count)]
        self.layer_stride = self.image_size // self.array_len
        self.ftype = self.format >> 8
        self.variant = self.format & 0xFF
        self._data = data

    # -- description -----------------------------------------------------
    @property
    def is_cubemap(self):
        return self.dim_type in (3, 9) and self.array_len % 6 == 0

    @property
    def looks_like_normal_map(self):
        # two-channel BC5 whose R/G stay R/G (UI textures remap BC5 to grey+alpha instead)
        return self.ftype == 0x1E and self.comp_sel[0] == 2 and self.comp_sel[1] == 3 and self.comp_sel[2] in (0, 4)

    @property
    def supported(self):
        return self.ftype in FORMAT_TYPES and self.tile_mode == 0

    @property
    def format_name(self):
        t = FORMAT_TYPES.get(self.ftype)
        base = t[0] if t else 'type 0x%02X' % self.ftype
        return '%s_%s' % (base, VARIANTS.get(self.variant, '%02X' % self.variant))

    def describe(self):
        s = '%s  %dx%d  %s  mips:%d' % (self.name, self.width, self.height, self.format_name, self.mip_count)
        if self.array_len > 1:
            s += '  layers:%d%s' % (self.array_len, ' (cubemap)' if self.is_cubemap else '')
        if self.depth > 1:
            s += '  depth:%d (only slice 0 shown)' % self.depth
        return s

    # -- raw surface access ---------------------------------------------
    def _mip_geom(self, level):
        _, bw, bh, bpp, _ = FORMAT_TYPES[self.ftype]
        w = max(1, self.width >> level)
        h = max(1, self.height >> level)
        wu, hu = _ceil_div(w, bw), _ceil_div(h, bh)
        lines = (1 << self.block_height_log2) * 8
        shift = 0
        for lv in range(1, level + 1):
            hl = _ceil_div(max(1, self.height >> lv), bh)
            if _pow2_roundup(hl) < lines:
                shift += 1
        bhl2 = max(0, self.block_height_log2 - shift)
        return w, h, wu, hu, bpp, 1 << bhl2

    def linear_mip(self, layer=0, level=0):
        """Deswizzled (linear) data of one mip level of one layer."""
        if not self.supported:
            raise FormatError('Unsupported texture format %s (tile mode %d)' % (self.format_name, self.tile_mode))
        w, h, wu, hu, bpp, bh = self._mip_geom(level)
        base = self.mip_offsets[level] + layer * self.layer_stride
        if level + 1 < self.mip_count:
            end = self.mip_offsets[level + 1] + layer * self.layer_stride
        else:
            end = self.mip_offsets[0] + (layer + 1) * self.layer_stride
        raw = self._data[base:end]
        return deswizzle_block_linear(raw, wu, hu, bpp, bh), (w, h, wu, hu)

    # -- decoding ---------------------------------------------------------
    def decode(self, layer=0, level=0, rebuild_normal_z=False, apply_comp_sel=True):
        """Return an RGBA PIL image for one layer/mip."""
        lin, (w, h, wu, hu) = self.linear_mip(layer, level)
        kind = FORMAT_TYPES[self.ftype][4]
        if kind == 'bc':
            mode, n, pf = _PIL_BCN[self.ftype]
            if self.ftype == 0x1F and self.variant == 5:
                pf = 'BC6HS'
            if self.ftype == 0x1E and self.variant == 2:
                pf = 'BC5S'
            img = Image.frombytes(mode, (wu * 4, hu * 4), lin, 'bcn', (n, pf))
            img = img.crop((0, 0, w, h))
            arr = np.array(img.convert('RGBA'))
        else:
            arr = self._decode_raw(lin, w, h)
        if apply_comp_sel:
            arr = apply_channel_map(arr, self.comp_sel)
        if rebuild_normal_z and self.looks_like_normal_map:
            arr = rebuild_blue(arr)
        return Image.fromarray(arr, 'RGBA')

    def _decode_raw(self, lin, w, h):
        t = self.ftype
        out = np.zeros((h, w, 4), np.uint8)
        out[..., 3] = 255
        if t == 0x02:
            out[..., 0] = np.frombuffer(lin, np.uint8).reshape(h, w)
        elif t == 0x09:
            a = np.frombuffer(lin, np.uint8).reshape(h, w, 2)
            out[..., 0:2] = a
        elif t == 0x0B:
            out = np.frombuffer(lin, np.uint8).reshape(h, w, 4).copy()
        elif t == 0x0C:
            out = np.frombuffer(lin, np.uint8).reshape(h, w, 4)[..., [2, 1, 0, 3]].copy()
        elif t in (0x14, 0x15, 0x19):
            dt, ch = {0x14: (np.float32, 1), 0x15: (np.float16, 4), 0x19: (np.float32, 4)}[t]
            f = np.frombuffer(lin, dt).astype(np.float32).reshape(h, w, ch)
            f = np.nan_to_num(np.clip(f, 0.0, 1.0)) * 255.0 + 0.5
            out[..., :ch] = f.astype(np.uint8)
        else:
            raise FormatError('No decoder for ' + self.format_name)
        return out

    # -- DDS -------------------------------------------------------------
    def to_dds(self):
        dxgi = DXGI.get((self.ftype, self.variant))
        if dxgi is None:
            raise FormatError('No DDS mapping for ' + self.format_name)
        chunks = []
        for layer in range(self.array_len):
            for lv in range(self.mip_count):
                lin, _ = self.linear_mip(layer, lv)
                chunks.append(lin)
        first, _ = self.linear_mip(0, 0)
        cube = self.is_cubemap
        flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 | (0x20000 if self.mip_count > 1 else 0)
        caps = 0x1000 | ((0x400000 | 0x8) if self.mip_count > 1 else 0) | (0x8 if cube or self.array_len > 1 else 0)
        caps2 = 0xFE00 if cube else 0
        hdr = struct.pack('<4sIIIIIII44x', b'DDS ', 124, flags, self.height, self.width,
                          len(first), 1, self.mip_count)
        hdr += struct.pack('<II4s20x', 32, 0x4, b'DX10')
        hdr += struct.pack('<IIII4x', caps, caps2, 0, 0)
        arr_size = self.array_len // 6 if cube else self.array_len
        hdr += struct.pack('<IIIII', dxgi, 3, 0x4 if cube else 0, arr_size, 0)
        return hdr + b''.join(chunks)


class Bntx:
    def __init__(self, data):
        if data[:4] != b'BNTX':
            raise FormatError('Not a BNTX file')
        if data[0xC:0xE] != b'\xff\xfe':
            raise FormatError('Big-endian BNTX not supported')
        self.data = data
        count = _u32(data, 0x24)
        info = _u64(data, 0x28)
        self.textures = [BntxTexture(data, _u64(data, info + 8 * i)) for i in range(count)]


def apply_channel_map(arr, sel):
    src = [np.zeros(arr.shape[:2], np.uint8), np.full(arr.shape[:2], 255, np.uint8),
           arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]]
    if sel == [2, 3, 4, 5] or any(s > 5 for s in sel):
        return arr
    return np.stack([src[s] for s in sel], axis=-1)


def rebuild_blue(arr):
    """Reconstruct the Z (blue) channel of a two-channel normal map."""
    arr = arr.copy()
    x = arr[..., 0].astype(np.float32) / 127.5 - 1.0
    y = arr[..., 1].astype(np.float32) / 127.5 - 1.0
    z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))
    arr[..., 2] = (z * 127.5 + 127.5).astype(np.uint8)
    return arr


# ---------------------------------------------------------------------------
# message text (.dat / .tbl)
# ---------------------------------------------------------------------------

_SPECIAL = {0xBE00: '\\r', 0xBE01: '\\c', 0xBDFF: ''}


def is_msg_dat(data):
    if len(data) < 0x14:
        return False
    ns, nl, total, ik, so = struct.unpack_from('<HHIII', data, 0)
    return ns == 1 and ik == 0 and 0 < so <= len(data) and so + 4 + nl * 8 <= len(data)


def decode_msg(data):
    ns, nl, total, ik, so = struct.unpack_from('<HHIII', data, 0)
    lines, key = [], 0x7C89
    for i in range(nl):
        off, ln, _ = struct.unpack_from('<IHH', data, so + 4 + i * 8)
        k, chars = key, []
        base = so + off
        for j in range(ln):
            if base + j * 2 + 2 > len(data):
                break
            chars.append(_u16(data, base + j * 2) ^ k)
            k = ((k << 3) | (k >> 13)) & 0xFFFF
        lines.append(_render_chars(chars))
        key = (key + 0x2983) & 0xFFFF
    return lines


def _render_chars(c):
    out, i = [], 0
    while i < len(c):
        ch = c[i]
        if ch == 0:
            if any(c[i + 1:]):          # data after the terminator (e.g. Japanese): keep it visible
                out.append('[0000]')
                i += 1
                continue
            break
        if ch == 0x10 and i + 2 < len(c):
            n = c[i + 1]
            cmd = c[i + 2]
            args = c[i + 3:i + 2 + n]
            if cmd in (0xBE00, 0xBE01) and n == 1:     # line scroll / clear without arguments
                out.append(_SPECIAL[cmd])
            else:
                a = ','.join('%X' % x for x in args)
                out.append('[VAR %04X%s]' % (cmd, '(' + a + ')' if a else ''))
            i += 2 + n
            continue
        if ch == 0x0A:
            out.append('\\n')
        elif 0xD800 <= ch < 0xDC00 and i + 1 < len(c) and 0xDC00 <= c[i + 1] < 0xE000:   # surrogate pair
            out.append(chr(0x10000 + ((ch - 0xD800) << 10) + (c[i + 1] - 0xDC00)))
            i += 1
        elif 0x20 <= ch < 0xD800 or 0xE000 <= ch <= 0xFFFD:
            out.append(chr(ch))
        else:
            out.append('[%04X]' % ch)
        i += 1
    return ''.join(out)


def parse_tbl(data):
    if data[:4] != b'AHTB':
        return []
    n = _u32(data, 4)
    p, labels = 8, []
    for _ in range(n):
        if p + 10 > len(data):
            break
        ln = _u16(data, p + 8)
        p += 10
        labels.append(data[p:p + ln].rstrip(b'\0').decode('utf-8', 'replace'))
        p += ln
    return labels


def msg_to_text(dat_bytes, tbl_bytes=None):
    lines = decode_msg(dat_bytes)
    labels = parse_tbl(tbl_bytes) if tbl_bytes else []
    out = []
    for i, t in enumerate(lines):
        lab = labels[i] if i < len(labels) else '#%d' % i
        out.append('%s\t%s' % (lab, t))
    return '\n'.join(out) + '\n'


# ---------------------------------------------------------------------------
# SARC
# ---------------------------------------------------------------------------

def parse_sarc(data):
    """Return list of (name, bytes)."""
    if data[:4] != b'SARC':
        raise FormatError('Not a SARC archive')
    if data[6:8] != b'\xff\xfe':
        raise FormatError('Big-endian SARC not supported')
    hdr_len = _u16(data, 4)
    data_off = _u32(data, 0xC)
    sfat = hdr_len
    if data[sfat:sfat + 4] != b'SFAT':
        raise FormatError('Missing SFAT')
    count = _u16(data, sfat + 6)
    nodes = sfat + 0xC
    sfnt = nodes + 16 * count
    names = sfnt + 8
    files = []
    for i in range(count):
        h, attr, start, end = struct.unpack_from('<IIII', data, nodes + 16 * i)
        if attr >> 24:
            p = names + (attr & 0xFFFF) * 4
            e = data.index(b'\0', p)
            name = data[p:e].decode('utf-8', 'replace')
        else:
            name = '%08X.bin' % h
        files.append((name, data[data_off + start:data_off + end]))
    return files


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------

def detect_kind(name, data):
    if data[:4] == b'BNTX':
        return 'texture'
    if data[:4] == b'SARC':
        return 'archive'
    if name.lower().endswith('.dat') and is_msg_dat(data):
        return 'text'
    return 'other'


def hexdump(data, limit=4096):
    lines = []
    for o in range(0, min(len(data), limit), 16):
        chunk = data[o:o + 16]
        hx = ' '.join('%02X' % b for b in chunk)
        asc = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append('%08X  %-47s  %s' % (o, hx, asc))
    return '\n'.join(lines)


def printable_strings(data, min_len=5, limit=400):
    import re
    out = []
    for m in re.finditer(rb'[\x20-\x7e]{%d,}' % min_len, data):
        out.append(m.group().decode('ascii'))
        if len(out) >= limit:
            break
    return out
