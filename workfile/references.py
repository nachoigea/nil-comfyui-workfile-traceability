"""
references.py — Qué archivos y paquetes usó una ejecución.

Responsabilidad:
    A partir del prompt (formato API) deducir:
      1. Qué modelos referenció (checkpoints, LoRAs, VAEs, ControlNets...).
      2. Qué archivos de entrada usó (imágenes de LoadImage, etc.).
      3. Qué paquetes de nodos personalizados intervinieron y en qué versión.

Dependencias de ComfyUI:
    - folder_paths: dónde viven los modelos y las entradas (la misma función
      get_full_path que usan los nodos cargadores para encontrar sus archivos).
    - nodes: el registro de clases (NODE_CLASS_MAPPINGS) y las carpetas de los
      paquetes cargados (LOADED_MODULE_DIRS).

Decisión clave — detección genérica de modelos:
    El prompt no dice qué entradas son nombres de modelo. En lugar de
    mantener una lista de nodos conocidos (que nunca cubriría los nodos de
    terceros), se prueba cada valor de texto contra cada carpeta registrada
    en folder_paths: si get_full_path encuentra un archivo, es una referencia.
    Ventaja: funciona con cualquier nodo que use folder_paths. Limitación: no
    detecta nodos que cargan modelos desde rutas propias fuera de
    folder_paths.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from typing import Any

import folder_paths  # módulo de ComfyUI
import nodes         # módulo de ComfyUI

from .config import NON_MODEL_FOLDERS
from .hashing import HashCache
from .history import class_types_in_prompt, iter_string_inputs
from .paths import is_within_directory

logger = logging.getLogger("NIL_ComfyUI-Workfile_traceability")


@dataclass
class FileReference:
    """Un archivo usado por la ejecución."""
    node_id: str
    class_type: str
    input_name: str
    value: str                 # el texto tal cual aparece en el prompt
    category: str              # carpeta lógica ("checkpoints", "loras", "input"...)
    path: str                  # ruta absoluta en ESTA máquina
    size: int | None = None
    sha256: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# 1 y 2. Modelos y archivos de entrada
# ---------------------------------------------------------------------------

def _model_folders() -> list[str]:
    """Carpetas lógicas de modelos registradas en folder_paths."""
    return [name for name in folder_paths.folder_names_and_paths.keys() if name not in NON_MODEL_FOLDERS]


def _resolve_model(value: str, folders: list[str]) -> tuple[str, str] | None:
    """(carpeta, ruta) del primer modelo que coincida con `value`, o None."""
    for folder in folders:
        try:
            full_path = folder_paths.get_full_path(folder, value)
        except Exception:  # un valor raro no debe romper la recogida
            continue
        if full_path and os.path.isfile(full_path):
            return folder, full_path
    return None


def _allowed_input_dirs() -> list[str]:
    """Carpetas de las que puede venir un archivo de entrada: input, output y temp."""
    dirs = []
    for getter_name in ("get_input_directory", "get_output_directory", "get_temp_directory"):
        getter = getattr(folder_paths, getter_name, None)
        if getter is None:
            continue
        try:
            dirs.append(getter())
        except Exception:
            continue
    return [d for d in dirs if d]


def _resolve_input(value: str) -> str | None:
    """
    Ruta de un archivo de entrada, o None.

    get_annotated_filepath entiende las anotaciones "[input]", "[output]" y
    "[temp]" que usa la interfaz, y por defecto busca en la carpeta input.

    Decisión — validar aquí la ruta:
        get_annotated_filepath de ComfyUI solo hace os.path.join(base, valor),
        sin comprobar que el resultado siga dentro de la carpeta. Como aquí se
        prueba CUALQUIER texto del prompt, un valor como "../../secreto.txt" o
        una ruta absoluta que exista acabaría en el manifiesto (con su ruta y
        su hash, y leyéndolo entero si es enorme). Por eso solo se aceptan
        rutas dentro de input, output o temp. La comprobación es léxica (sin
        resolver enlaces) para no rechazar enlaces que el usuario haya puesto
        dentro de esas carpetas a propósito.
    """
    try:
        full_path = folder_paths.get_annotated_filepath(value)
    except Exception:
        return None
    if not full_path:
        return None
    if not any(is_within_directory(d, full_path, resolve_symlinks=False) for d in _allowed_input_dirs()):
        return None
    return full_path if os.path.isfile(full_path) else None


def collect_file_references(
    prompt: dict[str, Any],
    hash_cache: HashCache | None,
) -> tuple[list[FileReference], list[FileReference]]:
    """
    Devuelve (modelos, entradas) referenciados por el prompt.

    Si `hash_cache` es None no se calculan hashes (opción desactivada).
    Los archivos se agrupan por ruta: si dos nodos usan el mismo modelo, se
    calcula su hash una sola vez.
    """
    folders = _model_folders()
    models: list[FileReference] = []
    inputs: list[FileReference] = []

    for node_id, class_type, input_name, value in iter_string_inputs(prompt):
        # Los textos largos (prompts) o con saltos de línea no son nombres de
        # archivo; se descartan para no hacer cientos de comprobaciones inútiles.
        if len(value) > 512 or "\n" in value:
            continue

        found = _resolve_model(value, folders)
        if found:
            category, path = found
            models.append(FileReference(node_id, class_type, input_name, value, category, path))
            continue

        input_path = _resolve_input(value)
        if input_path:
            inputs.append(FileReference(node_id, class_type, input_name, value, "input", input_path))

    if hash_cache is not None:
        _hash_references(models + inputs, hash_cache)
    return models, inputs


def _hash_references(references: list[FileReference], hash_cache: HashCache) -> None:
    """Rellena size y sha256 de cada referencia (una vez por ruta)."""
    by_path: dict[str, tuple[str | None, int | None, str | None]] = {}
    for ref in references:
        if ref.path not in by_path:
            try:
                digest, size = hash_cache.get_or_compute(ref.path)
                by_path[ref.path] = (digest, size, None)
            except OSError as exc:
                by_path[ref.path] = (None, None, str(exc))
        ref.sha256, ref.size, ref.error = by_path[ref.path]


def references_to_json(references: list[FileReference]) -> list[dict[str, Any]]:
    return [asdict(ref) for ref in references]


# ---------------------------------------------------------------------------
# 3. Paquetes de nodos personalizados
# ---------------------------------------------------------------------------

def _read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return None


def read_git_revision(directory: str) -> dict[str, str] | None:
    """
    Commit y rama de un repositorio Git leyendo los archivos de .git.

    Por qué no ejecutar `git rev-parse`:
        No requiere tener git instalado ni lanzar procesos desde el servidor
        de ComfyUI (los estándares del registro desaconsejan usar subprocess
        en nodos). Leer .git/HEAD es suficiente para el caso habitual.
    """
    git_path = os.path.join(directory, ".git")

    # En submódulos y worktrees, .git es un archivo "gitdir: <ruta>".
    if os.path.isfile(git_path):
        content = _read_text(git_path) or ""
        if content.startswith("gitdir:"):
            git_path = os.path.normpath(os.path.join(directory, content.split(":", 1)[1].strip()))
    if not os.path.isdir(git_path):
        return None

    head = _read_text(os.path.join(git_path, "HEAD"))
    if not head:
        return None
    if not head.startswith("ref:"):
        return {"commit": head, "ref": "HEAD (detached)"}

    ref = head.split(":", 1)[1].strip()  # p. ej. "refs/heads/main"
    commit = _read_text(os.path.join(git_path, ref))
    if not commit:
        # Las referencias pueden estar empaquetadas en packed-refs.
        packed = _read_text(os.path.join(git_path, "packed-refs")) or ""
        for line in packed.splitlines():
            parts = line.split(" ")
            if len(parts) == 2 and parts[1] == ref:
                commit = parts[0]
                break
    return {"commit": commit or "", "ref": ref}


def read_pyproject_version(directory: str) -> str | None:
    """Versión declarada en pyproject.toml (tomllib existe desde Python 3.11)."""
    path = os.path.join(directory, "pyproject.toml")
    if not os.path.isfile(path):
        return None
    try:
        import tomllib
    except ImportError:
        return None
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle).get("project", {}).get("version")
    except Exception:
        return None


def _find_module_dir(module_name: str) -> str | None:
    """
    Carpeta de un paquete cargado.

    nodes.LOADED_MODULE_DIRS usa como clave el nombre del módulo; para paquetes
    de una sola carpeta coincide con su nombre, pero para módulos de un único
    archivo .py la clave es la ruta sin extensión. Se prueban ambos casos.
    """
    loaded = getattr(nodes, "LOADED_MODULE_DIRS", {})
    if module_name in loaded:
        return loaded[module_name]
    for key, directory in loaded.items():
        if os.path.basename(key) == module_name:
            return directory
    return None


def collect_custom_node_packages(prompt: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Paquetes de nodos que intervinieron en la ejecución, con su versión.

    Al cargar un paquete, ComfyUI anota en cada clase de nodo el atributo
    RELATIVE_PYTHON_MODULE ("custom_nodes.<paquete>" o "comfy_extras.<archivo>").
    Las clases de nodes.py (núcleo) no lo tienen. Así se sabe de qué paquete
    viene cada class_type del prompt.
    """
    packages: dict[str, dict[str, Any]] = {}
    for class_type in sorted(class_types_in_prompt(prompt)):
        node_class = nodes.NODE_CLASS_MAPPINGS.get(class_type)
        if node_class is None:
            # Puede pasar si se desinstaló un paquete después de ejecutar.
            packages.setdefault("<desconocido>", {"name": "<desconocido>", "source": "unknown", "node_types": []})
            packages["<desconocido>"]["node_types"].append(class_type)
            continue

        module = getattr(node_class, "RELATIVE_PYTHON_MODULE", "nodes")
        if module.startswith("custom_nodes."):
            name = module.split(".", 1)[1]
            if name not in packages:
                directory = _find_module_dir(name)
                packages[name] = {
                    "name": name,
                    "source": "custom_nodes",
                    "version": read_pyproject_version(directory) if directory else None,
                    "git": read_git_revision(directory) if directory else None,
                    "node_types": [],
                }
            packages[name]["node_types"].append(class_type)
        else:
            # Nodos integrados (nodes.py, comfy_extras, comfy_api_nodes): su
            # versión es la de ComfyUI, que se registra en el entorno.
            packages.setdefault("<comfyui-core>", {"name": "<comfyui-core>", "source": "core", "node_types": []})
            packages["<comfyui-core>"]["node_types"].append(class_type)

    return list(packages.values())
