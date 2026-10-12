from PIL import Image

from app.services.dicom_render import (
    MAX_SIDE,
    THUMB_SIDE,
    is_decodable,
    render,
    render_dicom,
    render_image,
    thumbnail_jpeg,
)
from tests.helpers import make_dicom


def test_monochrome2_without_window_uses_full_gradient(tmp_path):
    [image] = render_dicom(make_dicom(tmp_path / "a"))
    assert image.mode == "L"
    assert image.size == (48, 64)
    assert image.getpixel((0, 0)) == 0
    assert image.getpixel((47, 0)) == 255


def test_monochrome1_is_inverted(tmp_path):
    [image] = render_dicom(make_dicom(tmp_path / "a", photometric="MONOCHROME1"))
    assert image.getpixel((0, 0)) == 255
    assert image.getpixel((47, 0)) == 0


def test_window_from_file_is_applied(tmp_path):
    [image] = render_dicom(make_dicom(tmp_path / "a", window=(1000, 200)))
    assert image.getpixel((5, 0)) == 0  # value ~436: below the window
    assert image.getpixel((30, 0)) == 255  # value ~2614: above the window
    assert 0 < image.getpixel((11, 0)) < 255  # value ~958: inside the window


def test_every_frame_becomes_an_image(tmp_path):
    assert len(render_dicom(make_dicom(tmp_path / "a", frames=3))) == 3


def test_color_image_stays_rgb(tmp_path):
    [image] = render_dicom(make_dicom(tmp_path / "a", color=True))
    assert image.mode == "RGB"
    assert image.getpixel((0, 0)) == (200, 0, 0)


def test_long_side_is_capped(tmp_path):
    [image] = render_dicom(make_dicom(tmp_path / "a", rows=5000, cols=10))
    assert max(image.size) == MAX_SIDE


def test_rle_compressed_file_decodes(tmp_path):
    [image] = render_dicom(make_dicom(tmp_path / "a", rle=True))
    assert image.size == (48, 64)


def test_decoder_availability():
    assert is_decodable("1.2.840.10008.1.2.4.70")  # JPEG lossless, used by hospital discs
    assert is_decodable("1.2.840.10008.1.2.4.90")  # JPEG 2000 lossless
    assert not is_decodable("1.2.3.4")


def test_plain_image_and_dispatch(tmp_path):
    path = tmp_path / "copy.jpg"
    Image.new("RGB", (30, 20), "white").save(path)
    [image] = render_image(path)
    assert image.size == (30, 20)
    assert len(render(path, "image")) == 1
    assert len(render(make_dicom(tmp_path / "d"), "dicom")) == 1


def test_thumbnail_is_small_jpeg(tmp_path):
    data = thumbnail_jpeg(make_dicom(tmp_path / "a", rows=1000, cols=800), "dicom")
    assert data[:2] == b"\xff\xd8"
    from io import BytesIO

    assert max(Image.open(BytesIO(data)).size) == THUMB_SIDE
