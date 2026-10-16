"""Compose a full-resolution photograph with a quiet camera information border."""
from functools import lru_cache
from io import BytesIO
import os
from pathlib import Path
import re

from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps


PREVIEW_WIDTH = 1600
INK = (28, 29, 31)
SECONDARY = (107, 109, 113)


def framed_border_height(width: int) -> int:
    """Keep still exports and slideshow background extraction in agreement."""
    return max(1, round(width * .09))


def _clean(value) -> str:
    # EXIF may contain newlines, null padding, or very long arbitrary strings.
    return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).split())[:500]


def camera_identity(camera: str) -> tuple[str, str, str | None]:
    """Return a brand, its actual model, and an optional bundled wordmark."""
    camera = _clean(camera)
    ricoh = re.match(r"^ricoh imaging company,?\s*ltd\.?\s*", camera, re.IGNORECASE)
    if ricoh:
        model = camera[ricoh.end():].strip(" -,")
        if re.match(r"^pentax\b", model, re.IGNORECASE):
            return "PENTAX", re.sub(r"^pentax\s*", "", model, flags=re.IGNORECASE), None
        if re.match(r"^(?:ricoh\b|gr\b)", model, re.IGNORECASE):
            return "RICOH", re.sub(r"^ricoh\s*", "", model, flags=re.IGNORECASE), None
        return "RICOH IMAGING", model, None
    brands = (
        (r"^(?:panasonic|lumix)\b", "LUMIX", "lumix.png"),
        (r"^canon\b", "Canon", None),
        (r"^nikon(?: corporation)?\b", "Nikon", None),
        (r"^sony\b", "SONY", None),
        (r"^fuji(?:film)?\b", "FUJIFILM", None),
        (r"^leica(?: camera ag)?\b", "Leica", None),
        (r"^(?:om digital solutions|om system)\b", "OM SYSTEM", None),
        (r"^olympus(?: imaging corp\.?)?\b", "OLYMPUS", None),
        (r"^pentax\b", "PENTAX", None),
        (r"^ricoh\b", "RICOH", None),
        (r"^hasselblad\b", "HASSELBLAD", None),
        (r"^apple\b", "Apple", None),
        (r"^dji\b", "DJI", None),
    )
    for pattern, brand, asset in brands:
        match = re.match(pattern, camera, re.IGNORECASE)
        if match:
            model = camera[match.end():].strip(" -,")
            # Some manufacturers repeat their name in Model (e.g. NIKON Z 6).
            model = re.sub(r"^" + re.escape(brand) + r"\s+", "", model, flags=re.IGNORECASE)
            return brand, model, asset
    return "", camera, None


@lru_cache(maxsize=64)
def _font(size: int, bold: bool = False, cjk: bool = False):
    size = max(1, int(size))
    font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    names = (["msyhbd.ttc" if bold else "msyh.ttc"] if cjk else [])
    names += ["arialbd.ttf" if bold else "arial.ttf", "seguisb.ttf" if bold else "segoeui.ttf"]
    # Windows system fonts travel with the OS, not with the migration bundle.
    for candidate in [*(font_dir / name for name in names), Path("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")]:
        try:
            return ImageFont.truetype(str(candidate), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_text(text: str, size: int, maximum_width: int, bold: bool = False):
    """Fit real metadata into its own column, never into its neighbour."""
    cjk = any(ord(char) > 0x2ff for char in text)
    minimum = max(1, round(size * .65))
    for current in range(max(1, size), minimum - 1, -1):
        font = _font(current, bold, cjk)
        if font.getlength(text) <= maximum_width:
            return text, font
    font = _font(minimum, bold, cjk)
    while text and font.getlength(text + "…") > maximum_width:
        text = text[:-1]
    return (text.rstrip() + "…") if text else "", font


def _text_height(text: str, font) -> int:
    box = font.getbbox(text)
    return box[3] - box[1] if text else 0


def _draw_text(draw, text: str, font, x: int, y: int, fill, right=False):
    if not text:
        return
    box = font.getbbox(text)
    width = font.getlength(text)
    draw.text((x - (width if right else 0) - box[0], y - box[1]), text, font=font, fill=fill)


def _to_srgb(image: Image.Image, profile: bytes | None) -> Image.Image:
    """Normalize tagged photos before composing the white sRGB border."""
    alpha = image.convert("RGBA").getchannel("A") if "A" in image.getbands() or "transparency" in image.info else None
    color = image if image.mode in ("RGB", "CMYK", "LAB", "L") else image.convert("RGB")
    if profile:
        try:
            color = ImageCms.profileToProfile(color, ImageCms.ImageCmsProfile(BytesIO(profile)), ImageCms.createProfile("sRGB"), outputMode="RGB")
        except (OSError, ValueError, TypeError, ImageCms.PyCMSError):
            color = color.convert("RGB")
    else:
        color = color.convert("RGB")
    if alpha is not None:
        opaque = Image.new("RGB", color.size, "white")
        opaque.paste(color, (0, 0), alpha)
        return opaque
    return color


def _draw_border(canvas: Image.Image, photo: dict, assets_dir: Path, image_height: int):
    width = canvas.width
    bar_height = canvas.height - image_height
    pad = max(1, round(width * .036))
    draw = ImageDraw.Draw(canvas)
    brand, model, asset = camera_identity(photo.get("camera", ""))
    parameters = "   ".join(value for value in (
        _clean(photo.get("focal_length")), _clean(photo.get("aperture")),
        _clean(photo.get("shutter")), "ISO " + _clean(photo.get("iso")) if _clean(photo.get("iso")) else "",
    ) if value)
    lens = _clean(photo.get("lens"))
    center_y = image_height + bar_height / 2
    gap = max(1, round(width * .008))
    left_width = max(1, round(width * .29))
    right_width = max(1, width - pad * 2 - left_width - round(width * .045))

    logo = None
    if asset:
        try:
            with Image.open(assets_dir / "camera-brands" / asset) as source:
                logo = source.convert("RGBA")
            bounds = logo.getchannel("A").getbbox()
            if bounds:
                logo = logo.crop(bounds)
                logo.thumbnail((max(1, round(width * .175)), max(1, round(width * .036))), Image.Resampling.LANCZOS)
            else:
                logo = None
        except (OSError, ValueError):
            logo = None
    brand_text, brand_font = _fit_text(brand, max(1, round(width * .033)), left_width, bold=True)
    model_text, model_font = _fit_text(model, max(1, round(width * .019)), left_width)
    main_height = logo.height if logo else _text_height(brand_text, brand_font)
    model_height = _text_height(model_text, model_font)
    left_height = main_height + model_height + (gap if main_height and model_height else 0)
    y = round(center_y - left_height / 2)
    if logo:
        canvas.paste(logo, (pad, y), logo)
    else:
        _draw_text(draw, brand_text, brand_font, pad, y, INK)
    y += main_height + (gap if main_height and model_height else 0)
    _draw_text(draw, model_text, model_font, pad, y, SECONDARY if main_height else INK)

    params_text, params_font = _fit_text(parameters, max(1, round(width * .024)), right_width, bold=True)
    lens_text, lens_font = _fit_text(lens, max(1, round(width * .0175)), right_width)
    params_height = _text_height(params_text, params_font)
    lens_height = _text_height(lens_text, lens_font)
    right_height = params_height + lens_height + (gap if params_height and lens_height else 0)
    y = round(center_y - right_height / 2)
    _draw_text(draw, params_text, params_font, width - pad, y, INK, right=True)
    y += params_height + (gap if params_height and lens_height else 0)
    _draw_text(draw, lens_text, lens_font, width - pad, y, SECONDARY, right=True)


def render_framed_photo(original_path: Path, photo_dict: dict, assets_dir: Path, preview: bool = False,
                        *, max_width: int | None = None) -> bytes:
    """Export the first, correctly oriented frame; never modify the source file."""
    with Image.open(original_path) as source:
        source.seek(0)
        if source.width * source.height > 80_000_000:
            raise ValueError("The photo exceeds the album's 80 megapixel limit")
        profile = source.info.get("icc_profile")
        upright = ImageOps.exif_transpose(source)
        # Preview and final composition use the same width-relative geometry.
        limit = max_width if max_width is not None else (PREVIEW_WIDTH if preview else None)
        if limit is not None and upright.width > limit:
            upright = upright.resize((limit, max(1, round(upright.height * limit / upright.width))), Image.Resampling.LANCZOS)
        photo = _to_srgb(upright, profile)
    width, height = photo.size
    border_height = framed_border_height(width)
    canvas = Image.new("RGB", (width, height + border_height), "white")
    canvas.paste(photo, (0, 0))
    photo.close()
    _draw_border(canvas, photo_dict, Path(assets_dir), height)
    output = BytesIO()
    canvas.save(output, "JPEG", quality=92 if preview else 97, subsampling=0, optimize=True,
                icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    canvas.close()
    # Deliberately omit source EXIF: orientation has already been applied, and
    # visible camera settings are enough; no GPS or local file paths are copied.
    return output.getvalue()
