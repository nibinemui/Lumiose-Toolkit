"""Print a .bfbs (FlatBuffers binary schema) as .fbs-like text. Usage: python bfbs_dump.py file.bfbs"""
import struct
import sys

from za_trinity import Table

BT = ['None', 'UType', 'bool', 'byte', 'ubyte', 'short', 'ushort', 'int', 'uint', 'long', 'ulong',
      'float', 'double', 'string', 'Vector', 'Obj', 'Union', 'Array', 'Vector64']


def dump(path):
    b = open(path, 'rb').read()
    r = Table.root(b)
    objs = r.tables(0)
    enums = r.tables(1)
    names = [o.string(0) for o in objs]
    enames = [e.string(0) for e in enums]

    def tname(t):
        bt, el, idx = t.scalar(0, 'b'), t.scalar(1, 'b'), t.scalar(2, 'i', -1)
        if bt in (15,):
            return names[idx]
        if bt == 14:
            return '[' + (names[idx] if el == 15 else enames[idx] if idx >= 0 else BT[el]) + ']'
        if bt == 16:
            return enames[idx]
        if idx >= 0 and bt <= 10:
            return enames[idx]
        return BT[bt]
    out = []
    for e in enums:
        out.append('enum %s { %s }' % (e.string(0), ', '.join('%s=%d' % (v.string(0), v.scalar(1, 'q')) for v in e.tables(1))))
    for o in objs:
        fields = sorted(o.tables(1), key=lambda f: f.scalar(2, 'H'))
        out.append('%s %s {' % ('struct' if o.scalar(2, 'b') else 'table', o.string(0)))
        for f in fields:
            out.append('  %s:%s;  // id %d' % (f.string(0), tname(f.table(1)), f.scalar(2, 'H')))
        out.append('}')
    out.append('root_type %s;' % names[r.table(4).string(0) and names.index(r.table(4).string(0))])
    return '\n'.join(out)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    print(dump(sys.argv[1]))
