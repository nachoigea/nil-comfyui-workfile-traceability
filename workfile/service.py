"""
service.py — Orquestación: de un prompt_id a las carpetas _workfile.

Es el "director" de la extensión. No sabe nada de HTTP (eso es routes.py)
ni de cómo se escribe un archivo (eso es writer.py): coordina a los demás.

Flujo de save_workfiles(prompt_id):
    1. Esperar a que la ejecución aparezca en el historial.
    2. Interpretar la entrada (history.py).
    3. Comprobar que terminó con éxito.
    4. Quedarse con los archivos de tipo "output".
    5. En un hilo aparte (trabajo de disco y CPU):
         a. Calcular la ruta real de cada archivo y su carpeta _workfile, y
            agrupar las salidas que comparten carpeta (mismo nombre, distinta
            extensión).
         b. Si la carpeta ya existe: omitirla si su manifiesto ya describe el
            archivo (re-ejecución cacheada) o marcar "conflict" si no.
         c. Recoger una sola vez lo común: modelos, entradas, paquetes,
            entorno.
         d. Escribir prompt.json, workflow.json y manifest.json.
    6. Devolver un informe con el resultado de cada archivo.

Decisión — por qué un hilo aparte (asyncio.to_thread):
    El servidor de ComfyUI es asíncrono (aiohttp sobre asyncio): un único
    bucle atiende todas las peticiones y el WebSocket de la interfaz. Calcular
    el hash de un modelo de 6 GB dentro de ese bucle congelaría la interfaz
    durante segundos. to_thread ejecuta el trabajo pesado fuera del bucle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import folder_paths                  # módulo de ComfyUI
from server import PromptServer      # módulo de ComfyUI

from . import config
from .environment import collect_environment
from .hashing import HashCache
from .history import HistoryFormatError, HistoryRecord, OutputFile, iter_output_files, parse_history_entry
from .paths import UnsafePathError, relative_to_base, resolve_output_path, workfile_dir_for
from .references import collect_custom_node_packages, collect_file_references, references_to_json
from .writer import write_workfile_folder

logger = logging.getLogger("NIL_ComfyUI-Workfile_traceability")


@dataclass
class SaveOptions:
    """Opciones que llegan desde la interfaz (ajustes del usuario)."""
    hash_models: bool = True   # calcular SHA-256 de modelos, entradas y salidas


@dataclass
class SaveReport:
    """Resultado que se devuelve al navegador como JSON."""
    prompt_id: str
    status: str                                  # ver valores en save_workfiles
    results: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"prompt_id": self.prompt_id, "status": self.status,
                "results": self.results, "warnings": self.warnings}


# ---------------------------------------------------------------------------
# Estado compartido del proceso
# ---------------------------------------------------------------------------

# prompt_id que se están procesando ahora mismo. Evita que dos peticiones
# para la misma ejecución (p. ej. dos pestañas, o un doble clic) escriban a la
# vez. Solo se toca desde el bucle de asyncio, así que no necesita Lock.
_in_flight: set[str] = set()

_hash_cache: HashCache | None = None


def _get_hash_cache() -> HashCache:
    """
    Caché de hashes, creada la primera vez que se necesita.

    Se guarda en el directorio de "usuario de sistema" de ComfyUI
    (user/__workfile/), que según folder_paths solo es accesible
    internamente y no desde las rutas HTTP.
    """
    global _hash_cache
    if _hash_cache is None:
        getter = getattr(folder_paths, "get_system_user_directory", None)
        if getter:
            base = getter("workfile")
        else:  # versiones antiguas de ComfyUI
            base = os.path.join(folder_paths.get_user_directory(), "__workfile")
        _hash_cache = HashCache(os.path.join(base, "hash_cache.json"))
    return _hash_cache


# ---------------------------------------------------------------------------
# Paso 1: esperar al historial
# ---------------------------------------------------------------------------

async def _wait_for_history(prompt_id: str) -> dict[str, Any] | None:
    """
    Devuelve la entrada del historial, esperando un poco si aún no existe.

    Decisión — por qué esperar:
        En main.py, el trabajador de la cola ejecuta el prompt y DESPUÉS
        llama a task_done(), que es quien guarda la entrada en el historial.
        El mensaje "execution_success" se envía durante la ejecución, es decir,
        antes de que exista la entrada. La interfaz de esta extensión escucha
        el mensaje "executing" con node=None, que se envía después de
        task_done(), pero se mantiene esta espera como red de seguridad (por
        ejemplo, si otro cliente llama a la ruta antes de tiempo).
    """
    queue = PromptServer.instance.prompt_queue
    loop = asyncio.get_running_loop()
    deadline = loop.time() + config.HISTORY_WAIT_SECONDS
    while True:
        history = queue.get_history(prompt_id=prompt_id)
        if prompt_id in history:
            return history[prompt_id]
        if loop.time() >= deadline:
            return None
        await asyncio.sleep(config.HISTORY_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

async def save_workfiles(prompt_id: str, options: SaveOptions) -> SaveReport:
    """
    Crea las carpetas _workfile de una ejecución.

    Valores posibles de SaveReport.status:
        "done"            — se procesaron los archivos (ver results)
        "busy"            — ya hay un proceso en marcha para ese prompt_id
        "not_found"       — el prompt_id no apareció en el historial
        "invalid_history" — la entrada del historial no tiene la forma esperada
        "not_success"     — la ejecución terminó con error o se interrumpió
        "no_outputs"      — ningún nodo declaró archivos de tipo "output"
    """
    if prompt_id in _in_flight:
        return SaveReport(prompt_id, "busy")
    _in_flight.add(prompt_id)
    try:
        entry = await _wait_for_history(prompt_id)
        if entry is None:
            return SaveReport(prompt_id, "not_found")

        try:
            record = parse_history_entry(prompt_id, entry)
        except HistoryFormatError as exc:
            return SaveReport(prompt_id, "invalid_history", warnings=[str(exc)])

        # Decisión: solo ejecuciones completas. Si un nodo falla a mitad, el
        # prompt guardado no describiría lo que se generó de verdad.
        if not record.succeeded:
            return SaveReport(prompt_id, "not_success", warnings=record.warnings)

        outputs = [f for f in iter_output_files(record.outputs) if f.type == config.OUTPUT_TYPE]
        if not outputs:
            return SaveReport(prompt_id, "no_outputs", warnings=record.warnings)

        # Pasos 5a–5d en un hilo: no bloquear el bucle del servidor.
        return await asyncio.to_thread(_process_outputs, record, outputs, options)
    finally:
        _in_flight.discard(prompt_id)


# ---------------------------------------------------------------------------
# Pasos 5a–5d (se ejecutan en un hilo)
# ---------------------------------------------------------------------------

def _process_outputs(record: HistoryRecord, outputs: list[OutputFile], options: SaveOptions) -> SaveReport:
    report = SaveReport(record.prompt_id, "done", warnings=list(record.warnings))
    base_dir = folder_paths.get_directory_by_type(config.OUTPUT_TYPE)
    hash_cache = _get_hash_cache() if options.hash_models else None

    # 5a. Ruta real de cada archivo y agrupación por carpeta _workfile.
    #
    # Decisión — agrupar:
    #     La carpeta se nombra con el nombre SIN extensión, así que dos salidas
    #     como "clip_00001.png" y "clip_00001.mp4" corresponden a la misma
    #     carpeta. Si se procesaran una a una, la segunda encontraría la
    #     carpeta recién creada y se omitiría como si viniera de la caché, sin
    #     quedar descrita en ningún manifiesto. Agrupando, se escribe una sola
    #     carpeta cuyo manifiesto describe todas sus salidas.
    groups: dict[Path, list[tuple[OutputFile, Path, dict[str, Any]]]] = {}
    for output in outputs:
        result: dict[str, Any] = {"filename": output.filename, "subfolder": output.subfolder, "node_id": output.node_id}
        report.results.append(result)
        try:
            file_path = resolve_output_path(base_dir, output.subfolder, output.filename)
            if not file_path.is_file():
                result["status"] = "missing_file"
                continue
            folder = workfile_dir_for(file_path)
            result["workfile"] = relative_to_base(base_dir, folder)
            groups.setdefault(folder, []).append((output, file_path, result))
        except UnsafePathError as exc:
            result["status"] = "unsafe_path"
            result["error"] = str(exc)
            logger.warning("[Workfile] Ruta rechazada: %s", exc)
        except Exception as exc:  # un archivo con problemas no debe impedir los demás
            result["status"] = "error"
            result["error"] = str(exc)
            logger.exception("[Workfile] Error preparando el workfile de %s", output.filename)

    # La parte común del manifiesto se calcula solo si hace falta (puede ser
    # costosa por los hashes) y solo una vez aunque haya varias carpetas.
    shared: dict[str, Any] | None = None

    for folder, members in groups.items():
        try:
            # 5b. La carpeta ya existe.
            if folder.exists():
                _mark_existing(folder, base_dir, members, report)
                continue

            # 5c. Parte común, una sola vez.
            if shared is None:
                shared = _build_shared_manifest(record, hash_cache)

            # 5d. Manifiesto de la carpeta (todas sus salidas) + escritura.
            described = [_describe_output(base_dir, record, o, p, hash_cache) for o, p, _ in members]
            manifest = dict(shared)
            # "output" se mantiene (esquema 1.0) con la primera salida;
            # "outputs" (esquema 1.1) las lista todas.
            manifest["output"] = described[0]
            manifest["outputs"] = described
            write = write_workfile_folder(folder, {
                config.PROMPT_FILENAME: record.prompt,
                config.WORKFLOW_FILENAME: record.workflow,   # None => no se escribe
                config.MANIFEST_FILENAME: manifest,
            })
            for _, _, result in members:
                result["status"] = write.status
                result["files"] = write.files
            if write.status == "written":
                _log_written_folder(folder, [p for _, p, _ in members], write.files)
        except Exception as exc:  # una carpeta con problemas no debe impedir las demás
            for output, _, result in members:
                result["status"] = "error"
                result["error"] = str(exc)
            logger.exception("[Workfile] Error creando %s", folder)

    if hash_cache is not None:
        hash_cache.save()

    written = sum(1 for r in report.results if r.get("status") == "written")
    logger.info("[Workfile] prompt %s: %d carpeta(s) _workfile creada(s).", record.prompt_id, written)
    return report


def _log_written_folder(folder: Path, output_paths: list[Path], json_files: list[str]) -> None:
    """
    Muestra en la consola de ComfyUI dónde ha quedado cada archivo: la(s)
    salida(s) generada(s) y cada JSON de su carpeta _workfile.
    """
    lines = ["[Workfile] Generación terminada."]
    lines += [f"  Imagen guardada en: {path}" for path in output_paths]
    lines += [f"  {name} guardado en: {folder / name}" for name in json_files]
    logger.info("\n".join(lines))


def _manifest_relative_paths(folder: Path) -> set[str]:
    """
    relative_path de las salidas que describe el manifest.json de `folder`.

    Acepta el esquema 1.0 ("output") y el 1.1 ("outputs"). Devuelve un
    conjunto vacío si no hay manifiesto o no se puede leer.
    """
    try:
        with open(folder / config.MANIFEST_FILENAME, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError):
        return set()
    if not isinstance(manifest, dict):
        return set()
    entries = manifest.get("outputs")
    if not isinstance(entries, list):
        entries = [manifest.get("output")]
    return {e["relative_path"] for e in entries if isinstance(e, dict) and isinstance(e.get("relative_path"), str)}


def _mark_existing(
    folder: Path,
    base_dir: str,
    members: list[tuple[OutputFile, Path, dict[str, Any]]],
    report: SaveReport,
) -> None:
    """
    Estado de las salidas cuya carpeta _workfile ya existe.

    - "skipped_existing": el manifiesto existente ya describe ese archivo.
      Es el caso normal de una re-ejecución cacheada (el nodo de guardado no
      se re-ejecuta y el historial vuelve a declarar el mismo archivo).
    - "conflict": la carpeta existe pero NO describe ese archivo (p. ej. la
      creó una ejecución anterior para otro archivo con el mismo nombre y
      distinta extensión, o no es una carpeta de esta extensión). No se toca
      nada, pero se avisa en lugar de omitirlo en silencio.
    """
    known = _manifest_relative_paths(folder)
    for _, file_path, result in members:
        if relative_to_base(base_dir, file_path) in known:
            result["status"] = "skipped_existing"
        else:
            result["status"] = "conflict"
            message = (f"La carpeta {result['workfile']} ya existe y no describe "
                       f"{relative_to_base(base_dir, file_path)}; no se ha modificado.")
            result["error"] = message
            report.warnings.append(message)
            logger.warning("[Workfile] %s", message)


def _build_shared_manifest(record: HistoryRecord, hash_cache: HashCache | None) -> dict[str, Any]:
    """Parte del manifiesto común a todas las salidas de una ejecución."""
    models, inputs = collect_file_references(record.prompt, hash_cache)
    warnings = list(record.warnings)
    warnings += [f"No se pudo leer {m.path}: {m.error}" for m in models + inputs if m.error]
    if hash_cache is None:
        warnings.append("Hashes desactivados: el manifiesto no permite verificar que los archivos sean idénticos.")

    return {
        "schema_version": config.MANIFEST_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prompt_id": record.prompt_id,
        "status": record.status_str,
        "models": references_to_json(models),
        "inputs": references_to_json(inputs),
        "node_packages": collect_custom_node_packages(record.prompt),
        "environment": collect_environment(),
        # Otras claves que algún nodo haya añadido a extra_pnginfo.
        "extra_pnginfo_keys": sorted(k for k in record.extra_pnginfo if k != "workflow"),
        "warnings": warnings,
    }


def _describe_output(
    base_dir: str,
    record: HistoryRecord,
    output: OutputFile,
    file_path,
    hash_cache: HashCache | None,
) -> dict[str, Any]:
    """Datos del archivo concreto, incluido su hash para verificar re-ejecuciones."""
    node = record.prompt.get(output.node_id) or {}
    info: dict[str, Any] = {
        "filename": output.filename,
        "subfolder": output.subfolder,
        "relative_path": relative_to_base(base_dir, file_path),
        "node_id": output.node_id,
        "class_type": node.get("class_type") if isinstance(node, dict) else None,
        "ui_key": output.ui_key,
    }
    if hash_cache is not None:
        digest, size = hash_cache.get_or_compute(file_path)
        info["sha256"], info["size"] = digest, size
    return info
