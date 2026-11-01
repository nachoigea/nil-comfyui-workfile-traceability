"""
history.py — Interpretar una entrada del historial de ComfyUI.

Responsabilidad:
    Convertir la estructura "en bruto" que guarda ComfyUI en su historial en
    objetos con nombre (HistoryRecord, OutputFile) que el resto de la
    extensión pueda usar sin conocer los detalles internos.

Por qué el historial y no otra fuente:
    Al terminar una ejecución, ComfyUI guarda en el historial, para cada
    prompt_id, tres cosas que necesitamos y que en ningún otro sitio están
    juntas (ver PromptQueue.task_done en execution.py):
      - "prompt": el elemento de la cola, una tupla
            (número, prompt_id, prompt_api, extra_data, salidas_a_ejecutar)
        donde prompt_api es el JSON en formato API y extra_data contiene
        extra_pnginfo["workflow"], es decir, el JSON en formato UI.
      - "outputs": por cada nodo, el diccionario "ui" que devolvió
        (p. ej. {"images": [{"filename", "subfolder", "type"}]}).
      - "status": si la ejecución terminó con éxito o con error.

Este módulo NO importa nada de ComfyUI: recibe diccionarios y devuelve
objetos. Así se puede probar con datos de ejemplo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator


# Posiciones dentro de la tupla "prompt" del historial.
# Se definen como constantes para que, si ComfyUI cambia el orden, solo haya
# que tocar este bloque.
_IDX_PROMPT_ID = 1
_IDX_PROMPT = 2
_IDX_EXTRA_DATA = 3


@dataclass
class OutputFile:
    """Un archivo que un nodo de salida declaró haber escrito."""
    node_id: str      # id del nodo en el grafo (p. ej. "9")
    ui_key: str       # clave bajo la que lo declaró ("images", "gifs", ...)
    filename: str     # nombre del archivo, sin carpetas
    subfolder: str    # subcarpeta relativa al directorio de su tipo
    type: str         # "output", "temp" o "input"


@dataclass
class HistoryRecord:
    """Todo lo que la extensión necesita de una ejecución terminada."""
    prompt_id: str
    prompt: dict[str, Any]                 # JSON formato API (ejecutado)
    workflow: dict[str, Any] | None        # JSON formato UI (puede faltar)
    extra_pnginfo: dict[str, Any]          # todo lo que venía en extra_pnginfo
    outputs: dict[str, Any]                # salidas "ui" por nodo
    status_str: str | None                 # "success", "error" o None
    warnings: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.status_str == "success"


class HistoryFormatError(ValueError):
    """La entrada del historial no tiene la forma esperada."""


def parse_history_entry(prompt_id: str, entry: dict[str, Any]) -> HistoryRecord:
    """
    Convierte una entrada del historial en un HistoryRecord.

    `entry` es el valor de history[prompt_id], tal como lo devuelve
    PromptQueue.get_history(prompt_id=...) o la ruta /history/{prompt_id}.

    Es defensiva a propósito: si falta el workflow (por ejemplo, porque la
    ejecución se lanzó por API sin enviarlo) no falla, sino que lo anota en
    `warnings`. Solo falla si falta el prompt, porque sin él no hay nada que
    guardar.
    """
    if not isinstance(entry, dict):
        raise HistoryFormatError("La entrada del historial no es un diccionario.")

    queue_item = entry.get("prompt")
    # En memoria es una tupla; si llega por HTTP (JSON) es una lista.
    if not isinstance(queue_item, (list, tuple)) or len(queue_item) <= _IDX_EXTRA_DATA:
        raise HistoryFormatError("El campo 'prompt' del historial no tiene la forma esperada.")

    prompt = queue_item[_IDX_PROMPT]
    if not isinstance(prompt, dict) or not prompt:
        raise HistoryFormatError("El historial no contiene un prompt en formato API.")

    stored_id = queue_item[_IDX_PROMPT_ID]
    warnings: list[str] = []
    if stored_id != prompt_id:
        # No debería ocurrir; se anota en lugar de fallar.
        warnings.append(f"prompt_id del historial ({stored_id}) distinto del pedido ({prompt_id}).")

    extra_data = queue_item[_IDX_EXTRA_DATA] if isinstance(queue_item[_IDX_EXTRA_DATA], dict) else {}
    extra_pnginfo = extra_data.get("extra_pnginfo") or {}
    if not isinstance(extra_pnginfo, dict):
        extra_pnginfo = {}

    workflow = extra_pnginfo.get("workflow")
    if not isinstance(workflow, dict):
        workflow = None
        warnings.append(
            "La ejecución no incluía el workflow (formato UI). "
            "Ocurre si se lanzó por API sin enviarlo en extra_data.extra_pnginfo."
        )

    status = entry.get("status") or {}
    status_str = status.get("status_str") if isinstance(status, dict) else None

    outputs = entry.get("outputs") or {}
    if not isinstance(outputs, dict):
        outputs = {}

    return HistoryRecord(
        prompt_id=prompt_id,
        prompt=prompt,
        workflow=workflow,
        extra_pnginfo=extra_pnginfo,
        outputs=outputs,
        status_str=status_str,
        warnings=warnings,
    )


def iter_output_files(outputs: dict[str, Any]) -> Iterator[OutputFile]:
    """
    Recorre las salidas "ui" de todos los nodos y devuelve cada archivo.

    Por qué recorrer TODAS las claves:
        Cada nodo de salida elige la clave bajo la que informa de sus archivos:
        SaveImage usa "images", SaveLatent usa "latents" (ver nodes.py) y los
        nodos de terceros pueden usar otras. En lugar de asumir una clave,
        se acepta cualquier lista cuyos elementos sean diccionarios con
        "filename" y "type". Es la convención que comparten los nodos de
        guardado integrados; los nodos que no la sigan no se detectan.
    """
    for node_id, ui in outputs.items():
        if not isinstance(ui, dict):
            continue
        for ui_key, value in ui.items():
            # Hay claves que no son listas de archivos (p. ej. "animated": [True]).
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict):
                    continue
                filename = item.get("filename")
                file_type = item.get("type")
                if not isinstance(filename, str) or not filename or not isinstance(file_type, str):
                    continue
                subfolder = item.get("subfolder") or ""
                if not isinstance(subfolder, str):
                    subfolder = ""
                yield OutputFile(
                    node_id=str(node_id),
                    ui_key=str(ui_key),
                    filename=filename,
                    subfolder=subfolder,
                    type=file_type,
                )


def iter_string_inputs(prompt: dict[str, Any]) -> Iterator[tuple[str, str, str, str]]:
    """
    Recorre los valores de texto de todos los nodos del prompt (formato API).

    Devuelve tuplas (node_id, class_type, nombre_entrada, valor).

    Se usa para detectar referencias a archivos (modelos, imágenes de
    entrada). En formato API, una entrada es un valor literal o un enlace
    [id_nodo, slot]; los enlaces son listas, así que filtrar por `str`
    descarta los enlaces automáticamente.
    """
    for node_id, node in prompt.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type", ""))
        inputs = node.get("inputs") or {}
        if not isinstance(inputs, dict):
            continue
        for name, value in inputs.items():
            if isinstance(value, str) and value:
                yield str(node_id), class_type, str(name), value


def class_types_in_prompt(prompt: dict[str, Any]) -> set[str]:
    """Conjunto de class_type usados en el prompt (para saber qué paquetes intervinieron)."""
    return {
        str(node["class_type"])
        for node in prompt.values()
        if isinstance(node, dict) and node.get("class_type")
    }
