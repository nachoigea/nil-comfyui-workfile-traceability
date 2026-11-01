# NIL_ComfyUI-Workfile_traceability (v0.1.0)

Extensión para ComfyUI que, al activar una opción en **Ajustes › Workfile**,
crea junto a cada imagen o vídeo guardado en `output` una carpeta
`<nombre>_workfile` con:

| Archivo | Contenido |
| --- | --- |
| `prompt.json` | El grafo en formato API, con los valores realmente ejecutados. Se puede reenviar a `/prompt`. |
| `workflow.json` | El grafo en formato UI, para reabrirlo en el lienzo (si la ejecución lo incluía). |
| `manifest.json` | Hashes SHA-256 de modelos, entradas y salida; versiones de paquetes; entorno (ComfyUI, PyTorch, GPU, flags). |

Si un nodo guarda varios archivos con el mismo nombre y distinta extensión
(p. ej. `clip_00001_.png` y `clip_00001_.mp4`), comparten una sola carpeta y
`manifest.json` los describe todos en `outputs`. Si la carpeta ya existía y no
describe el archivo, no se modifica y el resultado se marca como `conflict`.

No añade nodos: funciona con los nodos de guardado que informan de sus
archivos con la convención `{filename, subfolder, type}`.

## Instalación

1. Copia esta carpeta en `ComfyUI/custom_nodes/NIL_ComfyUI-Workfile_traceability`.
2. Reinicia ComfyUI y recarga el navegador.
3. Comprueba `http://127.0.0.1:8188/workfile/status`.
4. Activa la opción en Ajustes › Workfile.

## Estructura

```
NIL_ComfyUI-Workfile_traceability/
├── __init__.py          entrada: registra rutas, exporta WEB_DIRECTORY y NODE_CLASS_MAPPINGS vacío
├── pyproject.toml
├── web/js/workfile.js   interfaz: ajustes + detección del fin de ejecución
├── workfile/
│   ├── config.py        constantes compartidas
│   ├── history.py       interpreta el historial de ComfyUI       (sin ComfyUI)
│   ├── paths.py         rutas y validación de seguridad          (sin ComfyUI)
│   ├── hashing.py       SHA-256 con caché persistente            (sin ComfyUI)
│   ├── writer.py        escritura atómica de JSON y carpetas     (sin ComfyUI)
│   ├── references.py    modelos, entradas y paquetes usados      (folder_paths, nodes)
│   ├── environment.py   versiones, dispositivo, flags            (folder_paths, torch)
│   ├── service.py       orquesta todo el proceso                 (PromptServer)
│   └── routes.py        rutas HTTP /workfile/save y /status      (aiohttp, PromptServer)
└── tests/               pruebas (pytest) y un ComfyUI simulado
```

## Pruebas

```
pip install pytest aiohttp
python -m pytest tests
```

La guía completa de módulos, flujo y decisiones está en el documento que
acompaña a esta versión.
