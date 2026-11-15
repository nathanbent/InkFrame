"""Photo helpers shared by the slideshow and picture-of-the-day modes."""
from PIL import Image, ImageEnhance, ImageOps

from .display import PANEL_RGB, PURE_RGB, _flat


def prepare(im, size, fit="crop", colour_boost=1.0, contrast_boost=1.0):
    """Orient, resize and colour-boost a photo for the panel. Returns RGB.

    fit: "crop" fills the area, "fit" shows the whole photo with white bars,
         "auto" crops unless that would cut off a lot (panoramas, tall portraits).
    """
    im = ImageOps.exif_transpose(im)
    im.draft("RGB", (size[0] * 2, size[1] * 2))   # fast JPEG downscale on the Zero
    im = im.convert("RGB")
    if fit == "auto":
        ratio = (im.width / im.height) / (size[0] / size[1])
        fit = "crop" if 0.7 <= ratio <= 1.45 else "fit"
    if fit == "fit":
        im = ImageOps.pad(im, size, method=Image.Resampling.LANCZOS, color=(255, 255, 255))
    else:
        im = ImageOps.fit(im, size, method=Image.Resampling.LANCZOS)
    if colour_boost != 1.0:
        im = ImageEnhance.Color(im).enhance(colour_boost)
    if contrast_boost != 1.0:
        im = ImageEnhance.Contrast(im).enhance(contrast_boost)
    return im


def dither(im, saturation):
    """Dither an RGB image to panel colour indices, the same way the Inky driver does.

    Use this when you want to draw crisp text or shapes on top of a photo afterwards.
    """
    s = saturation
    blend = [tuple(int(p * s + d * (1 - s)) for p, d in zip(pure, panel))
             for pure, panel in zip(PURE_RGB[:7], PANEL_RGB[:7])]
    pal = Image.new("P", (1, 1))
    pal.putpalette(_flat(blend))
    out = im.convert("RGB").quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG)
    out.putpalette(_flat(PANEL_RGB))
    return out
