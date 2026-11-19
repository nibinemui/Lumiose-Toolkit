"""Test for "Apply to all languages" (za_textsync): one line changed in every language reads back as typed, and
every other line of every file keeps its exact bytes.

    python tools/test_textsync.py                 # a made-up game folder (no game files needed)
    python tools/test_textsync.py "E:/ZA EXPORT"  # also a real file of a game dump, in all its languages

Exits with 0 when everything passed. Nothing is written outside a temporary folder."""
import os
import shutil
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import za_formats as zf  # noqa: E402
import za_merge  # noqa: E402
import za_modding as zm  # noqa: E402
import za_textsync as ts  # noqa: E402

# a game dump given on the command line (taken off so unittest doesn't see it)
DUMP = sys.argv.pop() if len(sys.argv) > 1 and os.path.isdir(sys.argv[-1]) else None


def make_dat(texts):
    return za_merge.msg_build([(tuple(zm._encode_text(t)), 0) for t in texts])


def make_tbl(labels):
    ents = []
    for lab in labels:
        raw = lab.encode('utf-8') + b'\0'
        ents.append((struct.pack('<QH', zm.fnv1a64(lab), len(raw)) + raw, lab))
    return za_merge.tbl_build(ents)


def raw_lines(data):
    """Each line's stored bytes (encrypted text) and flag - what must stay identical for unchanged lines."""
    ns, nl, total, ik, so = struct.unpack_from('<HHIII', data, 0)
    out = []
    for i in range(nl):
        off, ln, flag = struct.unpack_from('<IHH', data, so + 4 + i * 8)
        out.append((bytes(data[so + off:so + off + ln * 2]), flag))
    return out


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='lt_textsync_')
        self.root = os.path.join(self.tmp, 'game')
        self.mod = zm.Mod(os.path.join(self.tmp, 'mods', 'test'))
        os.makedirs(self.mod.romfs)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check_saved(self, entries, news):
        """Every changed language: the line reads back as typed, every other line has its old bytes."""
        for e in entries:
            if e['lang'] not in news or e['index'] is None:
                continue
            with open(self.mod.path_for(e['rel']), 'rb') as f:
                after = f.read()
            self.assertTrue(zf.is_msg_dat(after), e['lang'])
            lines = zf.decode_msg(after)
            self.assertEqual(lines[e['index']], news[e['lang']], e['lang'])
            old_lines = zf.decode_msg(e['data'])
            self.assertEqual(len(lines), len(old_lines), e['lang'])
            old_raw, new_raw = raw_lines(e['data']), raw_lines(after)
            for i in range(len(lines)):
                if i != e['index']:
                    self.assertEqual(lines[i], old_lines[i], '%s line %d' % (e['lang'], i))
                    self.assertEqual(new_raw[i], old_raw[i], '%s line %d bytes' % (e['lang'], i))
            self.assertEqual(new_raw[e['index']][1], old_raw[e['index']][1], 'flag kept')


class MadeUp(Base):
    LABELS = ['msg_a', 'msg_b', 'msg_c', 'msg_d']

    def setUp(self):
        super().setUp()
        d = os.path.join(self.root, 'ik_message', 'dat')
        texts = {
            'English': ['Hello!', 'You got [VAR 0100(0)]!\\nGreat.', 'Bye', 'Last line'],
            'French': ['Bonjour !', 'Vous obtenez [VAR 0100(0)] !\\nSuper.', 'Au revoir', 'Dernière ligne'],
            'German': ['Hallo!', 'Du hast [VAR 0100(0)]!', 'Tschüss', 'Letzte Zeile'],
            'JPN_KANJI': ['こんにちは', '[VAR 0100(0)]を手に入れた！', 'さようなら', '最後'],
        }
        for lang, t in texts.items():
            write(os.path.join(d, lang, 'common', 'test.dat'), make_dat(t))
            if lang == 'German':        # labels in another order: the line is found by its label, not its number
                order = [3, 1, 0, 2]
                write(os.path.join(d, lang, 'common', 'test.dat'), make_dat([t[i] for i in order]))
                write(os.path.join(d, lang, 'common', 'test.tbl'), make_tbl([self.LABELS[i] for i in order]))
            elif lang != 'JPN_KANJI':   # no labels: matched by line number
                write(os.path.join(d, lang, 'common', 'test.tbl'), make_tbl(self.LABELS))
        write(os.path.join(d, 'Korean', 'common', 'test.dat'), b'')     # an empty file in the dump
        write(os.path.join(d, 'Korean', 'common', 'test.tbl'), make_tbl(self.LABELS))
        self.rel = 'ik_message/dat/English/common/test.dat'

    def test_gather(self):
        es = {e['lang']: e for e in ts.gather(self.root, self.mod, self.rel, 1, 'msg_b')}
        self.assertEqual(sorted(es), ['English', 'French', 'German', 'JPN_KANJI', 'Korean'])
        self.assertEqual(es['English']['index'], 1)
        self.assertEqual(es['German']['index'], 1)          # msg_b stays at 1 in the reordered file
        self.assertEqual(es['French']['text'], 'Vous obtenez [VAR 0100(0)] !\\nSuper.')
        self.assertEqual(es['JPN_KANJI']['index'], 1)
        self.assertIn('line number', es['JPN_KANJI']['note'])
        self.assertIsNone(es['Korean']['index'])
        es = {e['lang']: e for e in ts.gather(self.root, self.mod, self.rel, 0, 'msg_a')}
        self.assertEqual(es['German']['index'], 2)
        self.assertEqual(es['German']['text'], 'Hallo!')

    def test_same_text_everywhere(self):
        entries = ts.gather(self.root, self.mod, self.rel, 1, 'msg_b')
        new = 'Got [VAR 0100(0)]!\\nNice - ÄÖÜ é 🎉'
        news = {e['lang']: new for e in entries if e['index'] is not None}
        todo = ts.changes(entries, news)
        self.assertEqual(len(todo), 4)
        built = ts.save(self.mod, todo)
        self.assertEqual(len(built), 4)
        self.check_saved(entries, news)
        self.assertFalse(os.path.exists(self.mod.path_for('ik_message/dat/Korean/common/test.dat')))
        # after saving, the mod's copies are read: same text, nothing left to change
        again = ts.gather(self.root, self.mod, self.rel, 1, 'msg_b')
        self.assertTrue(all(e['from_mod'] for e in again if e['index'] is not None))
        self.assertEqual(ts.changes(again, news), [])
        self.assertEqual(ts.changed_since_read(self.mod, again), [])

    def test_one_text_per_language_and_mod_copy(self):
        # the mod already changed another line of the French file: that change is kept
        fr = 'ik_message/dat/French/common/test.dat'
        with open(os.path.join(self.root, *fr.split('/')), 'rb') as f:
            lines = zf.decode_msg(f.read())
        lines[3] = 'Ligne du mod'
        with open(os.path.join(self.root, *fr.split('/')), 'rb') as f:
            self.mod.add_bytes(fr, zm.encode_msg(lines, f.read()))
        entries = ts.gather(self.root, self.mod, self.rel, 2, 'msg_c')
        fe = next(e for e in entries if e['lang'] == 'French')
        self.assertTrue(fe['from_mod'])
        news = {'English': 'See you', 'French': 'À bientôt', 'German': 'Tschüss'}   # German unchanged
        todo = ts.changes(entries, news)
        self.assertEqual(sorted(e['lang'] for e, _ in todo), ['English', 'French'])
        ts.save(self.mod, todo)
        self.check_saved(entries, {'English': 'See you', 'French': 'À bientôt'})
        with open(self.mod.path_for(fr), 'rb') as f:
            self.assertEqual(zf.decode_msg(f.read())[3], 'Ligne du mod')

    def test_stale_and_codes(self):
        entries = ts.gather(self.root, self.mod, self.rel, 1, 'msg_b')
        self.assertEqual(ts.changed_since_read(self.mod, entries), [])
        self.mod.add_bytes('ik_message/dat/French/common/test.dat', b'x')    # another window wrote it
        self.assertEqual(ts.changed_since_read(self.mod, entries), ['French'])
        self.assertEqual(ts.removed_codes('A [VAR 0100(0)] B\\r', 'A B'), ['[VAR 0100(0)]', '\\r'])
        self.assertEqual(ts.removed_codes('A [VAR 0100(0)]', 'x [VAR 0100(0)]'), [])

    def test_unreadable_mod_copy_is_left_alone(self):
        self.mod.add_bytes('ik_message/dat/French/common/test.dat', b'broken')
        fe = next(e for e in ts.gather(self.root, self.mod, self.rel, 1, 'msg_b') if e['lang'] == 'French')
        self.assertIsNone(fe['index'])
        self.assertIn("mod's copy", fe['note'])


@unittest.skipUnless(DUMP, 'no game dump given')
class RealDump(Base):
    def test_every_language(self):
        root = DUMP
        en = os.path.join(root, 'ik_message', 'dat', 'English')
        picked = None
        for dp, dn, fn in os.walk(en):           # a file with a line that has [VAR …] codes and a line break
            for f in sorted(fn):
                if not f.endswith('.dat'):
                    continue
                p = os.path.join(dp, f)
                with open(p, 'rb') as fh:
                    d = fh.read()
                if not zf.is_msg_dat(d):
                    continue
                lines = zf.decode_msg(d)
                for i, t in enumerate(lines):
                    if '[VAR ' in t and '\\n' in t and len(lines) > 5:
                        picked = (os.path.relpath(p, root).replace(os.sep, '/'), i)
                        break
                if picked:
                    break
            if picked:
                break
        self.assertIsNotNone(picked, 'no suitable English text file found')
        rel, i = picked
        labels = ts._labels(os.path.join(root, *rel.split('/')))
        entries = ts.gather(root, self.mod, rel, i, labels[i] if i < len(labels) else None)
        self.assertGreaterEqual(len(entries), 9)
        ok = [e for e in entries if e['index'] is not None]
        self.assertGreaterEqual(len(ok), 9)
        news = {}
        for e in ok:
            news[e['lang']] = e['text'] + ' (synced)'
        ts.save(self.mod, ts.changes(entries, news))
        self.check_saved(entries, news)
        print('\n  real dump: %s line %d changed in %d languages: %s'
              % (rel, i, len(ok), ', '.join(e['lang'] for e in ok)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
