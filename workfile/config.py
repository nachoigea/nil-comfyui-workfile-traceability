"""
config.py — Constantes compartidas por toda la extensión.

Por qué un módulo aparte:
    Los nombres de rutas, los identificadores de ajustes y la versión del
    esquema del manifiesto se usan en varios sitios (Python y JavaScript).
    Tenerlos en un único lugar evita que se desincronicen. El archivo
    JavaScript repite los mismos valores; si cambias uno aquí, cámbialo
    también en web/js/workfile.js.

Este módulo NO importa nada de ComfyUI, así que se puede usar en pruebas.
"""

# Versión de la propia extensión. Debe coincidir con pyproject.toml.
EXTENSION_VERSION = "0.1.0"

# Versión del esquema de manifest.json. Se incrementa (versionado semántico)
# cuando cambia la estructura del manifiesto de forma incompatible, para que
# las herramientas que lo lean (por ejemplo un lector en Nuke) sepan qué
# formato esperan.
#   1.1.0: se añade "outputs" (lista con todas las salidas de la carpeta);
#          "output" se mantiene con la primera, por compatibilidad.
MANIFEST_SCHEMA_VERSION = "1.1.0"

# Sufijo de la carpeta que se crea junto a cada archivo de salida.
# Requisito del proyecto: "<nombre del archivo sin extensión>_workfile".
WORKFILE_SUFFIX = "_workfile"

# Nombres de los archivos que se escriben dentro de la carpeta _workfile.
PROMPT_FILENAME = "prompt.json"        # formato API: lo que se ejecutó
WORKFLOW_FILENAME = "workflow.json"    # formato UI: para reabrir el grafo
MANIFEST_FILENAME = "manifest.json"    # entorno, hashes, versiones

# Rutas HTTP propias. ComfyUI añade además una copia con prefijo /api,
# que es la que usa api.fetchApi() desde el navegador.
ROUTE_SAVE = "/workfile/save"
ROUTE_STATUS = "/workfile/status"

# Solo se procesan archivos de este tipo. "temp" son previsualizaciones
# (PreviewImage) que ComfyUI borra; "input" son entradas, no resultados.
OUTPUT_TYPE = "output"

# Espera máxima (segundos) a que la entrada aparezca en el historial.
# Ver la decisión "esperar al historial" en service.py.
HISTORY_WAIT_SECONDS = 10.0
HISTORY_POLL_INTERVAL = 0.25

# Tamaño de bloque al calcular hashes: leer en trozos evita cargar en
# memoria modelos de varios GB.
HASH_CHUNK_BYTES = 8 * 1024 * 1024  # 8 MiB

# Carpetas de folder_paths que no contienen modelos ni datos de entrada y
# que, por tanto, no se usan para detectar referencias a archivos.
NON_MODEL_FOLDERS = frozenset({"custom_nodes"})
