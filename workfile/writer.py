"""
writer.py — Escritura segura de archivos JSON y de la carpeta _workfile.

Decisiones:
    1. Escritura atómica: se escribe en un archivo temporal dentro de la misma
       carpeta y luego se renombra con os.replace. El renombrado es atómico en
       el mismo sistema de archivos, así que otro proceso (por ejemplo, una
       herramienta del pipeline que vigila la carpeta) nunca ve un JSON a
       medio escribir.

    2. Carpeta en dos fases: los archivos se escriben en una carpeta temporal
       oculta y, cuando están todos, se renombra a "<nombre>_workfile". Así la
       carpeta final o existe completa o no existe.

    3. No sobrescribir por defecto: si la carpeta ya existe, se omite. Esto
       cubre el caso de la caché de ComfyUI: si se reencola un prompt
       idéntico, el nodo de guardado no se re-ejecuta y el historial vuelve a
       declarar el MISMO archivo antiguo, cuyo _workfile ya existe.

Este módulo NO importa nada de ComfyUI.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def atomic_write_json(path: str | os.PathLike, data: Any) -> None:
    """Escribe `data` como JSON en `path` de forma atómica (temporal + rename)."""
    path = Path(path)
    # NamedTemporaryFile en la MISMA carpeta: os.replace solo es atómico
    # dentro del mismo sistema de archivos.
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            # ensure_ascii=False conserva tildes y otros caracteres del prompt;
            # indent=2 lo hace legible por una persona.
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())  # asegura que los datos llegan al disco
        os.replace(tmp_name, path)
    except BaseException:
        # Si algo falla, no dejar temporales huérfanos.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


@dataclass
class WriteResult:
    folder: Path
    status: str          # "written", "skipped_existing"
    files: list[str]


def write_workfile_folder(
    folder: Path,
    files: dict[str, Any],
    overwrite: bool = False,
) -> WriteResult:
    """
    Crea `folder` con un JSON por cada entrada de `files` ({nombre: datos}).

    Los valores None se omiten (por ejemplo, workflow.json cuando no hay
    workflow), en lugar de escribir un archivo con "null".
    """
    if folder.exists() and not overwrite:
        return WriteResult(folder=folder, status="skipped_existing", files=[])

    folder.parent.mkdir(parents=True, exist_ok=True)

    # Carpeta temporal oculta junto a la definitiva (mismo sistema de archivos).
    staging = folder.parent / f".{folder.name}.{uuid.uuid4().hex[:8]}.tmp"
    staging.mkdir()
    written: list[str] = []
    try:
        for name, data in files.items():
            if data is None:
                continue
            atomic_write_json(staging / name, data)
            written.append(name)

        if folder.exists():  # solo posible con overwrite=True
            shutil.rmtree(folder)
        os.replace(staging, folder)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return WriteResult(folder=folder, status="written", files=written)
