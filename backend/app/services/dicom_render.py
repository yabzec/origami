"""Turn DICOM pixel data into 8-bit images for the study PDF and the import preview."""

import io
from pathlib import Path

import numpy as np
import pydicom
from PIL import Image
from pydicom.pixels import apply_modality_lut, apply_voi_lut, get_decoder

MAX_SIDE = 4096
THUMB_SIDE = 256
LOW_PERCENTILE, HIGH_PERCENTILE = 0.5, 99.5


def is_decodable(transfer_syntax: str) -> bool:
    """True when an installed pixel decoder (pydicom, pylibjpeg plugins) handles the syntax."""
    try:
        return get_decoder(transfer_syntax).is_available
    except NotImplementedError:
        return False


def _has_window(ds) -> bool:
    return ("WindowCenter" in ds and "WindowWidth" in ds) or "VOILUTSequence" in ds


def _to_uint8(values: np.ndarray, full_range: bool) -> np.ndarray:
    """Stretch to 0-255: the whole range after a VOI window, else the 0.5-99.5 percentile window."""
    values = values.astype(np.float64)
    if full_range:
        low, high = float(values.min()), float(values.max())
    else:
        low, high = (float(v) for v in np.percentile(values, [LOW_PERCENTILE, HIGH_PERCENTILE]))
    if high <= low:
        return np.zeros(values.shape, dtype=np.uint8)
    return ((np.clip(values, low, high) - low) / (high - low) * 255.0).round().astype(np.uint8)


def _gray(frame: np.ndarray, ds) -> Image.Image:
    values = apply_modality_lut(frame, ds)
    windowed = _has_window(ds)
    if windowed:
        values = apply_voi_lut(values, ds)
    pixels = _to_uint8(values, full_range=windowed)
    if ds.get("PhotometricInterpretation") == "MONOCHROME1":
        pixels = 255 - pixels  # MONOCHROME1: low values are white
    return Image.fromarray(pixels)


def _color(frame: np.ndarray) -> Image.Image:
    # pydicom already converts YBR colour spaces to RGB in pixel_array
    if frame.dtype != np.uint8:
        frame = _to_uint8(frame, full_range=True)
    return Image.fromarray(frame).convert("RGB")


def _cap(image: Image.Image) -> Image.Image:
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return image


def render_dicom(path: Path) -> list[Image.Image]:
    """One 8-bit image per frame (grayscale "L" or "RGB"), at most MAX_SIDE on the long side."""
    ds = pydicom.dcmread(path)
    pixels = ds.pixel_array
    if int(ds.get("NumberOfFrames") or 1) == 1:
        pixels = pixels[np.newaxis, ...]
    color = int(ds.get("SamplesPerPixel") or 1) == 3
    return [_cap(_color(frame) if color else _gray(frame, ds)) for frame in pixels]


def render_image(path: Path) -> list[Image.Image]:
    with Image.open(path) as img:
        image = img.convert("L" if img.mode == "L" else "RGB")
    return [_cap(image)]


def render(path: Path, kind: str) -> list[Image.Image]:
    return render_dicom(path) if kind == "dicom" else render_image(path)


def thumbnail_jpeg(path: Path, kind: str) -> bytes:
    image = render(path, kind)[0]
    image.thumbnail((THUMB_SIDE, THUMB_SIDE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=80)
    return out.getvalue()
