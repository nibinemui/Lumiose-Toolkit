"""In-app image editor window for live texture editing: the bundled miniPaint (offline) or Photopea (online).

    za_editor.py minipaint|photopea <png> [--title NAME]

A small local web server serves the editor page and the PNG; "Save to mod" (or Ctrl+S) posts the edited
PNG back, and it replaces <png> on disk. The main window's live edit (live_poll) notices the new file
and imports it into the current mod, exactly as when an external image editor saves it.
"""
import http.server
import os
import socketserver
import sys
import threading
import traceback
import urllib.parse

FROZEN = getattr(sys, 'frozen', False)
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
TOOLS = os.path.join(APP_DIR, 'tools')
LOG = os.path.join(APP_DIR, 'error_log.txt')
ENGINES = {'minipaint': ('minipaint_host.html', 'miniPaint'), 'photopea': ('photopea_host.html', 'Photopea')}
MAX_PNG = 256 << 20


def log(msg):
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write('[editor] ' + msg + '\n')
    except OSError:
        pass


def make_handler(engine, png, title):
    name = os.path.basename(png)
    image_url = '/tex/' + urllib.parse.quote(name)
    page_file = os.path.join(TOOLS, 'editor', ENGINES[engine][0])
    mp_root = os.path.join(TOOLS, 'minipaint')
    types = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css',
             '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml', '.json': 'application/json',
             '.gif': 'image/gif', '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.woff': 'font/woff'}

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
            try:
                if path in ('/', '/index.html'):
                    with open(page_file, encoding='utf-8') as f:
                        page = f.read()
                    esc = lambda s: s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')  # noqa: E731
                    page = (page.replace('__TITLE__', esc(title)).replace('__NAME__', esc(name))
                            .replace('__IMAGE__', image_url))
                    return self.send(200, page.encode('utf-8'), types['.html'])
                if path.startswith('/tex/'):
                    with open(png, 'rb') as f:
                        return self.send(200, f.read(), 'image/png')
                if path.startswith('/minipaint/'):
                    full = os.path.normpath(os.path.join(mp_root, path[len('/minipaint/'):]))
                    if not full.startswith(mp_root + os.sep) or not os.path.isfile(full):
                        return self.send(404, b'not found', 'text/plain')
                    with open(full, 'rb') as f:
                        return self.send(200, f.read(), types.get(os.path.splitext(full)[1].lower(),
                                                                  'application/octet-stream'))
                return self.send(404, b'not found', 'text/plain')
            except Exception as e:  # noqa: BLE001
                log(traceback.format_exc())
                return self.send(500, str(e).encode(), 'text/plain')

        def do_POST(self):
            if urllib.parse.urlparse(self.path).path != '/save':
                return self.send(404, b'not found', 'text/plain')
            try:
                n = int(self.headers.get('Content-Length') or 0)
                if n <= 8 or n > MAX_PNG:
                    return self.send(400, b'no image received', 'text/plain')
                data = self.rfile.read(n)
                if data[:8] != b'\x89PNG\r\n\x1a\n':
                    return self.send(400, b'not a PNG image', 'text/plain')
                tmp = png + '.saving'
                with open(tmp, 'wb') as f:
                    f.write(data)
                os.replace(tmp, png)                  # one step, so live edit never reads half a file
                return self.send(200, b'ok', 'text/plain')
            except Exception as e:  # noqa: BLE001
                log(traceback.format_exc())
                return self.send(500, str(e).encode(), 'text/plain')
    return H


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] not in ENGINES:
        print(__doc__)
        return 2
    engine, png = args[0], os.path.abspath(args[1])
    if not os.path.isfile(png):
        print('not found: ' + png)
        return 2
    label = args[args.index('--title') + 1] if '--title' in args else os.path.basename(png)
    title = 'Lumiose Toolkit - %s - %s' % (ENGINES[engine][1], label)
    srv = socketserver.ThreadingTCPServer(('127.0.0.1', 0), make_handler(engine, png, title))
    srv.daemon_threads = True
    url = 'http://127.0.0.1:%d/' % srv.server_address[1]
    if '--serve' in args:                 # no window (testing in a browser)
        print(url, flush=True)
        srv.serve_forever()
        return 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    import webview
    webview.create_window(title, url, width=1400, height=900, background_color='#1E1E1E', text_select=True)
    webview.start()
    srv.shutdown()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        log(traceback.format_exc())
        raise
