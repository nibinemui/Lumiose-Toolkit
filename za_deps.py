"""Files from pkZukan/PokeDocs, downloaded on first start instead of being shipped with Lumiose Toolkit.

PokeDocs (https://github.com/pkZukan/PokeDocs) publishes no licence, so its files are not
redistributed here: they are fetched from the PokeDocs repository itself when missing.

    python za_deps.py          download whatever is missing (also done by Lumiose Toolkit on start)
    python za_deps.py --force  download everything again
"""
import os
import sys
import tempfile
import urllib.request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BASE = 'https://raw.githubusercontent.com/pkZukan/PokeDocs/main/'
SCHEMAS = os.path.join(APP_DIR, 'tools', 'schemas', 'pokedocs')

# (local file, PokeDocs path, what Lumiose Toolkit uses it for)
FILES = [
    (os.path.join(APP_DIR, 'names', 'za_hashes_inside_fd.txt'), 'ZA/Hashlists/FileSystem/hashes_inside_fd.txt',
     'file names for restore_missing.py (extracting the game archive)'),
    (os.path.join(SCHEMAS, 'dress_up_data.fbs'), 'ZA/Flatbuffers/dress_up_data.fbs',
     'clothing data (Game data tab > Clothing items)'),
    (os.path.join(SCHEMAS, 'trpmcatalog.fbs'), 'ZA/Flatbuffers/trpmcatalog.fbs',
     'Pokemon model catalog (data preview)'),
    (os.path.join(SCHEMAS, 'sv_personal.fbs'), 'SV/Flatbuffers/pml/personal.fbs',
     'Pokemon data (Pokemon data tab)'),
]
# za_personal.fbs = the Scarlet/Violet personal schema with one fix for Z-A: evolutions are a list
DERIVED = [(os.path.join(SCHEMAS, 'za_personal.fbs'), os.path.join(SCHEMAS, 'sv_personal.fbs'),
            [('evo_table: EvoData;', 'evo_table: [EvoData];')])]


def missing():
    return [f for f in FILES if not os.path.isfile(f[0])] + \
        [d for d in DERIVED if not os.path.isfile(d[0])]


def _download(url, dest, log):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers={'User-Agent': 'Lumiose-Toolkit'})
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), suffix='.part')
    try:
        with urllib.request.urlopen(req, timeout=60) as r, os.fdopen(fd, 'wb') as f:
            total = int(r.headers.get('Content-Length') or 0)
            done = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total > 4 << 20:
                    log('  %s: %d / %d MB' % (os.path.basename(dest), done >> 20, total >> 20))
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def ensure(log=print, force=False):
    """Download missing PokeDocs files and build the derived ones. Returns a list of problems
    (empty when everything is in place)."""
    problems = []
    for dest, path, why in FILES:
        if os.path.isfile(dest) and not force:
            continue
        log('Downloading %s from PokeDocs (%s)…' % (os.path.basename(dest), why))
        try:
            _download(BASE + path, dest, log)
        except OSError as e:
            problems.append('%s: %s' % (os.path.basename(dest), e))
    for dest, src, edits in DERIVED:
        if os.path.isfile(dest) and not force:
            continue
        if not os.path.isfile(src):
            problems.append('%s: needs %s' % (os.path.basename(dest), os.path.basename(src)))
            continue
        with open(src, encoding='utf-8') as f:
            text = f.read()
        for old, new in edits:
            if old not in text:
                problems.append('%s: PokeDocs changed %s (could not apply the Z-A fix)'
                                % (os.path.basename(dest), os.path.basename(src)))
                break
            text = text.replace(old, new)
        else:
            with open(dest, 'w', encoding='utf-8', newline='') as f:
                f.write(text)
    return problems


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    probs = ensure(force='--force' in sys.argv)
    if probs:
        print('Some files could not be prepared:\n  ' + '\n  '.join(probs))
        sys.exit(1)
    print('PokeDocs files are in place.')
