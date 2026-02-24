"""Shared image upload processing utilities."""

import logging
import os
import uuid
from pathlib import Path

from flask import current_app
from PIL import Image
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif'}
MAX_SIZE = (800, 800)


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
    return unique_filename
