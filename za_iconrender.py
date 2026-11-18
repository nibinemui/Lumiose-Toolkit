"""Render Pokémon icons in Z-A's style in a hidden browser window (pywebview, like the 3D viewer).

    render(job, out_dir) -> {shot name: png path}
job = {'models': {'normal': glb path, 'shiny': glb path}, 'exclude': regex of material names left out of the framing
       (e.g. flames), 'shots': [{'name', 'model', 'clip', 't', 'yaw', 'pitch', 'margin', 'up', 'size'}], 'style': {}}
The page is tools/icon_render/pokemon_icon.html. In the .exe this module runs as `--iconrender job.json`."""
import base64
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

APP_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(APP_DIR, 'tools')
PAGE = os.path.join(TOOLS, 'icon_render', 'pokemon_icon.html')
EDITOR = os.path.join(TOOLS, 'icon_render', 'icon_editor.html')

# lively animations for icon poses, best first
POSE_ANIMS = ('00300_roar01', '00550_glad01', '00310_appeal01', '00400_attack01', '00450_rangeattack01',
              '00560_notice01', '00001_battlewait01_loop', '00000_defaultwait01_loop')


def command(job_path):
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--iconrender', job_path]
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    return [w if os.path.isfile(w) else exe, os.path.abspath(__file__), job_path]


def edit(job, out_dir):
    """Open the icon editor window (turn, zoom, move, pick the animation frame) for job['models'];
    returns {'final_normal': png, 'final_shiny': png} when the user presses "Use this icon", else {}."""
    job = dict(job, mode='edit', timeout=24 * 3600, shots=[{'name': 'final_normal'}, {'name': 'final_shiny'}])
    for f in ('final_normal.png', 'final_shiny.png'):
        try:
            os.remove(os.path.join(out_dir, f))
        except OSError:
            pass
    return render(job, out_dir, timeout=24 * 3600)


def render(job, out_dir, timeout=180):
    """Run the hidden render window for `job`; returns {name: png path} for the shots it saved."""
    os.makedirs(out_dir, exist_ok=True)
    job = dict(job, out_dir=out_dir)
    fd, path = tempfile.mkstemp(suffix='.json', prefix='iconjob_')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(job, f)
    try:
        p = subprocess.Popen(command(path), cwd=APP_DIR, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            p.wait(timeout)
        except subprocess.TimeoutExpired:
            p.kill()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    out = {}
    for s in job['shots']:
        f = os.path.join(out_dir, s['name'] + '.png')
        if os.path.isfile(f):
            out[s['name']] = f
    return out


def export_models(model_path, mod_romfs, game_root, out_dir, rare_path=None, anims_dir=None):
    """glb files of the model (normal and shiny) with the lively animations it has, for render().
    Returns (models dict, [animation names in clip order])."""
    import za_trinity as zt
    os.makedirs(out_dir, exist_ok=True)
    zt.set_override(game_root, mod_romfs)
    d = anims_dir or os.path.dirname(model_path)
    stem = os.path.splitext(os.path.basename(model_path))[0]
    anims, names = [], []
    for slot in POSE_ANIMS:
        p = os.path.join(d, '%s_%s.tranm' % (stem, slot))
        if os.path.isfile(p):
            try:
                anims.append(zt.Animation(p))
                names.append(slot)
            except Exception:  # noqa: BLE001
                pass
    variants = zt.material_variants(model_path)
    models = {}
    for key, label in (('normal', 'Normal'), ('shiny', 'Shiny')):
        v = rare_path if key == 'shiny' and rare_path else variants.get(label)
        if key == 'shiny' and not v:
            continue
        out = os.path.join(out_dir, key + '.glb')
        zt.export_glb(zt.Model(model_path, [v] if v else None), anims, out_path=out, texture_size=1024)
        models[key] = out
    return models, names


# ---------------------------------------------------------------------------------------------------------
# the render process
# ---------------------------------------------------------------------------------------------------------

def serve(job):
    """Start the local server for the render / editor page: (server, url, done event)."""
    out_dir = job['out_dir']
    done = threading.Event()

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
            path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
            if path == '/done':
                done.set()
                return self.send(200, b'ok', 'text/plain')
            if path == '/job.json':
                return self.send(200, json.dumps({k: v for k, v in job.items() if k != 'models'} |
                                                 {'models': list(job['models'])}).encode(), 'application/json')
            if path.startswith('/glb/'):
                f = job['models'].get(path[5:])
            elif path.startswith('/three/'):
                f = os.path.normpath(os.path.join(TOOLS, *path.strip('/').split('/')))
                if not f.startswith(os.path.join(TOOLS, 'three')):
                    f = None
            else:
                f = EDITOR if job.get('mode') == 'edit' else PAGE
            if not f or not os.path.isfile(f):
                return self.send(404, b'not found', 'text/plain')
            ctype = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript',
                     '.glb': 'model/gltf-binary'}.get(os.path.splitext(f)[1], 'application/octet-stream')
            with open(f, 'rb') as fh:
                self.send(200, fh.read(), ctype)

        def do_POST(self):
            name = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('name', ['icon'])[0]
            name = ''.join(c for c in name if c.isalnum() or c in '_-') or 'icon'
            data = self.rfile.read(int(self.headers['Content-Length'])).decode()
            png = base64.b64decode(data.split(',', 1)[1])
            with open(os.path.join(out_dir, name + '.png'), 'wb') as f:
                f.write(png)
            self.send(200, b'ok', 'text/plain')

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, 'http://127.0.0.1:%d/' % srv.server_address[1], done


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    with open(argv[0], encoding='utf-8') as f:
        job = json.load(f)
    srv, url, done = serve(job)
    import webview
    if job.get('mode') == 'edit':
        win = webview.create_window(job.get('title', 'Icon editor'), url, width=980, height=640, resizable=True,
                                    background_color='#1E1E21')
    else:
        win = webview.create_window('Lumiose Toolkit icons', url, width=700, height=700, x=-32000, y=-32000,
                                    frameless=True, shadow=False, focus=False)

    def watch(w):
        done.wait(job.get('timeout', 150))
        time.sleep(0.2)
        try:
            w.destroy()
        except Exception:  # noqa: BLE001 - already closed by the user
            pass
    webview.start(watch, win)
    srv.shutdown()


if __name__ == '__main__':
    main()
