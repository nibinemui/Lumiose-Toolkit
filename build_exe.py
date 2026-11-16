"""Build the Windows release of Lumiose Toolkit with PyInstaller.

    python -m pip install pyinstaller
    python build_exe.py            -> dist/Lumiose Toolkit/  and  dist/Lumiose-Toolkit-<version>-windows.zip

The release folder contains:
    Lumiose Toolkit.exe        the program (also starts itself as the 3D viewer)
    restore_missing.exe        extracts the game archive (command line, see README)
    _internal/                 Python runtime and libraries (PyInstaller)
    tools/ names/              helper programs, schemas, name lists (next to the .exe, like the source layout)
    licenses/ LICENSE THIRD_PARTY_NOTICES.md README.md README.txt
    source/                    the complete source of this version (GPL-2.0 requires it to accompany the .exe)
Files from PokeDocs are not included; the program downloads them on first start.
"""
import os
import shutil
import subprocess
import sys
import zipfile

from za_version import VERSION  # noqa: E402 - one place for the version number
HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, 'dist')
BUILD = os.path.join(HERE, 'build')
NAME = 'Lumiose Toolkit'
OUT = os.path.join(DIST, NAME)

HIDDEN = ['za_map', 'za_pokedit', 'za_dataedit', 'za_textedit', 'za_history', 'za_import', 'za_mated',
          'za_audio', 'za_viewer', 'za_deps', 'za_names', 'za_trinity', 'za_formats', 'za_modding',
          'za_setup', 'restore_missing', 'za_colours', 'za_conflicts', 'za_update', 'za_version', 'za_editor', 'za_prefs', 'za_report', 'za_whatsnew',
          'webview', 'webview.platforms.edgechromium', 'webview.platforms.winforms']
# big packages that may be installed but are never used by the program
EXCLUDE = ['torch', 'tensorflow', 'cv2', 'matplotlib', 'scipy', 'pandas', 'IPython', 'jupyter', 'notebook',
           'accelerate', 'transformers', 'absl', 'sklearn', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6']

SOURCE_FILES = [f for f in os.listdir(HERE) if f.endswith('.py')] + [
    'Lumiose Toolkit.bat', 'requirements.txt', '.gitignore', 'README.md', 'README.txt', 'CHANGELOG.md', 'LICENSE',
    'THIRD_PARTY_NOTICES.md', 'lumiose_toolkit.ico']
# from tools/: everything except files that are downloaded at run time
SKIP_TOOLS = {os.path.join('tools', 'schemas', 'pokedocs'), os.path.join('tools', 'icon_render', 'luxuryball.glb')}


def run(args):
    print('>', ' '.join(args), flush=True)
    subprocess.run(args, check=True, cwd=HERE)


def pyinstaller(script, name, windowed, onefile, extra=()):
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--name', name,
            '--icon', os.path.join(HERE, 'lumiose_toolkit.ico'), '--distpath', DIST, '--workpath', BUILD,
            '--specpath', BUILD]
    args += ['--windowed'] if windowed else ['--console']
    args += ['--onefile'] if onefile else ['--onedir']
    for m in EXCLUDE:
        args += ['--exclude-module', m]
    args += list(extra) + [os.path.join(HERE, script)]
    run(args)


def copytree(src, dst, skip=()):
    for dp, dn, fn in os.walk(src):
        rel = os.path.relpath(dp, HERE)
        if any(rel == s or rel.startswith(s + os.sep) for s in skip) or '__pycache__' in rel:
            continue
        for f in fn:
            if os.path.join(rel, f) in skip:
                continue
            target = os.path.join(dst, os.path.relpath(os.path.join(dp, f), src))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(os.path.join(dp, f), target)


def collect_bundled_licences(dest):
    """The .exe bundles Python, Tcl/Tk and Python packages (NumPy, Pillow, pywebview...): copy the licence
    file of everything that ended up in _internal, so the release carries all of them."""
    import importlib.metadata as md
    internal = os.path.join(OUT, '_internal')
    present = {n.lower().split('.')[0] for n in os.listdir(internal)}
    os.makedirs(dest, exist_ok=True)
    listed = []
    for dist in md.distributions():
        name = dist.metadata['Name']
        tops = (dist.read_text('top_level.txt') or '').split() or [name.replace('-', '_')]
        if not any(t.lower() in present for t in tops):
            continue
        files = [f for f in (dist.files or []) if any(k in f.name.upper() for k in ('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'AUTHORS'))]
        if not files:
            continue
        d = os.path.join(dest, name)
        os.makedirs(d, exist_ok=True)
        for f in files:
            src = f.locate()
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(d, f.name if not os.path.exists(os.path.join(d, f.name)) else
                                               f.parent.name + '_' + f.name))
        lic = (dist.metadata.get('License-Expression') or dist.metadata.get('License') or 'see folder').strip()
        lic = lic if len(lic) < 100 and len(lic.splitlines()) == 1 else 'see licence file in folder'
        listed.append('%s %s - %s' % (name, dist.version, lic))
    base = sys.base_prefix
    for src, nm in ((os.path.join(base, 'LICENSE.txt'), 'Python-LICENSE.txt'),
                    (os.path.join(base, 'tcl', 'tcl8.6', 'license.terms'), 'Tcl-license.terms'),
                    (os.path.join(base, 'tcl', 'tk8.6', 'license.terms'), 'Tk-license.terms')):
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest, nm))
            listed.append(nm)
    if 'Tcl-license.terms' not in listed and 'Tk-license.terms' in listed:
        listed.append('Tcl - same licence terms as Tk (Tk-license.terms), from the Tcl/Tk project')
    with open(os.path.join(dest, 'README.txt'), 'w', encoding='utf-8') as f:
        f.write('Licences of the Python runtime and packages bundled inside Lumiose Toolkit.exe (_internal):\n\n'
                + '\n'.join(sorted(listed)) + '\n')


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    hidden = []
    for m in HIDDEN:
        hidden += ['--hidden-import', m]
    pyinstaller('za_extractor.py', NAME, windowed=True, onefile=False,
                extra=hidden + ['--collect-submodules', 'webview'])
    pyinstaller('restore_missing.py', 'restore_missing', windowed=False, onefile=True)
    shutil.move(os.path.join(DIST, 'restore_missing.exe'), os.path.join(OUT, 'restore_missing.exe'))

    # resources live next to the .exe, exactly as in the source folder
    copytree(os.path.join(HERE, 'tools'), os.path.join(OUT, 'tools'), skip=SKIP_TOOLS)
    os.makedirs(os.path.join(OUT, 'names'), exist_ok=True)
    for f in os.listdir(os.path.join(HERE, 'names')):
        if f != 'za_hashes_inside_fd.txt':                    # PokeDocs file: downloaded at run time
            shutil.copy2(os.path.join(HERE, 'names', f), os.path.join(OUT, 'names', f))
    copytree(os.path.join(HERE, 'licenses'), os.path.join(OUT, 'licenses'))
    for f in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'README.txt', 'CHANGELOG.md', 'lumiose_toolkit.ico'):
        shutil.copy2(os.path.join(HERE, f), os.path.join(OUT, f))
    src = os.path.join(OUT, 'source')
    os.makedirs(src, exist_ok=True)
    for f in SOURCE_FILES:
        if os.path.isfile(os.path.join(HERE, f)):
            shutil.copy2(os.path.join(HERE, f), os.path.join(src, f))
    copytree(os.path.join(HERE, 'licenses'), os.path.join(src, 'licenses'))
    copytree(os.path.join(HERE, 'tools', 'viewer'), os.path.join(src, 'tools', 'viewer'))
    copytree(os.path.join(HERE, 'tools', 'editor'), os.path.join(src, 'tools', 'editor'))
    copytree(os.path.join(HERE, 'tools', 'schemas', 'za'), os.path.join(src, 'tools', 'schemas', 'za'))
    copytree(os.path.join(HERE, 'tools', 'icon_render'), os.path.join(src, 'tools', 'icon_render'), skip=SKIP_TOOLS)
    with open(os.path.join(src, 'README_SOURCE.txt'), 'w', encoding='utf-8') as f:
        f.write('Complete source code of Lumiose Toolkit %s (GPL-2.0).\n'
                'Same files as https://github.com/nibinemui/Lumiose-Toolkit at this version.\n'
                'tools/ (flatc, texconv, vgmstream, three.js, miniPaint and its source, GFTool source, schemas) '
                'is in the main folder.\n'
                'Rebuild the .exe with:  python -m pip install -r requirements.txt pyinstaller  &&  '
                'python build_exe.py\n' % VERSION)

    collect_bundled_licences(os.path.join(OUT, 'licenses', 'bundled-python'))

    zp = os.path.join(DIST, 'Lumiose-Toolkit-%s-windows.zip' % VERSION)
    if os.path.exists(zp):
        os.remove(zp)
    with zipfile.ZipFile(zp, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for dp, dn, fn in os.walk(OUT):
            for f in fn:
                full = os.path.join(dp, f)
                z.write(full, os.path.join(NAME, os.path.relpath(full, OUT)))
    print('\nRelease folder:', OUT)
    print('Release zip:   ', zp, '(%.1f MB)' % (os.path.getsize(zp) / 1048576))


if __name__ == '__main__':
    main()
