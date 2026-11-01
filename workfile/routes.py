"""
routes.py — Rutas HTTP propias de la extensión.

Es la "puerta" del servidor: recibe la petición del navegador, valida lo
que llega y delega en service.py. No contiene lógica de negocio.

Registro de rutas:
    Se sigue el patrón documentado por ComfyUI: obtener
    PromptServer.instance.routes y decorar funciones definidas a nivel de
    módulo (la documentación advierte de no definirlas dentro de una clase).
    ComfyUI registra además cada ruta con el prefijo /api, que es el que usa
    api.fetchApi() desde el navegador.

Rutas:
    POST /workfile/save    {"prompt_id": "...", "options": {"hash_models": true}}
    GET  /workfile/status  información de diagnóstico de la extensión

Seguridad:
    Del cliente solo se acepta un prompt_id y opciones booleanas. Todas las
    rutas de disco se calculan en el servidor a partir del historial, nunca a
    partir de lo que envía el navegador, así que un cliente no puede pedir
    que se escriba en una carpeta arbitraria.
"""

from __future__ import annotations

import logging

from aiohttp import web
from server import PromptServer  # módulo de ComfyUI

import folder_paths              # módulo de ComfyUI

from . import config
from .service import SaveOptions, save_workfiles

logger = logging.getLogger("NIL_ComfyUI-Workfile_traceability")

routes = PromptServer.instance.routes

# Longitud máxima razonable de un prompt_id (ComfyUI usa UUID de 36 caracteres).
_MAX_PROMPT_ID_LEN = 128


def _error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


@routes.post(config.ROUTE_SAVE)
async def workfile_save(request: web.Request) -> web.Response:
    """Crea las carpetas _workfile de la ejecución indicada."""
    try:
        body = await request.json()
    except Exception:
        return _error("El cuerpo de la petición debe ser JSON.")

    if not isinstance(body, dict):
        return _error("El cuerpo debe ser un objeto JSON.")

    prompt_id = body.get("prompt_id")
    if not isinstance(prompt_id, str) or not prompt_id or len(prompt_id) > _MAX_PROMPT_ID_LEN:
        return _error("Falta 'prompt_id' o no es válido.")

    raw_options = body.get("options") or {}
    if not isinstance(raw_options, dict):
        return _error("'options' debe ser un objeto JSON.")
    # bool() porque solo se aceptan interruptores; cualquier otra clave se ignora.
    options = SaveOptions(hash_models=bool(raw_options.get("hash_models", True)))

    report = await save_workfiles(prompt_id, options)
    return web.json_response(report.to_json())


@routes.get(config.ROUTE_STATUS)
async def workfile_status(request: web.Request) -> web.Response:
    """Diagnóstico: permite comprobar desde el navegador que la extensión está cargada."""
    return web.json_response({
        "extension_version": config.EXTENSION_VERSION,
        "manifest_schema_version": config.MANIFEST_SCHEMA_VERSION,
        "output_directory": folder_paths.get_output_directory(),
    })


logger.info("[Workfile] Rutas registradas: %s, %s", config.ROUTE_SAVE, config.ROUTE_STATUS)
