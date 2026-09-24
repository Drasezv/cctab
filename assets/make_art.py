#!/usr/bin/env python3
"""rebuild the banner, icon and social card from assets/src"""
import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
DOCS = os.path.join(os.path.dirname(HERE), "docs")
DOT = (24, 16, 60, 52)      # status emoji inside the cropped bubble
SS = 3                      # supersampling, text stays crisp after the downscale
BONE = (242, 239, 233)
MUTED = (176, 168, 158)
CORAL = (217, 119, 87)


def font(name, size, ss=None, weight=None):
    f = ImageFont.truetype(f"/System/Library/Fonts/{name}", size * (ss or SS))
    if weight:
        f.set_variation_by_name(weight)
    return f


def tracked(draw, xy, text, fnt, fill, tracking=0, ss=None):
    """draw text letter by letter so the spacing is ours, not the font's"""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += draw.textlength(ch, font=fnt) + tracking * (ss or SS)
    return x


def cover(img, w, h, anchor="right"):
    scale = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left = img.width - w if anchor == "right" else (img.width - w) // 2
    top = (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def shade(img, width, strength=225):
    grad = Image.new("L", (img.width, 1), 0)
    px = grad.load()
    for x in range(img.width):
        px[x, 0] = int(strength * max(0.0, 1 - x / width) ** 1.3)
    black = Image.new("RGB", img.size, (10, 9, 8))
    return Image.composite(black, img, grad.resize(img.size))


def banner(scale=2):
    """rendered at 2x so it stays sharp on retina and in the readme"""
    SS = 4
    W, H = 1280 * SS, 640 * SS
    img = shade(cover(Image.open(f"{SRC}/banner-bg.png").convert("RGB"), W, H), 900 * SS)
    d = ImageDraw.Draw(img)
    # everything sits low and clear of the laptop on the right
    edge = 92 * SS                       # one optical margin for every line
    wm = font("SFNS.ttf", 116, SS)
    tracked(d, (edge - wm.getbbox("c")[0] - round(2.2 * SS), 250 * SS),
            "cctab", wm, BONE, tracking=-4, ss=SS)
    body = font("SFNS.ttf", 27, SS)
    for i, line in enumerate(("Telegram push when a long Claude Code",
                              "task finishes, with its cost and limit used.")):
        lean = round(2.5 * SS) if line[0].islower() else 0
        d.text((edge - body.getbbox(line)[0] + lean, (418 + i * 40) * SS), line, font=body, fill=MUTED)
    mono = font("SFNSMono.ttf", 24, SS)
    d.rectangle([edge, 540 * SS, edge + 3 * SS, 572 * SS], fill=CORAL)
    d.text((edge + 22 * SS - mono.getbbox("g")[0], 543 * SS),
           "github.com/Drasezv/cctab", font=mono, fill=BONE)
    return img.resize((1280 * scale, 640 * scale), Image.LANCZOS)


def icon():
    """drop the generator's white corners, keep the squircle on alpha"""
    raw = Image.open(f"{SRC}/icon-raw.png").convert("RGB")
    side = min(raw.size)
    raw = raw.crop(((raw.width - side) // 2, (raw.height - side) // 2,
                    (raw.width + side) // 2, (raw.height + side) // 2))
    size = 512 * SS
    raw = raw.resize((size, size), Image.LANCZOS)
    inset = round(size * 0.012)                     # shave the soft white rim
    raw = raw.crop((inset, inset, size - inset, size - inset)).resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1],
                                           radius=round(size * 0.225), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(raw, (0, 0), mask)
    return out.resize((512, 512), Image.LANCZOS)


def duotone(img, dark=(17, 16, 16), light=(238, 234, 228)):
    """flatten telegram's green and purple into one warm range, keeping depth"""
    grey = img.convert("L")
    ramp = []
    for i in range(256):
        t = (i / 255) ** 1.45          # hold the dark end down, let type stay bright
        ramp.append(tuple(round(dark[c] + (light[c] - dark[c]) * t) for c in range(3)))
    out = Image.new("RGB", img.size)
    out.putdata([ramp[v] for v in grey.getdata()])
    return out


def social():
    W, H = 1600, 900
    big = (W * SS, H * SS)
    card = cover(Image.open(f"{SRC}/social-bg.png").convert("RGB"), *big, anchor="center")
    card = Image.blend(card, Image.new("RGB", big, (13, 12, 12)), 0.45)

    # the bubble only: no chat background, no timestamp, a breath under the quote
    shot = Image.open(f"{DOCS}/task-done.png").convert("RGB").crop((6, 4, 832, 543))
    pad = Image.new("RGB", (shot.width, 22), shot.getpixel((shot.width - 6, shot.height - 4)))
    grown = Image.new("RGB", (shot.width, shot.height + pad.height))
    grown.paste(shot, (0, 0)); grown.paste(pad, (0, shot.height))
    shot = grown
    circle = shot.crop(DOT)
    shot = duotone(shot)
    ring = Image.new("L", (DOT[2] - DOT[0], DOT[3] - DOT[1]), 0)
    ImageDraw.Draw(ring).ellipse([2, 2, ring.width - 3, ring.height - 3], fill=128)
    shot.paste(Image.composite(circle, shot.crop(DOT), ring), DOT[:2])

    x = (W - shot.width) // 2
    y = (H - shot.height) // 2 + 10
    text_x = x + 22                      # line up with the type inside the bubble

    glow = Image.new("RGBA", big, (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle(
        [(x - 16) * SS, (y - 16) * SS, (x + shot.width + 16) * SS, (y + shot.height + 16) * SS],
        radius=40 * SS, fill=(214, 128, 92, 70))
    card = Image.alpha_composite(card.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(46 * SS)))

    shadow = Image.new("RGBA", big, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [x * SS, (y + 18) * SS, (x + shot.width) * SS, (y + shot.height + 34) * SS],
        radius=30 * SS, fill=(0, 0, 0, 210))
    card = Image.alpha_composite(card, shadow.filter(ImageFilter.GaussianBlur(34 * SS)))
    card = card.convert("RGB").resize((W, H), Image.LANCZOS)

    mask = Image.new("L", shot.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, shot.width - 1, shot.height - 1], radius=26, fill=255)
    card.paste(shot, (x, y), mask)

    # all type on one oversampled layer, so nothing is drawn at final size
    layer = Image.new("RGBA", big, (0, 0, 0, 0))
    t = ImageDraw.Draw(layer)
    wm = font("SFNS.ttf", 62, weight="Medium")
    sub = font("SFNS.ttf", 26)
    base = (y - 30) * SS                              # shared baseline
    end_x = tracked(t, (x * SS - wm.getbbox("c")[0], base - wm.getbbox("cctab")[3]),
                    "cctab", wm, BONE, tracking=-3)
    t.text((end_x + 20 * SS, base - sub.getbbox("one")[3]),
           "one message per finished task", font=sub, fill=MUTED)

    foot = font("SFNSMono.ttf", 24)
    fy = (y + shot.height + 46) * SS
    t.rectangle([x * SS, fy, (x + 3) * SS, fy + 32 * SS], fill=CORAL)
    t.text(((x + 22) * SS - foot.getbbox("g")[0], fy + 3 * SS), "github.com/Drasezv/cctab",
           font=foot, fill=BONE)

    card = Image.alpha_composite(card.convert("RGBA"),
                                 layer.resize((W, H), Image.LANCZOS)).convert("RGB")
    return card


if __name__ == "__main__":
    for name, im in (("banner.png", banner()), ("icon.png", icon()), ("social-card.png", social())):
        path = os.path.join(HERE, name)
        im.save(path)
        print(name, im.size, f"{os.path.getsize(path) // 1024}kb")
