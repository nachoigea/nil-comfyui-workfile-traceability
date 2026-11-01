"""
paths.py — Cálculo y validación de rutas.

Responsabilidad:
    - Calcular la ruta real de un archivo de salida a partir de lo que el nodo
      declaró (directorio base + subcarpeta + nombre).
    - Calcular la carpeta "<nombre>_workfile" correspondiente.
    - Garantizar que ninguna ruta se salga del directorio permitido.

Por qué validar las rutas aunque vengan del propio ComfyUI:
    La subcarpeta y el nombre los declara cada nodo de salida, incluidos nodos
    de terceros. Un nodo mal escrito (o malicioso) podría declarar algo como
    "../../otra_carpeta". ComfyUI aplica la misma comprobación al guardar
    (get_save_image_path lanza un error si la ruta sale de output); aquí se
    repite antes de escribir nada.

Este módulo NO importa nada de ComfyUI. La función is_within_directory
replica la de folder_paths.py para poder probarla de forma aislada.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import WORKFILE_SUFFIX


class UnsafePathError(ValueError):
    """La ruta calculada queda fuera del directorio permitido."""


def is_within_directory(directory: str, target: str, resolve_symlinks: bool = True) -> bool:
    """
    True si `target` está dentro de `directory`.

    Con resolve_symlinks=True (por defecto) usa realpath en ambos lados para
    que un enlace simbólico dentro del directorio que apunte fuera no burle
    la comprobación. Es el mismo criterio que folder_paths.is_within_directory
    de ComfyUI, y es el adecuado antes de ESCRIBIR.

    Con resolve_symlinks=False la comprobación es solo léxica (abspath): sirve
    para rechazar valores con ".." o rutas absolutas sin rechazar enlaces que
    el propio usuario haya puesto dentro de la carpeta (p. ej. input/ apuntando
    a otro disco). Es el criterio adecuado antes de LEER una referencia.
    """
    normalize = os.path.realpath if resolve_symlinks else os.path.abspath
    try:
        # normcase: en Windows las rutas no distinguen mayúsculas.
        directory = os.path.normcase(normalize(directory))
        target = os.path.normcase(normalize(target))
        return os.path.commonpath((directory, target)) == directory
    except ValueError:
        # commonpath falla con rutas en distintas unidades (Windows) o con
        # bytes nulos: en ambos casos no está dentro.
        return False


def resolve_output_path(base_dir: str, subfolder: str, filename: str) -> Path:
    """
    Ruta absoluta del archivo de salida.

    `filename` se reduce a su nombre base para que no pueda contener carpetas;
    las carpetas solo pueden venir de `subfolder`, y el resultado completo se
    valida contra `base_dir`.
    """
    safe_name = os.path.basename(filename)
    if not safe_name or safe_name in (".", ".."):
        raise UnsafePathError(f"Nombre de archivo no válido: {filename!r}")

    candidate = os.path.abspath(os.path.join(base_dir, os.path.normpath(subfolder or ""), safe_name))
    if not is_within_directory(base_dir, candidate):
        raise UnsafePathError(f"La ruta {candidate!r} queda fuera de {base_dir!r}")
    return Path(candidate)


def workfile_dir_for(output_file: Path) -> Path:
    """
    Carpeta _workfile que corresponde a un archivo de salida.

    Regla del proyecto: misma carpeta que el archivo, nombre del archivo sin
    extensión + "_workfile".

        output/proyecto/ComfyUI_00001_.png
        -> output/proyecto/ComfyUI_00001__workfile/

    El doble guion bajo es intencionado: SaveImage termina sus nombres en "_"
    y la regla añade "_workfile" sin modificar el nombre original, de modo
    que siempre se pueda deducir un nombre a partir del otro.

    Path.stem quita solo la última extensión ("a.b.png" -> "a.b"), que es lo
    deseable para nombres con puntos.
    """
    return output_file.parent / f"{output_file.stem}{WORKFILE_SUFFIX}"


def relative_to_base(base_dir: str, path: Path) -> str:
    """Ruta relativa con barras "/" para guardar en el manifiesto.

    Se guardan rutas relativas al directorio de salida porque son portables:
    la misma estructura sirve en cualquier máquina del pipeline aunque la raíz
    (--output-directory) sea distinta.
    """
    return Path(os.path.relpath(path, base_dir)).as_posix()
