"""Serve render.html + three.js + the exported ball, and save what the page renders.
    python tools/icon_render/serve.py [port]      then open http://127.0.0.1:<port>/render.html?...
Saved files: tools/icon_render/<name>.png and <name>.json (mouth position)."""
import base64
import http.server
import json
import os
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)


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
        if path.startswith('/three/'):
            f = os.path.join(TOOLS, *path.strip('/').split('/'))
        else:
            f = os.path.join(HERE, path.strip('/') or 'render.html')
        f = os.path.normpath(f)
        if not (f.startswith(HERE) or f.startswith(os.path.join(TOOLS, 'three'))) or not os.path.isfile(f):
            return self.send(404, b'not found', 'text/plain')
        ctype = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.glb': 'model/gltf-binary'}.get(
            os.path.splitext(f)[1], 'application/octet-stream')
        with open(f, 'rb') as fh:
            self.send(200, fh.read(), ctype)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        name = urllib.parse.parse_qs(u.query).get('name', ['ball'])[0]
        name = ''.join(c for c in name if c.isalnum() or c in '_-') or 'ball'
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        png = base64.b64decode(data['png'].split(',', 1)[1])
        with open(os.path.join(HERE, name + '.png'), 'wb') as f:
            f.write(png)
        with open(os.path.join(HERE, name + '.json'), 'w') as f:
            json.dump({'mouth': data['mouth']}, f)
        self.send(200, ('%s.png %d bytes' % (name, len(png))).encode(), 'text/plain')


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print('http://127.0.0.1:%d/render.html' % port, flush=True)
    http.server.ThreadingHTTPServer(('127.0.0.1', port), H).serve_forever()
