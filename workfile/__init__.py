"""
Paquete interno de NIL_ComfyUI-Workfile_traceability.

Módulos sin dependencias de ComfyUI (se pueden probar de forma aislada):
    config, history, paths, hashing, writer

Módulos que usan ComfyUI (solo funcionan dentro del servidor):
    references, environment, service, routes

Importar este paquete NO registra nada; el registro de rutas ocurre al
importar `routes`, cosa que hace el __init__.py raíz de la extensión.
"""
