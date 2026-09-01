"""Rasterize frontend/favicon.svg into the PWA PNG icons."""
import os

from svglib.svglib import svg2rlg
from reportlab.graphics import renderPM
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SVG_PATH = os.path.join(HERE, "favicon.svg")
TEAL = "#14B8A6"


def render_rgb(size, bg):
    drawing = svg2rlg(SVG_PATH)
    scale = size / drawing.width
    drawing.width = drawing.height = size
    drawing.scale(scale, scale)
    out = os.path.join(HERE, f"_tmp_{bg:x}.png")
    renderPM.drawToFile(drawing, out, fmt="PNG", bg=bg)
    img = Image.open(out).convert("RGB")
    os.remove(out)
    return img


def render_rgba(size):
    # renderPM cannot emit transparency; recover alpha by rendering the same
    # artwork over black and over white and differencing the channels.
    black = render_rgb(size, 0x000000)
    white = render_rgb(size, 0xFFFFFF)
    b = black.load()
    w = white.load()
    out = Image.new("RGBA", (size, size))
    o = out.load()
    for y in range(size):
        for x in range(size):
            r0, g0, b0 = b[x, y]
            r1, g1, b1 = w[x, y]
            alpha = 255 - (r1 - r0)
            if alpha <= 0:
                o[x, y] = (0, 0, 0, 0)
            else:
                o[x, y] = (
                    round(r0 * 255 / alpha),
                    round(g0 * 255 / alpha),
                    round(b0 * 255 / alpha),
                    alpha,
                )
    return out


def main():
    logo_512 = render_rgba(512)
    logo_512.save(os.path.join(HERE, "icon-512.png"))
    logo_512.resize((192, 192), Image.LANCZOS).save(
        os.path.join(HERE, "icon-192.png")
    )

    # Maskable: full-bleed teal background, logo shrunk into the safe zone.
    canvas = Image.new("RGBA", (512, 512), TEAL)
    small = logo_512.resize((int(512 * 0.78), int(512 * 0.78)), Image.LANCZOS)
    offset = (512 - small.width) // 2
    canvas.alpha_composite(small, (offset, offset))
    canvas.save(os.path.join(HERE, "icon-maskable-512.png"))

    for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png"):
        p = os.path.join(HERE, name)
        print(name, os.path.getsize(p), "bytes")


if __name__ == "__main__":
    main()
