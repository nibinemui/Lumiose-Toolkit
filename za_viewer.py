"""3D model viewer window for Lumiose Toolkit.

    pythonw za_viewer.py <model.trmdl> [--anim <animation.tranm>]

Serves the model (as glTF), three.js and animations from a local HTTP server bound to
127.0.0.1 and shows them in a native window (pywebview / Edge WebView2)."""
import http.server
import json
import os
import socketserver
import sys
import threading
import traceback
import urllib.parse

APP_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))   # the .exe's folder when frozen
sys.path.insert(0, APP_DIR)
import za_trinity as zt  # noqa: E402

TOOLS = os.path.join(APP_DIR, 'tools')
LOG = os.path.join(APP_DIR, 'error_log.txt')


def log(msg):
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write('[viewer] ' + msg + '\n')
    except OSError:
        pass


class State:
    model = None
    glb = b''
    info = {}
    anims = {}          # name -> path
    path = ''
    variants = {}       # label -> material file or None
    mod_romfs = None
    cache = {}          # (variant, stamp) -> glb bytes
    lock = threading.Lock()
    outfit = None       # (base skeleton, [piece .trmdl]) when previewing clothes on the player

    def stamp(self):
        """Changes whenever a file in the mod's romfs changes (used for live reload)."""
        if not self.mod_romfs or not os.path.isdir(self.mod_romfs):
            return '0'
        s = 0
        for dp, dn, fn in os.walk(self.mod_romfs):
            for f in fn:
                try:
                    st = os.stat(os.path.join(dp, f))
                    s += int(st.st_mtime * 1000) + st.st_size
                except OSError:
                    pass
        return str(s)

    clothing = {}       # pattern label -> clothing_sets() entry (player clothing colours)

    def glb_for(self, variant, choices=None):
        choices = choices or {}
        key = (variant, tuple(sorted(choices.items())), self.stamp())
        with self.lock:
            if key not in self.cache:
                mats, ov = None, None
                if variant in self.clothing:
                    mats, ov = zt.clothing_overrides(self.clothing[variant], choices)
                else:
                    v = self.variants.get(variant)
                    if v:
                        mats = [v]
                if self.outfit:
                    m = zt.Outfit(*self.outfit, variant_piece=self.path, variant=(mats, ov) if mats or ov else None,
                                  skin=choices.get('color_skin'))
                else:
                    m = zt.Model(self.path, mats, ov)
                self.model = m
                self.cache = {key: zt.export_glb(m, (), texture_size=1024)}
            return self.cache[key]


def make_handler(state):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            path = urllib.parse.unquote(u.path)
            try:
                if path in ('/', '/viewer.html'):
                    with open(os.path.join(TOOLS, 'viewer', 'viewer.html'), 'rb') as f:
                        return self.send(200, f.read(), 'text/html; charset=utf-8')
                if path == '/model.glb':
                    qs = urllib.parse.parse_qs(u.query)
                    v = qs.get('variant', ['Normal'])[0]
                    choices = {k[3:]: int(x[0]) for k, x in qs.items() if k.startswith('cg_') and x[0].isdigit()}
                    return self.send(200, state.glb_for(v, choices), 'model/gltf-binary')
                if path == '/stamp':
                    return self.send(200, state.stamp().encode(), 'text/plain')
                if path == '/info.json':
                    return self.send(200, json.dumps(state.info).encode(), 'application/json')
                if path == '/anim':
                    name = urllib.parse.parse_qs(u.query).get('name', [''])[0]
                    p = state.anims.get(name)
                    if not p:
                        log('unknown animation %r (query %r, %d known)' % (name, u.query, len(state.anims)))
                        return self.send(404, ('unknown animation: %s' % name).encode(), 'text/plain')
                    a = zt.Animation(p)
                    skel = state.model.skeleton
                    tracks = []
                    for ni, prop, times, vals in a.channels_for(skel):
                        tracks.append({'bone': skel.bones[ni].name, 'path': prop,
                                       'times': [round(float(x), 5) for x in times],
                                       'values': [round(float(x), 6) for x in vals.reshape(-1)]})
                    missing = sum(1 for b in a.tracks if skel is None or b not in skel.index)
                    body = {'tracks': tracks, 'duration': a.duration, 'frames': a.frames, 'fps': a.fps,
                            'missing': missing}
                    return self.send(200, json.dumps(body).encode(), 'application/json')
                if path.startswith('/three/'):
                    full = os.path.normpath(os.path.join(TOOLS, path.lstrip('/')))
                    if not full.startswith(os.path.join(TOOLS, 'three')) or not os.path.isfile(full):
                        return self.send(404, b'not found', 'text/plain')
                    with open(full, 'rb') as f:
                        return self.send(200, f.read(), 'text/javascript')
                return self.send(404, b'not found', 'text/plain')
            except Exception as e:  # noqa: BLE001
                log(traceback.format_exc())
                return self.send(500, str(e).encode(), 'text/plain')
    return H


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    model_path = args[0]
    start_anim = None
    if '--anim' in args:
        start_anim = args[args.index('--anim') + 1]
    state = State()
    state.path = model_path
    if '--mod' in args and '--root' in args:
        state.mod_romfs = args[args.index('--mod') + 1]
        zt.set_override(args[args.index('--root') + 1], state.mod_romfs)
    state.variants = zt.material_variants(model_path)
    anim_dir = None
    if '--outfit' in args:
        # clothing try-on: model_path is the clicked piece; build the outfit around it
        chara = os.path.dirname(os.path.dirname(os.path.dirname(model_path)))      # .../ik_chara
        base_skel = os.path.join(chara, 'model_pc_base', 'model', 'p0_base.trskl')
        pieces = zt.default_outfit(model_path, os.path.join(chara, 'model_pc'))
        state.outfit = (base_skel, pieces)
        state.variants = {'Normal': None}
        anim_dir = os.path.join(chara, 'motion_pc', 'base')
    # player clothing: patterns + colour choices from the .trmmt (like the in-game dress-up screen)
    colours = {}
    try:
        state.clothing = zt.clothing_sets(model_path)
    except Exception:  # noqa: BLE001
        log(traceback.format_exc())
        state.clothing = {}
    if state.clothing:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(model_path))))
        try:
            import za_names
            names = za_names.clothing_colour_names(root, os.path.basename(os.path.dirname(model_path)))
        except Exception:  # noqa: BLE001
            names = {}
        for label, cs in state.clothing.items():
            rows = []
            for g, glabel, n in cs['groups']:
                kind = 'main' if g.endswith('_rep') else 'second' if g.endswith('_con') else None
                nm = names.get((cs['set'], kind), {}) if kind else {}
                if kind:
                    glabel = 'Main colour' if kind == 'main' else 'Second colour'
                opts = [nm.get(i) or ('Tone %d' % (i + 1) if g == 'color_skin' else 'Colour %d' % (i + 1))
                        for i in range(n)]
                default = 0
                try:      # start on the material's own value (e.g. the default skin tone)
                    mats_now = {}
                    for f in cs['trmtr']:
                        mats_now.update(zt.read_materials(f))
                    for mat, prm in cs['params'][g].items():
                        for pn, vals in prm.items():
                            cur = mats_now[mat].ints.get(pn) if mat in mats_now else None
                            if cur in vals:
                                default = vals.index(cur)
                            raise StopIteration
                except (StopIteration, Exception):  # noqa: BLE001
                    pass
                rows.append({'group': g, 'label': glabel, 'options': opts, 'default': default})
            colours[label] = rows
        state.variants = {k: None for k in state.clothing}
    first = next(iter(state.variants), 'Normal')
    state.glb_for(first)
    if state.model.skeleton:
        if anim_dir and os.path.isdir(anim_dir):
            anim_files = sorted(os.path.join(anim_dir, f) for f in os.listdir(anim_dir) if f.endswith('.tranm'))
        else:
            anim_files = zt.related_animations(model_path)
        for p in anim_files:
            state.anims[os.path.splitext(os.path.basename(p))[0]] = p
        if start_anim and os.path.isfile(start_anim):
            state.anims.setdefault(os.path.splitext(os.path.basename(start_anim))[0], start_anim)
    m = state.model
    tris = sum(len(s.indices) // 3 for s in m.submeshes)
    state.info = {
        'name': ('Try-on: ' if state.outfit else '') + os.path.basename(model_path),
        'summary': '%d parts · %d triangles · %d bones · %d materials'
                   % (len(m.submeshes), tris, len(m.skeleton.bones) if m.skeleton else 0, len(m.materials)),
        'animations': sorted(state.anims),
        'start_anim': os.path.splitext(os.path.basename(start_anim))[0] if start_anim else None,
        'variants': list(state.variants),
        'colours': colours,
        'mod': os.path.basename(os.path.dirname(state.mod_romfs)) if state.mod_romfs else '',
    }
    srv = socketserver.ThreadingTCPServer(('127.0.0.1', 0), make_handler(state))
    srv.daemon_threads = True
    port = srv.server_address[1]
    if '--serve' in args:            # no window: just serve (used for testing in a browser)
        print('http://127.0.0.1:%d/viewer.html' % port, flush=True)
        srv.serve_forever()
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    import webview
    webview.create_window('Lumiose Toolkit 3D Viewer - ' + os.path.basename(model_path),
                          'http://127.0.0.1:%d/viewer.html' % port, width=1280, height=800,
                          background_color='#1B1B1D')
    webview.start()
    srv.shutdown()


if __name__ == '__main__':
    try:
        main()
    except Exception:  # noqa: BLE001
        log(traceback.format_exc())
        raise
