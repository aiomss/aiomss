"""Build the desktop launcher icon for AI Ops Console.

Produces assets/aiops-console.ico (multi-resolution PNG-in-ICO) and
assets/aiops-console.png (preview). Pure Pillow, no external assets.
Re-run this file whenever the launcher artwork needs a refresh.
"""
import io
import math
import os
import struct

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
ICO = os.path.join(ASSETS, "aiops-console.ico")
PNG = os.path.join(ASSETS, "aiops-console.png")
S = 1024

BLUE = (79, 110, 246)
PURPLE = (126, 76, 238)
GOLD = (255, 208, 92)


def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def star(d, cx, cy, outer, inner, fill, outline=None, width=0):
    pts = []
    for i in range(8):
        ang = -math.pi / 2 + i * math.pi / 4
        r = outer if i % 2 == 0 else inner
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    d.polygon(pts, fill=fill)
    if outline:
        d.line(pts + [pts[0]], fill=outline, width=width, joint="curve")


def artwork(simple=False):
    """simple=True drops hairline detail so 16-48px copies stay legible."""
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # soft drop shadow
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [74, 84, 950, 958], radius=210, fill=(28, 38, 82, 120))
    base = Image.alpha_composite(base, sh.filter(ImageFilter.GaussianBlur(34)))

    # blue -> purple gradient plate
    plate = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    pd = ImageDraw.Draw(plate)
    for y in range(80, 960):
        pd.line([(72, y), (952, y)], fill=mix(BLUE, PURPLE, (y - 80) / 880) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([72, 80, 952, 960], radius=210, fill=255)
    plate.putalpha(mask)
    base = Image.alpha_composite(base, plate)

    # top-left sheen, clipped to the plate
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).ellipse([-190, -430, 700, 420], fill=(255, 255, 255, 34))
    sheen.putalpha(Image.composite(sheen.getchannel("A"), Image.new("L", (S, S), 0), mask))
    base = Image.alpha_composite(base, sheen)

    # terminal window: dark glass with a bright frame
    w = 15 if simple else 12
    d = ImageDraw.Draw(base)
    d.rounded_rectangle([186, 306, 838, 754], radius=62,
                        fill=(15, 25, 52, 178), outline=(255, 255, 255, 238), width=w)
    d.line([(214, 404), (810, 404)], fill=(255, 255, 255, 70), width=9 if simple else 7)
    if not simple:
        for i, c in enumerate(((255, 138, 138), (255, 211, 122), (140, 232, 178))):
            x = 244 + i * 52
            d.ellipse([x, 350, x + 24, 374], fill=c + (235,))

    # ">" prompt + cursor block
    lw = 44 if simple else 36
    d.line([(278, 490), (372, 562), (278, 634)], fill=(255, 255, 255, 250),
           width=lw, joint="curve")
    d.rounded_rectangle([412, 596, 528, 638], radius=12, fill=(122, 226, 255, 245))

    # AI sparkle over the top-right corner
    if simple:
        star(d, 782, 246, 158, 48, GOLD + (255,))
    else:
        glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse([630, 94, 934, 398], fill=(255, 214, 120, 90))
        base = Image.alpha_composite(base, glow.filter(ImageFilter.GaussianBlur(34)))
        d = ImageDraw.Draw(base)
        star(d, 782, 246, 150, 42, GOLD + (255,), (255, 255, 255, 220), 9)
        star(d, 900, 356, 52, 16, (255, 255, 255, 235))
    return base


def _encode(im):
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _dib(im):
    """Uncompressed 32bpp DIB (BITMAPINFOHEADER + BGRA + AND mask).

    Small icon entries must be DIB: Explorer's shortcut icon path will not
    reliably load a PNG-only ICO for 16-64px entries.
    """
    w, h = im.size
    px = im.convert("RGBA").load()
    xor = bytearray()
    for y in range(h - 1, -1, -1):
        for x in range(w):
            r, g, b, a = px[x, y]
            xor += bytes((b, g, r, a))
    and_mask = bytes(((w + 31) // 32) * 4 * h)
    header = struct.pack("<IiiHHIIiiII", 40, w, 2 * h, 1, 32, 0,
                         len(xor) + len(and_mask), 0, 0, 0, 0)
    return header + bytes(xor) + and_mask


def build_ico():
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    full, small = artwork(False), artwork(True)
    blobs = []
    for n in sizes:
        src = small if n <= 48 else full
        im = src.resize((n, n), Image.LANCZOS).convert("RGBA")
        blobs.append((n, _dib(im) if n <= 64 else _encode(im)))

    out = bytearray(struct.pack("<HHH", 0, 1, len(blobs)))
    offset = 6 + 16 * len(blobs)
    entries = bytearray()
    for n, data in blobs:
        entries += struct.pack("<BBBBHHII",
                               n if n < 256 else 0, n if n < 256 else 0,
                               0, 0, 1, 32, len(data), offset)
        offset += len(data)
    out += entries
    for _, data in blobs:
        out += data
    with open(ICO, "wb") as f:
        f.write(out)
    full.resize((256, 256), Image.LANCZOS).convert("RGBA").save(PNG)
    print("ICO:", ICO, os.path.getsize(ICO), "bytes, sizes:", sizes)
    print("PNG:", PNG)


if __name__ == "__main__":
    os.makedirs(ASSETS, exist_ok=True)
    build_ico()
