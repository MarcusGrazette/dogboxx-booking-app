"""
Regression tests for app/utils/uploads.py (audit M16): Pillow only *errors*
above 2x its default MAX_IMAGE_PIXELS and merely warns in between, which
would otherwise admit a huge decode from a small compressed upload. We cap
MAX_IMAGE_PIXELS at import time so oversized images raise instead of OOMing
the process.
"""
import io

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app.utils import uploads as uploads_mod


def _fake_upload(width=64, height=64, fmt='PNG'):
    img = Image.new('RGB', (width, height), color='red')
    buf = io.BytesIO()
    img.save(buf, fmt)
    buf.seek(0)
    return FileStorage(stream=buf, filename=f'photo.{fmt.lower()}',
                        content_type=f'image/{fmt.lower()}')


def test_max_image_pixels_is_capped():
    """Guard against silently reverting to Pillow's much larger default
    (~89M px) or None (unlimited) — both leave the decompression-bomb
    window open."""
    assert Image.MAX_IMAGE_PIXELS == 50_000_000


def test_process_dog_photo_rejects_image_over_the_pixel_cap(app, monkeypatch):
    """With the cap lowered to something a small real image still exceeds,
    process_dog_photo must reject it with a clean ValueError (via the
    existing broad except in the verify step) rather than decoding it."""
    monkeypatch.setattr(uploads_mod.Image, "MAX_IMAGE_PIXELS", 100)

    upload = _fake_upload(64, 64, 'PNG')  # 4096px > our lowered 100px cap

    with app.app_context():
        with pytest.raises(ValueError, match="Invalid image file"):
            uploads_mod.process_dog_photo(upload)


def test_process_dog_photo_accepts_image_under_the_pixel_cap(app):
    """Sanity check: an ordinary small photo is unaffected by the real cap."""
    upload = _fake_upload(64, 64, 'PNG')

    with app.app_context():
        filename = uploads_mod.process_dog_photo(upload)

    assert filename is not None and filename.endswith('.png')


def _disguised_upload(real_fmt, filename):
    """A real `real_fmt` image whose filename claims an allowed extension."""
    buf = io.BytesIO()
    Image.new('RGB', (16, 16), color='blue').save(buf, real_fmt)
    buf.seek(0)
    return FileStorage(stream=buf, filename=filename, content_type='image/jpeg')


@pytest.mark.parametrize("real_fmt,filename", [
    ('TIFF', 'photo.jpg'),
    ('BMP', 'photo.png'),
])
@pytest.mark.parametrize("processor", ['process_dog_photo', 'process_cropped_photo'])
def test_upload_rejects_disallowed_format_behind_allowed_extension(
        app, processor, real_fmt, filename):
    """The extension check reads the filename, so Image.open() must itself
    refuse non-JPEG/PNG/GIF content — otherwise a renamed TIFF/BMP reaches
    decoders we never meant to expose (Pillow 13.0.0 advisory batch)."""
    with app.app_context():
        with pytest.raises(ValueError, match="Invalid image file"):
            getattr(uploads_mod, processor)(_disguised_upload(real_fmt, filename))


@pytest.mark.parametrize("fmt", ['JPEG', 'PNG', 'GIF'])
def test_process_cropped_photo_accepts_allowed_formats(app, fmt):
    upload = _fake_upload(32, 32, fmt)
    with app.app_context():
        filename = uploads_mod.process_cropped_photo(upload)
    assert filename is not None and filename.endswith('.jpg')
