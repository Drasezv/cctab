#!/usr/bin/env python3
"""rebuild the banner, icon and social card from assets/src"""
import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
DOCS = os.path.join(os.path.dirname(HERE), "docs")
SS = 3                      # supersampling, text stays crisp after the downscale
BONE = (242, 239, 233)
MUTED = (176, 168, 158)
CORAL = (217, 119, 87)


def font(name, size):
    return ImageFont.truetype(f"/System/Library/Fonts/{name}", size * SS)


def tracked(draw, xy, text, fnt, fill, tracking=0):
    """draw text letter by letter so the spacing is ours, not the font's"""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += draw.textlength(ch, font=fnt) + tracking * SS
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


def banner():
    W, H = 1280 * SS, 640 * SS
    img = shade(cover(Image.open(f"{SRC}/banner-bg.png").convert("RGB"), W, H), 900 * SS)
    d = ImageDraw.Draw(img)
    # everything sits low and clear of the laptop on the right
    tracked(d, (86 * SS, 250 * SS), "cctab", font("SFNS.ttf", 116), BONE, tracking=-4)
    body = font("SFNS.ttf", 27)
    for i, line in enumerate(("Telegram push when a long Claude Code",
                              "task finishes, with its cost and limit used.")):
        d.text((92 * SS, (418 + i * 40) * SS), line, font=body, fill=MUTED)
    d.rectangle([92 * SS, 540 * SS, 95 * SS, 572 * SS], fill=CORAL)
    d.text((114 * SS, 543 * SS), "github.com/Drasezv/cctab", font=font("SFNSMono.ttf", 24), fill=BONE)
    return img.resize((1280, 640), Image.LANCZOS)


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


def social():
    W, H = 1600 * SS, 900 * SS
    card = cover(Image.open(f"{SRC}/social-bg.png").convert("RGB"), W, H, anchor="center")
    card = Image.blend(card, Image.new("RGB", card.size, (14, 12, 12)), 0.35)

    shot = Image.open(f"{DOCS}/task-done.png").convert("RGB")
    shot = shot.crop(shot.getbbox() or (0, 0, *shot.size))
    bg = Image.new("RGB", shot.size, shot.getpixel((2, 2)))
    from PIL import ImageChops
    box = ImageChops.difference(shot, bg).convert("L").point(lambda v: 255 if v > 10 else 0).getbbox()
    shot = shot.crop(box)                            # only the message, no window padding

    target = round(W * 0.55)
    shot = shot.resize((target, round(shot.height * target / shot.width)), Image.LANCZOS)

    x = (W - shot.width) // 2
    y = (H - shot.height) // 2 + round(28 * SS)
    glow = Image.new("RGBA", card.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).rectangle(
        [x - 10 * SS, y - 10 * SS, x + shot.width + 10 * SS, y + shot.height + 10 * SS],
        fill=(217, 119, 87, 60))
    card = Image.alpha_composite(card.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(40 * SS)))

    shadow = Image.new("RGBA", card.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle(
        [x, y + 16 * SS, x + shot.width, y + shot.height + 30 * SS], fill=(0, 0, 0, 205))
    card = Image.alpha_composite(card, shadow.filter(ImageFilter.GaussianBlur(30 * SS))).convert("RGB")
    card.paste(shot, (x, y))

    d = ImageDraw.Draw(card)
    d.rectangle([x, y - 2 * SS, x + shot.width, y], fill=(52, 60, 74))   # a clean top edge
    tracked(d, (x, y - 108 * SS), "cctab", font("SFNS.ttf", 52), BONE, tracking=-2)
    d.text((x + 168 * SS, y - 90 * SS), "one message per finished task",
           font=font("SFNS.ttf", 25), fill=MUTED)
    return card.resize((1600, 900), Image.LANCZOS)


if __name__ == "__main__":
    for name, im in (("banner.png", banner()), ("icon.png", icon()), ("social-card.png", social())):
        path = os.path.join(HERE, name)
        im.save(path)
        print(name, im.size, f"{os.path.getsize(path) // 1024}kb")
