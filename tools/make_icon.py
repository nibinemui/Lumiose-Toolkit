"""Draw lumiose_toolkit.ico: the game's Luxury Ball on a rounded tile with a download badge in the corner.

The ball is the game's own model (ik_chara/model_ob/ob0211_00_luxuryball), rendered closed by
tools/icon_render (serve.py + render.html, settings: open=0&rx=8&ry=-20&rz=0&name=ball_closed) to
tools/icon_render/ball_closed.png.  Run:  python tools/make_icon.py
"""
import os

from PIL import Image, ImageDraw, ImageFilter

S = 1024
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), 'lumiose_toolkit.ico')
BALL = os.path.join(HERE, 'icon_render', 'ball_closed.png')

TILE_TOP, TILE_BOTTOM = (40, 40, 46), (6, 6, 8)               # black face of the tile
GREEN_TOP, GREEN_BOTTOM = (52, 211, 120), (22, 163, 74)


def gradient(size, c0, c1, angle_diag=True):
    """c0 at the top (top-left when diagonal) blending to c1 at the bottom (bottom-right)."""
    import numpy as np
    w, h = size
    y, x = np.mgrid[0:h, 0:w]
    t = (x / w * 0.45 + y / h * 0.55) if angle_diag else y / h
    g = Image.fromarray((np.clip(t, 0, 1) * 255).astype(np.uint8))
    a = Image.new('RGB', size, c0)
    b = Image.new('RGB', size, c1)
    return Image.composite(b, a, g)


def tile():
    """Black tile with silver accents: brushed-silver rim, a thin inner silver line, a silver halo
    behind the ball (so the black ball stands out) and a faint gloss."""
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    box = (40, 40, S - 40, S - 40)
    outer = Image.new('L', (S, S), 0)
    ImageDraw.Draw(outer).rounded_rectangle(box, radius=210, fill=255)
    # silver rim: the whole tile in a metallic gradient, then the black face inset inside it
    img.paste(gradient((S, S), (236, 238, 244), (120, 124, 134)), (0, 0), outer)
    rim = 22
    inner = Image.new('L', (S, S), 0)
    ImageDraw.Draw(inner).rounded_rectangle((box[0] + rim, box[1] + rim, box[2] - rim, box[3] - rim),
                                            radius=210 - rim, fill=255)
    img.paste(gradient((S, S), TILE_TOP, TILE_BOTTOM), (0, 0), inner)
    # thin silver line just inside the rim
    line = Image.new('L', (S, S), 0)
    ImageDraw.Draw(line).rounded_rectangle((box[0] + rim + 14, box[1] + rim + 14, box[2] - rim - 14, box[3] - rim - 14),
                                           radius=210 - rim - 14, outline=110, width=4)
    silver = Image.new('RGBA', (S, S), (200, 204, 214, 0))
    silver.putalpha(line)
    img.alpha_composite(silver)
    # silver halo behind the ball
    halo = Image.new('L', (S, S), 0)
    ImageDraw.Draw(halo).ellipse((470 - 330, 465 - 330, 470 + 330, 465 + 330), fill=120)
    halo = Image.composite(halo.filter(ImageFilter.GaussianBlur(70)), Image.new('L', (S, S), 0), inner)
    h = Image.new('RGBA', (S, S), (190, 196, 210, 0))
    h.putalpha(halo)
    img.alpha_composite(h)
    # faint gloss across the top
    gl = Image.new('L', (S, S), 0)
    ImageDraw.Draw(gl).ellipse((-200, -620, S + 200, S * 0.42), fill=28)
    gl = Image.composite(gl.filter(ImageFilter.GaussianBlur(30)), Image.new('L', (S, S), 0), inner)
    white = Image.new('RGBA', (S, S), (255, 255, 255, 0))
    white.putalpha(gl)
    img.alpha_composite(white)
    return img


def ball_layer():
    b = Image.open(BALL).convert('RGBA')
    b = b.crop(b.getbbox())
    size = 690
    b = b.resize((size, int(b.size[1] * size / b.size[0])), Image.LANCZOS)
    layer = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    cx, cy = 470, 465
    # shadow under the ball
    sh = Image.new('L', (S, S), 0)
    ImageDraw.Draw(sh).ellipse((cx - 250, cy + 250, cx + 270, cy + 360), fill=150)
    shadow = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    shadow.putalpha(sh.filter(ImageFilter.GaussianBlur(28)))
    layer.alpha_composite(shadow)
    layer.alpha_composite(b, (cx - b.size[0] // 2, cy - b.size[1] // 2))
    return layer


def badge():
    """Green round badge with a white ring and a white download arrow (like the old icon)."""
    layer = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    cx, cy, r = 762, 762, 168
    sh = Image.new('L', (S, S), 0)
    ImageDraw.Draw(sh).ellipse((cx - r + 6, cy - r + 18, cx + r + 6, cy + r + 18), fill=120)
    shadow = Image.new('RGBA', (S, S), (10, 10, 50, 0))
    shadow.putalpha(sh.filter(ImageFilter.GaussianBlur(18)))
    layer.alpha_composite(shadow)
    d = ImageDraw.Draw(layer)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, 255))           # white ring
    ri = r - 26
    m = Image.new('L', (S, S), 0)
    ImageDraw.Draw(m).ellipse((cx - ri, cy - ri, cx + ri, cy + ri), fill=255)
    g = gradient((S, S), GREEN_TOP, GREEN_BOTTOM, angle_diag=False)
    layer.paste(g, (0, 0), m)
    # arrow: shaft + head
    sw, top, mid, tip, hw = 46, cy - 92, cy + 8, cy + 96, 98
    d.rounded_rectangle((cx - sw // 2, top, cx + sw // 2, mid + 10), radius=10, fill=(255, 255, 255, 255))
    d.polygon([(cx - hw, mid - 12), (cx + hw, mid - 12), (cx, tip)], fill=(255, 255, 255, 255))
    return layer


def compose():
    img = tile()
    img.alpha_composite(ball_layer())
    img.alpha_composite(badge())
    return img


if __name__ == '__main__':
    if not os.path.isfile(BALL):
        raise SystemExit('Render the ball first: tools/icon_render/serve.py, open render.html with '
                         '?size=1400&open=0&axis=y&rx=8&ry=-20&rz=0&name=ball_closed')
    art = compose()
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    imgs = {n: art.resize((n, n), Image.LANCZOS) for n in sizes}
    imgs[256].save(OUT, sizes=[(n, n) for n in sizes], append_images=[imgs[n] for n in sizes[:-1]])
    imgs[256].save(os.path.join(HERE, 'icon_preview.png'))
    art.resize((512, 512), Image.LANCZOS).save(os.path.join(HERE, 'icon_large.png'))
    print('wrote', OUT)
