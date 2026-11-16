"""Generate FlatBuffers schemas (.fbs) for Trinity formats from GFTool's C# FlatSharp classes.

    python gen_trinity_fbs.py   ->  tools/schemas/trinity/<ext>.fbs

Source: pkZukan/gftool (GPL-2.0) GFTool.Core/Flatbuffers/{Common,TR}/**.cs
"""
import os
import re
import sys

APP = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(APP, 'tools', 'gftool_src', 'gftool-main', 'GFTool.Core', 'Flatbuffers')
OUT = os.path.join(APP, 'tools', 'schemas', 'trinity')

PRIM = {'uint': 'uint', 'uint32': 'uint', 'int': 'int', 'int32': 'int', 'float': 'float', 'single': 'float',
        'double': 'double', 'bool': 'bool', 'boolean': 'bool', 'byte': 'ubyte', 'sbyte': 'byte',
        'ushort': 'ushort', 'uint16': 'ushort', 'short': 'short', 'int16': 'short', 'ulong': 'ulong',
        'uint64': 'ulong', 'long': 'long', 'int64': 'long', 'string': 'string'}

# file extension -> root class
ROOTS = {'trmdl': 'TRMDL', 'trmsh': 'TRMSH', 'trmbf': 'TRMBF', 'trmtr': 'TRMTR', 'trskl': 'TRSKL',
         'tranm': 'TRANM', 'tracm': 'TRACM', 'trscn': 'TRSCN', 'trins': 'TRINS'}


def strip_comments(s):
    s = re.sub(r'/\*.*?\*/', '', s, flags=re.S)
    return re.sub(r'//[^\n]*', '', s)


def body_after(s, i):
    """Text inside the {...} block starting at the first '{' after index i."""
    j = s.index('{', i)
    depth = 0
    for k in range(j, len(s)):
        if s[k] == '{':
            depth += 1
        elif s[k] == '}':
            depth -= 1
            if depth == 0:
                return s[j + 1:k]
    return s[j + 1:]


def parse(src_dirs):
    enums, types = {}, {}
    for d in src_dirs:
        for dp, dn, fn in os.walk(d):
            for f in fn:
                if not f.endswith('.cs'):
                    continue
                s = strip_comments(open(os.path.join(dp, f), encoding='utf-8-sig').read())
                for m in re.finditer(r'\[FlatBufferEnum\(typeof\((\w+)\)\)\]\s*public\s+enum\s+(\w+)\s*(?::\s*\w+)?', s):
                    base, name = m.group(1), m.group(2)
                    vals, cur = [], -1
                    for item in body_after(s, m.end()).split(','):
                        item = item.strip()
                        if not item:
                            continue
                        mm = re.match(r'(\w+)\s*(?:=\s*([-\w]+))?', item)
                        if not mm:
                            continue
                        if mm.group(2) is not None:
                            try:
                                cur = int(mm.group(2), 0)
                            except ValueError:
                                cur += 1
                        else:
                            cur += 1
                        vals.append((mm.group(1), cur))
                    enums[name] = (PRIM.get(base.lower(), 'uint'), vals)
                for m in re.finditer(r'\[(FlatBufferTable|FlatBufferStruct)[^\]]*\]\s*(?:\[[^\]]*\]\s*)*public\s+(?:sealed\s+)?(?:partial\s+)?class\s+(\w+)', s):
                    kind, name = m.group(1), m.group(2)
                    fields = []
                    for fm in re.finditer(r'\[FlatBufferItem\((\d+)([^\]]*)\)\]\s*(?:\[[^\]]*\]\s*)*public\s+(?:virtual\s+)?([\w<>\[\],\s\.\?]+?)\s+(\w+)\s*\{',
                                          body_after(s, m.end())):
                        idx, extra, ftype, fname = int(fm.group(1)), fm.group(2), fm.group(3).strip(), fm.group(4)
                        dflt = re.search(r'DefaultValue\s*=\s*\(?\w*\)?\s*([-\w\.]+)', extra)
                        fields.append((idx, ftype.replace('?', '').strip(), fname,
                                       dflt.group(1) if dflt else None, 'Deprecated = true' in extra))
                    fields.sort()
                    types[name] = (kind, fields)
    return enums, types


def fb_type(t, enums, types, unions, owner, fname):
    t = t.strip()
    m = re.match(r'(?:IList|List|IReadOnlyList)<(.+)>$', t)
    if m:
        return '[' + fb_type(m.group(1), enums, types, unions, owner, fname)[0] + ']', False
    if t.endswith('[]'):
        return '[' + fb_type(t[:-2], enums, types, unions, owner, fname)[0] + ']', False
    m = re.match(r'FlatBufferUnion<(.+)>$', t)
    if m:
        members = [x.strip() for x in m.group(1).split(',')]
        uname = 'U_%s_%s' % (owner, fname)
        unions[uname] = members
        return uname, True
    t = t.split('.')[-1]
    if t.lower() in PRIM:
        return PRIM[t.lower()], False
    if t in enums or t in types:
        return t, False
    return None, False


def emit(root, enums, types):
    needed, order, unions = set(), [], {}

    def visit(name):
        if name in needed or (name not in types and name not in enums):
            return
        needed.add(name)
        if name in types:
            for idx, ftype, fname, d, dep in types[name][1]:
                inner = re.sub(r'(IList|List|IReadOnlyList)<|>|\[\]|FlatBufferUnion<', ' ', ftype)
                for part in re.split(r'[\s,]+', inner):
                    if part:
                        visit(part.split('.')[-1])
        order.append(name)
    visit(root)
    lines = ['// generated by gen_trinity_fbs.py from pkZukan/gftool (GPL-2.0)', 'namespace Trinity;', '']
    for name in order:
        if name in enums:
            base, vals = enums[name]
            lines.append('enum %s : %s { %s }' % (name, base, ', '.join('%s = %d' % v for v in vals)))
    body = []
    for name in order:
        if name not in types:
            continue
        kind, fields = types[name]
        if kind == 'FlatBufferStruct':
            parts = []
            for idx, ftype, fname, d, dep in fields:
                t, _ = fb_type(ftype, enums, types, unions, name, fname)
                parts.append('  %s: %s;' % (fname, t or 'uint'))
            body.append('struct %s {\n%s\n}' % (name, '\n'.join(parts)))
            continue
        parts, used = [], {}
        for idx, ftype, fname, d, dep in fields:
            t, is_union = fb_type(ftype, enums, types, unions, name, fname)
            if t is None:
                t = 'ubyte'
                dep = True
            fid = idx + 1 if is_union else idx
            if is_union:
                used[idx] = True
            used[fid] = True
            attrs = ['id: %d' % fid] + (['deprecated'] if dep else [])
            # No default values: the game stores e.g. -1 explicitly in some files and 0 in others, so no
            # single default is right. za_modding.from_json writes with --force-defaults instead, which
            # keeps exactly the fields a file had (a dropped 0 would be read by the game as its -1 default).
            if fname in types or fname in enums or fname in unions:
                fname = fname + '_'          # flatc: a field may not share a type's name
            parts.append((fid, '  %s: %s (%s);' % (fname, t, ', '.join(attrs))))
        top = max(used) if used else -1
        for k in range(top + 1):
            if k not in used:
                parts.append((k, '  _unused%d: ubyte (id: %d, deprecated);' % (k, k)))
        parts.sort()
        body.append('table %s {\n%s\n}' % (name, '\n'.join(p for _, p in parts)))
    for uname, members in unions.items():
        lines.append('union %s { %s }' % (uname, ', '.join(members)))
    lines += [''] + body + ['', 'root_type %s;' % root, '']
    return '\n'.join(lines)


def main():
    enums, types = parse([os.path.join(SRC, 'Common'), os.path.join(SRC, 'TR')])
    os.makedirs(OUT, exist_ok=True)
    for ext, root in ROOTS.items():
        if root not in types:
            print('missing', root)
            continue
        with open(os.path.join(OUT, ext + '.fbs'), 'w', encoding='utf-8') as f:
            f.write(emit(root, enums, types))
        print('wrote', ext + '.fbs')


if __name__ == '__main__':
    sys.exit(main())
