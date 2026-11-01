"""
environment.py — Datos del entorno de ejecución para el manifiesto.

Por qué:
    PyTorch no garantiza resultados idénticos entre versiones, commits o
    plataformas, ni entre backends de atención; ComfyUI tampoco los garantiza
    ni siquiera con --deterministic. Para saber si una re-ejecución puede ser
    bit a bit idéntica hay que registrar con qué software, hardware y flags
    se hizo la original.

Qué se recoge (lo mismo que expone la ruta /system_stats, más el commit):
    - Versión y commit de ComfyUI.
    - Versiones de Python, PyTorch, CUDA y cuDNN.
    - Dispositivo de cómputo.
    - Argumentos de arranque (sys.argv): precisión, atención, --fast, etc.

Cada dato se recoge por separado dentro de un try: si uno falla (por
ejemplo, en una máquina sin GPU) el resto se guarda igualmente.

Se calcula una vez por proceso y se guarda en memoria, porque no cambia
mientras ComfyUI esté en marcha.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from functools import lru_cache
from typing import Any, Callable

import folder_paths  # módulo de ComfyUI

from .config import EXTENSION_VERSION
from .references import read_git_revision

logger = logging.getLogger("NIL_ComfyUI-Workfile_traceability")


def _safe(getter: Callable[[], Any]) -> Any:
    """Ejecuta `getter` y devuelve None (registrando el motivo) si falla."""
    try:
        return getter()
    except Exception as exc:
        logger.debug("[Workfile] Dato de entorno no disponible: %s", exc)
        return None


def _comfyui_version() -> str | None:
    import comfyui_version  # archivo comfyui_version.py en la raíz de ComfyUI
    return comfyui_version.__version__


def _comfyui_root() -> str:
    # folder_paths.py está en la raíz del repositorio de ComfyUI.
    return os.path.dirname(os.path.realpath(folder_paths.__file__))


def _torch_info() -> dict[str, Any]:
    import torch
    cudnn = torch.backends.cudnn.version() if torch.backends.cudnn.is_available() else None
    return {"torch": torch.__version__, "cuda": torch.version.cuda, "cudnn": cudnn}


def _device_name() -> str:
    import comfy.model_management as mm
    return mm.get_torch_device_name(mm.get_torch_device())


@lru_cache(maxsize=1)
def collect_environment() -> dict[str, Any]:
    """Diccionario con el entorno. Se calcula una sola vez por proceso."""
    return {
        "comfyui_version": _safe(_comfyui_version),
        "comfyui_git": _safe(lambda: read_git_revision(_comfyui_root())),
        "python": sys.version,
        "platform": platform.platform(),
        "libraries": _safe(_torch_info),
        "device": _safe(_device_name),
        # Los flags de arranque determinan precisión, atención y caché.
        "argv": list(sys.argv),
        "workfile_extension_version": EXTENSION_VERSION,
    }
