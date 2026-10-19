import importlib
import sys
from pathlib import Path
from types import ModuleType

import ee
from gee_acolite.correction import ACOLITE

from src.pipeline import AcoliteConfig


def transform(image: "ee.Image", config: AcoliteConfig) -> "ee.Image":
    """Correct one L1C image with ACOLITE and return the corrected image."""
    acolite = _import_acolite(config.path)
    # ACOLITE adds keys to the settings it receives: give it a copy so
    # `config.params` (and therefore `config.hash()`) never changes.
    corrector = ACOLITE(acolite, dict(config.params))
    corrected, _settings = corrector.correct(ee.ImageCollection.fromImages([image]))
    return corrected.first()


def _import_acolite(path: Path) -> ModuleType:
    """ACOLITE is not installable: it is used from its own folder."""
    folder = str(path)
    if folder not in sys.path:
        sys.path.append(folder)
    return importlib.import_module("acolite")
