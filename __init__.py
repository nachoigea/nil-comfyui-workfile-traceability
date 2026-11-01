"""
NIL_ComfyUI-Workfile_traceability — punto de entrada de la extensión.

ComfyUI importa este archivo al arrancar (recorre la carpeta custom_nodes).
Aquí se hacen tres cosas:

1. Registrar las rutas HTTP del servidor (importando workfile.routes, que las
   declara como efecto secundario de la importación).

2. Exportar NODE_CLASS_MAPPINGS VACÍO.
   Decisión: la extensión no añade ningún nodo (requisito del proyecto). Pero
   el cargador de ComfyUI (load_custom_node en nodes.py) solo da un paquete
   por cargado correctamente si exporta NODE_CLASS_MAPPINGS o
   comfy_entrypoint; si no, muestra un aviso de "Skip ... module". Un
   diccionario vacío cumple el contrato sin crear nodos.

3. Exportar WEB_DIRECTORY para que el navegador cargue web/js/workfile.js,
   que añade la opción en Ajustes y detecta el fin de cada ejecución.
"""

import logging

logger = logging.getLogger("NIL_ComfyUI-Workfile_traceability")

try:
    from .workfile import routes  # noqa: F401  (registra /workfile/save y /workfile/status)
except Exception:  # si algo falla, ComfyUI debe seguir arrancando
    logger.exception("[Workfile] No se pudieron registrar las rutas; la extensión queda desactivada.")

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
WEB_DIRECTORY = "./web/js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
