"""Shared image upload processing utilities."""

import base64
import io
import logging
import os
import uuid
from pathlib import Path

from flask import current_app
from PIL import Image
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif'}
MAX_SIZE = (800, 800)
CROPPED_SIZE = (400, 400)  # square output for cropper uploads


def _backup_to_r2(local_path, r2_key):
    """Copy a saved file to the R2 backup bucket. Best-effort — never raises."""
    try:
        import boto3
        from botocore.client import Config

        endpoint = os.environ.get('R2_ENDPOINT_URL')
        key_id   = os.environ.get('R2_ACCESS_KEY_ID')
        secret   = os.environ.get('R2_SECRET_ACCESS_KEY')
        bucket   = os.environ.get('R2_BUCKET_UPLOADS', 'dogboxx-uploads-backup')

        if not all([endpoint, key_id, secret]):
            return  # not configured (local dev / CI)

        client = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
            config=Config(signature_version='s3v4'),
            region_name='auto',
        )
        client.upload_file(local_path, bucket, r2_key)
        logging.info(f"R2 backup: {r2_key}")
    except Exception as e:
        logging.warning(f"R2 backup failed for {r2_key}: {e}")


def process_dog_photo(file_storage):
    """Process and save an uploaded dog photo.

    Validates the image, strips EXIF metadata, resizes, and saves
    with a UUID filename.

    Args:
        file_storage: A werkzeug FileStorage object from request.files

    Returns:
        The saved filename (e.g. 'abc123.jpg'), or None if no file provided.

    Raises:
        ValueError: If the file is invalid or unsupported format.
    """
    if not file_storage or not file_storage.filename:
        return None

    # 1. Verify it's a valid image
    try:
        img = Image.open(file_storage)
        img.verify()
        file_storage.seek(0)
    except Exception:
        raise ValueError("Invalid image file")

    # 2. Check allowed extensions
    file_extension = Path(secure_filename(file_storage.filename)).suffix.lower()
    if file_extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Unsupported file format. Use JPG, PNG, or GIF.")

    # 3. Re-open and strip metadata by copying pixel data to a fresh image
    img = Image.open(file_storage)
    clean_img = Image.new(img.mode, img.size)
    clean_img.putdata(list(img.getdata()))

    # 4. Resize
    clean_img.thumbnail(MAX_SIZE, Image.LANCZOS)

    # 5. Save with a UUID filename
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    upload_path = os.path.join(current_app.config["UPLOAD_FOLDER"], unique_filename)

    format_map = {
        '.png': ('PNG', {}),
        '.jpg': ('JPEG', {'quality': 85}),
        '.jpeg': ('JPEG', {'quality': 85}),
        '.gif': ('GIF', {}),
    }
    fmt, kwargs = format_map[file_extension]
    clean_img.save(upload_path, fmt, **kwargs)

    logging.info(f"Saved dog photo: {unique_filename}")
    _backup_to_r2(upload_path, f"dogs/{unique_filename}")
    return unique_filename


def process_cropped_photo(file_storage, subfolder='dogs'):
    """Process and save a pre-cropped photo from a Cropper.js canvas blob.

    The canvas always outputs a square JPEG blob with no EXIF metadata.
    This function resizes to CROPPED_SIZE, converts RGBA→RGB if needed,
    and saves as JPEG.

    Args:
        file_storage: A werkzeug FileStorage object (blob from canvas.toBlob).
        subfolder:    Upload sub-directory under static/uploads/ (default 'dogs').
                      Use 'profiles' for user profile photos.

    Returns:
        The saved filename (e.g. 'abc123.jpg'), or None if no file provided.

    Raises:
        ValueError: If the file is not a valid image.
    """
    if not file_storage:
        return None

    # Verify it's a real image
    try:
        img = Image.open(file_storage)
        img.verify()
        file_storage.seek(0)
    except Exception:
        raise ValueError("Invalid image file")

    # Re-open (verify() closes the file)
    img = Image.open(file_storage)

    # Canvas may output RGBA — flatten onto a white background before saving JPEG
    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
        background = Image.new('RGB', img.size, (27, 27, 27))  # #1B1B1B to match app theme
        background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')

    # Resize to square output size
    img = img.resize(CROPPED_SIZE, Image.LANCZOS)

    # Resolve the target directory from UPLOAD_FOLDER base + subfolder
    base_dir = os.path.dirname(current_app.config["UPLOAD_FOLDER"])
    target_dir = os.path.join(base_dir, subfolder)
    os.makedirs(target_dir, exist_ok=True)

    unique_filename = f"{uuid.uuid4()}.jpg"
    upload_path = os.path.join(target_dir, unique_filename)
    img.save(upload_path, 'JPEG', quality=88, optimize=True)

    logging.info(f"Saved cropped photo ({subfolder}): {unique_filename}")
    _backup_to_r2(upload_path, f"{subfolder}/{unique_filename}")
    return unique_filename
