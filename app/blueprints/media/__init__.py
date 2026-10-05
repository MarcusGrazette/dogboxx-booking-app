from flask import Blueprint

media_bp = Blueprint('media', __name__, url_prefix='/media')

from . import routes  # noqa
