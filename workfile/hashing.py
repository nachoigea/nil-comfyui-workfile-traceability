"""
hashing.py — Hash SHA-256 de archivos, con caché persistente.

Por qué hashes:
    El prompt solo guarda NOMBRES de archivo ("modelo.safetensors"). Dos
    archivos con el mismo nombre pueden ser distintos, y la documentación de
    ComfyUI advierte que el workflow no incluye los modelos ni los assets de
    entrada. El SHA-256 identifica el contenido exacto.

Por qué una caché:
    Calcular el hash de un modelo de varios GB tarda segundos o minutos, y
    los mismos modelos se reutilizan en casi todas las ejecuciones. La caché
    guarda, por ruta, el tamaño y la fecha de modificación junto al hash; si
    ninguno ha cambiado se reutiliza el hash sin leer el archivo.

    Riesgo asumido: si alguien reemplaza un archivo conservando exactamente
    el mismo tamaño y la misma fecha de modificación, la caché devolvería un
    hash antiguo. Es el mismo compromiso que hacen herramientas como make o
    rsync por defecto; para un pipeline estricto se puede desactivar la caché.

Por qué SHA-256:
    Es el algoritmo por defecto de ComfyUI para comparar archivos
    (--default-hashing-function sha256) y el que usa LoadImage en IS_CHANGED.

Este módulo NO importa nada de ComfyUI.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path

from .config import HASH_CHUNK_BYTES

logger = logging.getLogger("NIL_ComfyUI-Workfile_traceability")


def sha256_file(path: str | os.PathLike, chunk_size: int = HASH_CHUNK_BYTES) -> str:
    """SHA-256 de un archivo leyéndolo por bloques (memoria constante)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


class HashCache:
    """
    Caché {ruta absoluta -> (tamaño, mtime_ns, sha256)} guardada en un JSON.

    Es segura entre hilos (un Lock protege el diccionario y el archivo)
    porque el servicio calcula hashes en un hilo aparte para no bloquear el
    servidor, y dos ejecuciones pueden terminar casi a la vez.
    """

    def __init__(self, cache_file: str | os.PathLike | None):
        self._file = Path(cache_file) if cache_file else None
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if not self._file or not self._file.is_file():
            return
        try:
            with open(self._file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                self._data = data
        except (OSError, json.JSONDecodeError) as exc:
            # Una caché corrupta no debe impedir trabajar: se empieza de cero.
            logger.warning("[Workfile] No se pudo leer la caché de hashes (%s); se reconstruirá.", exc)
            self._data = {}

    def get_or_compute(self, path: str | os.PathLike) -> tuple[str, int]:
        """
        Devuelve (sha256, tamaño en bytes) del archivo.

        Usa la caché si tamaño y mtime coinciden; si no, lee el archivo.
        """
        abs_path = os.path.abspath(path)
        stat = os.stat(abs_path)
        key_size, key_mtime = stat.st_size, stat.st_mtime_ns

        with self._lock:
            cached = self._data.get(abs_path)
            if cached and cached.get("size") == key_size and cached.get("mtime_ns") == key_mtime:
                return cached["sha256"], key_size

        # El cálculo se hace FUERA del lock: puede tardar mucho y no debe
        # bloquear a otros hilos que consultan hashes ya cacheados.
        digest = sha256_file(abs_path)

        with self._lock:
            self._data[abs_path] = {"size": key_size, "mtime_ns": key_mtime, "sha256": digest}
            self._dirty = True
        return digest, key_size

    def save(self) -> None:
        """Escribe la caché en disco si ha cambiado (escritura atómica)."""
        if not self._file:
            return
        with self._lock:
            if not self._dirty:
                return
            snapshot = dict(self._data)
            self._dirty = False
        from .writer import atomic_write_json  # import local: evita ciclo de módulos
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self._file, snapshot)
        except OSError as exc:
            logger.warning("[Workfile] No se pudo guardar la caché de hashes: %s", exc)
