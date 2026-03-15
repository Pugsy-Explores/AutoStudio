"""Editing and patch configuration."""

import os


MAX_PATCH_SIZE = int(os.getenv("MAX_PATCH_SIZE", "200"))
MAX_FILES_EDITED = int(os.getenv("MAX_FILES_EDITED", "5"))
