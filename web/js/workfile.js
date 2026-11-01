/**
 * workfile.js — Parte de interfaz de NIL_ComfyUI-Workfile_traceability.
 *
 * ComfyUI carga automáticamente todos los .js de la carpeta que el paquete
 * exporta como WEB_DIRECTORY. Este archivo:
 *
 *   1. Añade dos opciones en Ajustes (sección "Workfile"):
 *        - Activar la creación de carpetas _workfile.
 *        - Calcular hashes SHA-256 de modelos, entradas y salidas.
 *   2. Detecta cuándo termina cada ejecución lanzada desde esta pestaña.
 *   3. Si la opción está activa, pide al servidor que cree las carpetas.
 *
 * Toda la lógica de archivos ocurre en el servidor (Python). El navegador
 * solo envía el prompt_id: no puede escribir en el disco del servidor y no
 * conoce las rutas reales.
 *
 * IMPORTANTE: los identificadores y la ruta deben coincidir con
 * workfile/config.py.
 */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const SETTING_ENABLED = "Workfile.Enabled";
const SETTING_HASH = "Workfile.HashModels";
const ROUTE_SAVE = "/workfile/save";
const LOG = "[Workfile]";

/**
 * prompt_id de las ejecuciones que esta pestaña ha visto empezar y que aún
 * no han terminado. Sirve para reaccionar solo una vez por ejecución.
 */
const pending = new Set();
let lastStartedPromptId = null;

/**
 * Lee un ajuste con la API documentada (app.extensionManager.setting.get).
 * La segunda vía cubre frontends antiguos; si ninguna existe, se usa el
 * valor por defecto.
 */
function getSetting(id, fallback) {
  try {
    const value = app.extensionManager?.setting?.get(id);
    if (value !== undefined && value !== null) return value;
  } catch (_) { /* API no disponible */ }
  try {
    const value = app.ui?.settings?.getSettingValue?.(id);
    if (value !== undefined && value !== null) return value;
  } catch (_) { /* API no disponible */ }
  return fallback;
}

/**
 * Pide al servidor que cree las carpetas _workfile de una ejecución.
 * api.fetchApi añade el prefijo /api y la cabecera de usuario de ComfyUI.
 */
async function requestWorkfiles(promptId) {
  const body = {
    prompt_id: promptId,
    options: { hash_models: Boolean(getSetting(SETTING_HASH, true)) },
  };
  try {
    const response = await api.fetchApi(ROUTE_SAVE, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const report = await response.json();
    if (!response.ok) {
      console.warn(LOG, "El servidor rechazó la petición:", report);
      return;
    }
    const written = (report.results || []).filter((r) => r.status === "written");
    console.info(LOG, `prompt ${promptId}: estado=${report.status}, carpetas creadas=${written.length}`, report);
  } catch (error) {
    console.error(LOG, "No se pudo contactar con el servidor:", error);
  }
}

/**
 * Reacción a cada mensaje "executing" del WebSocket.
 *
 * Decisión — por qué "executing" con node === null y no "execution_success":
 *   En main.py de ComfyUI, "execution_success" se emite durante la
 *   ejecución, ANTES de que task_done() guarde la entrada en el historial.
 *   "executing" con node=None se emite justo DESPUÉS de task_done(), así que
 *   en ese momento el historial ya contiene el prompt, el workflow y las
 *   salidas. Es también la señal de fin que usa el ejemplo oficial
 *   websockets_api_example.py.
 *
 *   Ese mensaje llega tanto si la ejecución tuvo éxito como si falló; el
 *   servidor consulta el estado en el historial y solo actúa si fue un éxito.
 */
function onExecuting(event) {
  const detail = event.detail;
  // Frontends actuales envían {node, prompt_id, ...}; algunos antiguos
  // enviaban solo el id del nodo (null al terminar).
  const node = detail && typeof detail === "object" ? detail.node : detail;
  if (node !== null && node !== undefined) return; // aún ejecutando un nodo

  const promptId = (detail && typeof detail === "object" && detail.prompt_id) || lastStartedPromptId;
  if (!promptId || !pending.has(promptId)) return; // no la empezamos aquí o ya se trató
  pending.delete(promptId);

  if (!getSetting(SETTING_ENABLED, false)) return; // opción desactivada
  requestWorkfiles(promptId);
}

/** Recuerda las ejecuciones que empieza el servidor para esta pestaña. */
function onExecutionStart(event) {
  const promptId = event.detail?.prompt_id;
  if (!promptId) return;
  pending.add(promptId);
  lastStartedPromptId = promptId;
}

app.registerExtension({
  name: "workfile.trazabilidad",

  // Opciones que aparecen en el panel de Ajustes. ComfyUI guarda su valor en
  // user/default/comfy.settings.json (en el servidor).
  settings: [
    {
      id: SETTING_ENABLED,
      name: "Crear carpeta _workfile al terminar cada ejecución",
      type: "boolean",
      defaultValue: false,
      category: ["Workfile", "Trazabilidad", "Activar"],
      tooltip: "Guarda prompt.json, workflow.json y manifest.json junto a cada imagen o vídeo de la carpeta output.",
    },
    {
      id: SETTING_HASH,
      name: "Calcular hashes SHA-256 (modelos, entradas y salidas)",
      type: "boolean",
      defaultValue: true,
      category: ["Workfile", "Trazabilidad", "Hashes"],
      tooltip: "Necesario para verificar la reproducibilidad. La primera vez puede tardar con modelos grandes; después se usa una caché.",
    },
  ],

  // setup() se llama al final del arranque de la interfaz: lugar recomendado
  // por la documentación para añadir escuchadores de eventos.
  async setup() {
    api.addEventListener("execution_start", onExecutionStart);
    api.addEventListener("executing", onExecuting);
    console.info(LOG, "Extensión cargada.");
  },
});
