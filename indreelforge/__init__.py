"""
Deprecated import name kept for code written against the ``indreelforge``
package (the SDK's name before 1.2.0).

Use ``import post_upload`` / ``from post_upload import PostUpload`` instead.
Everything here is the same object as in :mod:`post_upload` (``IndReelForge``
is ``PostUpload``, ``IndReelForgeError`` is ``PostUploadError``), and
``indreelforge.client`` / ``indreelforge.types`` are the ``post_upload`` modules.
"""

import sys as _sys
import warnings as _warnings

_warnings.warn(
    "The 'indreelforge' module is deprecated; the SDK is now called Post-Upload. "
    "Use 'from post_upload import PostUpload' (pip install post-upload).",
    DeprecationWarning,
    stacklevel=2,
)

import post_upload as _post_upload  # noqa: E402
from post_upload import *  # noqa: E402,F401,F403
from post_upload import __all__, __version__  # noqa: E402,F401
from post_upload import _version, client, types  # noqa: E402,F401

# ``import indreelforge.client`` / ``from indreelforge.client import ...`` keep
# working and resolve to the very same module objects.
for _name in ("client", "types", "_version"):
    _sys.modules[f"{__name__}.{_name}"] = getattr(_post_upload, _name)
del _name
