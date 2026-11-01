"""
Prueba de integración con un ComfyUI SIMULADO (tests/fake_comfy).

Los módulos de fake_comfy imitan solo las funciones que usa la extensión,
con las mismas firmas que el código de ComfyUI (folder_paths, nodes, server,
comfy.model_management, comfyui_version). La extensión se carga igual que lo
hace ComfyUI (importlib desde __init__.py) y se llama a la ruta HTTP real.

No sustituye a probarla en un ComfyUI de verdad (ver la guía), pero detecta
regresiones en la lógica sin necesidad de GPU ni modelos.

Requiere: pip install pytest aiohttp
"""

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

PKG_DIR = Path(__file__).resolve().parents[1]
FAKE_DIR = Path(__file__).resolve().parent / "fake_comfy"

PROMPT = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd15.safetensors"}},
    "5": {"class_type": "FancyNode", "inputs": {"image": "ref.png", "text": "../../etc/passwd"}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "proj/shot"}},
}


def _entry(status="success"):
    return {
        "prompt": (0, "pid", PROMPT, {"extra_pnginfo": {"workflow": {"nodes": [1]}}}, ["9"]),
        "outputs": {"9": {"images": [
            {"filename": "shot_00001_.png", "subfolder": "proj", "type": "output"},
            {"filename": "evil.png", "subfolder": "../../", "type": "output"},
            {"filename": "p_temp_00001_.png", "subfolder": "", "type": "temp"},
        ]}},
        "status": {"status_str": status},
    }


@pytest.fixture()
def comfy(tmp_path, monkeypatch):
    base = tmp_path
    monkeypatch.setenv("FAKE_COMFY_BASE", str(base))
    for d in ["models/checkpoints", "input", "output/proj", "user",
              "custom_nodes/ComfyUI-Fancy/.git/refs/heads"]:
        (base / d).mkdir(parents=True, exist_ok=True)
    (base / "models/checkpoints/sd15.safetensors").write_bytes(b"modelo")
    (base / "input/ref.png").write_bytes(b"entrada")
    (base / "output/proj/shot_00001_.png").write_bytes(b"img")
    fancy = base / "custom_nodes/ComfyUI-Fancy"
    (fancy / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (fancy / ".git/refs/heads/main").write_text("abc123\n")
    (fancy / "pyproject.toml").write_text('[project]\nname="fancy"\nversion="1.2.3"\n')

    # Módulos simulados primero en sys.path; se limpian al acabar la prueba.
    monkeypatch.syspath_prepend(str(FAKE_DIR))
    for name in list(sys.modules):
        if name in {"folder_paths", "nodes", "server", "comfyui_version"} or name.startswith(("comfy", "workfile")) \
                or PKG_DIR.name in name:
            monkeypatch.delitem(sys.modules, name, raising=False)

    import server
    server.PromptServer()

    spec = importlib.util.spec_from_file_location(str(PKG_DIR).replace(".", "_x_"), PKG_DIR / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module.NODE_CLASS_MAPPINGS == {}
    return base, server.PromptServer.instance


def test_end_to_end(comfy):
    base, prompt_server = comfy

    async def run():
        app = web.Application()
        app.add_routes(prompt_server.routes)
        async with TestClient(TestServer(app)) as client:
            # La entrada llega al historial con retraso (carrera con el evento).
            async def late():
                await asyncio.sleep(0.4)
                prompt_server.prompt_queue.history["pid"] = _entry()
            asyncio.create_task(late())
            first = await (await client.post("/workfile/save", json={"prompt_id": "pid"})).json()
            second = await (await client.post("/workfile/save", json={"prompt_id": "pid"})).json()
            prompt_server.prompt_queue.history["err"] = _entry("error")
            failed = await (await client.post("/workfile/save", json={"prompt_id": "err"})).json()
            bad = await client.post("/workfile/save", data="no json")
            return first, second, failed, bad.status

    first, second, failed, bad_status = asyncio.run(run())

    assert first["status"] == "done"
    assert [r["status"] for r in first["results"]] == ["written", "unsafe_path"]   # temp excluido
    assert [r["status"] for r in second["results"]] == ["skipped_existing", "unsafe_path"]
    assert failed["status"] == "not_success"
    assert bad_status == 400

    folder = base / "output/proj/shot_00001__workfile"
    assert sorted(os.listdir(folder)) == ["manifest.json", "prompt.json", "workflow.json"]
    assert json.loads((folder / "prompt.json").read_text()) == PROMPT
    manifest = json.loads((folder / "manifest.json").read_text())
    assert [m["value"] for m in manifest["models"]] == ["sd15.safetensors"]
    assert [m["value"] for m in manifest["inputs"]] == ["ref.png"]        # ../../etc/passwd ignorado
    fancy = next(p for p in manifest["node_packages"] if p["name"] == "ComfyUI-Fancy")
    assert fancy["version"] == "1.2.3" and fancy["git"]["commit"] == "abc123"
    assert manifest["output"]["sha256"] and manifest["output"]["class_type"] == "SaveImage"
    assert not (base / "evil.png").exists()


# --- Helpers para los casos siguientes ---------------------------------------

def _make_entry(prompt, images, status="success"):
    return {
        "prompt": (0, "pid", prompt, {"extra_pnginfo": {"workflow": {"nodes": [1]}}}, ["9"]),
        "outputs": {"9": {"images": images}},
        "status": {"status_str": status},
    }


def _save(prompt_server, prompt_id, entry):
    """Mete `entry` en el historial y llama a la ruta real /workfile/save."""
    prompt_server.prompt_queue.history[prompt_id] = entry

    async def run():
        app = web.Application()
        app.add_routes(prompt_server.routes)
        async with TestClient(TestServer(app)) as client:
            return await (await client.post("/workfile/save", json={"prompt_id": prompt_id})).json()

    return asyncio.run(run())


def _img(filename, subfolder="proj"):
    return {"filename": filename, "subfolder": subfolder, "type": "output"}


# --- Problema 1: entradas fuera de input/output/temp -------------------------

def test_inputs_outside_allowed_dirs_are_ignored(comfy):
    base, prompt_server = comfy
    # Archivos que existen pero quedan FUERA de input/output/temp.
    (base / "secret.txt").write_bytes(b"no debe aparecer")
    (base / "models/checkpoints/notes.txt").write_bytes(b"tampoco")
    secret_abs = str(base / "secret.txt")
    prompt = {
        "5": {"class_type": "FancyNode", "inputs": {
            "a": "ref.png",                    # input/ref.png -> válido
            "b": "shot_00001_.png [output]",   # anotación -> no existe en output raíz
            "c": "proj/shot_00001_.png [output]",  # output/proj/... -> válido
            "d": "../secret.txt",              # sale de input
            "e": secret_abs,                   # ruta absoluta fuera
            "f": "../models/checkpoints/notes.txt",
        }},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "proj/shot"}},
    }
    report = _save(prompt_server, "p1", _make_entry(prompt, [_img("shot_00001_.png")]))
    assert [r["status"] for r in report["results"]] == ["written"]

    manifest = json.loads((base / "output/proj/shot_00001__workfile/manifest.json").read_text(encoding="utf-8"))
    assert sorted(i["input_name"] for i in manifest["inputs"]) == ["a", "c"]
    assert "secret" not in json.dumps(manifest)


# --- Problema 2: salidas con el mismo nombre y distinta extensión ------------

def test_same_stem_outputs_share_one_manifest(comfy):
    base, prompt_server = comfy
    (base / "output/proj/clip_00001_.png").write_bytes(b"png")
    (base / "output/proj/clip_00001_.mp4").write_bytes(b"mp4")
    entry = _make_entry(PROMPT, [_img("clip_00001_.png"), _img("clip_00001_.mp4")])

    report = _save(prompt_server, "p2", entry)
    assert [r["status"] for r in report["results"]] == ["written", "written"]
    assert {r["workfile"] for r in report["results"]} == {"proj/clip_00001__workfile"}

    manifest = json.loads((base / "output/proj/clip_00001__workfile/manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "1.1.0"
    assert [o["relative_path"] for o in manifest["outputs"]] == [
        "proj/clip_00001_.png", "proj/clip_00001_.mp4"]
    assert manifest["output"] == manifest["outputs"][0]      # compatibilidad 1.0
    assert all(o["sha256"] for o in manifest["outputs"])

    # Re-ejecución cacheada: ambas ya están descritas -> se omiten sin aviso.
    again = _save(prompt_server, "p2b", entry)
    assert [r["status"] for r in again["results"]] == ["skipped_existing", "skipped_existing"]
    assert not any("ya existe" in w for w in again["warnings"])


def test_existing_folder_for_other_file_is_conflict(comfy):
    base, prompt_server = comfy
    (base / "output/proj/clip_00002_.png").write_bytes(b"png")
    (base / "output/proj/clip_00002_.mp4").write_bytes(b"mp4")

    # Primera ejecución: solo la imagen.
    first = _save(prompt_server, "p3", _make_entry(PROMPT, [_img("clip_00002_.png")]))
    assert [r["status"] for r in first["results"]] == ["written"]
    folder = base / "output/proj/clip_00002__workfile"
    before = (folder / "manifest.json").read_bytes()

    # Segunda ejecución: el vídeo cae en la misma carpeta, que no lo describe.
    second = _save(prompt_server, "p4", _make_entry(PROMPT, [_img("clip_00002_.mp4"), _img("clip_00002_.png")]))
    assert [r["status"] for r in second["results"]] == ["conflict", "skipped_existing"]
    assert any("clip_00002_.mp4" in w for w in second["warnings"])
    assert (folder / "manifest.json").read_bytes() == before   # no se ha tocado


def test_existing_folder_with_legacy_manifest(comfy):
    """Un manifiesto de esquema 1.0 (solo "output") se sigue reconociendo."""
    base, prompt_server = comfy
    (base / "output/proj/old_00001_.png").write_bytes(b"png")
    folder = base / "output/proj/old_00001__workfile"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps(
        {"schema_version": "1.0.0", "output": {"relative_path": "proj/old_00001_.png"}}))

    report = _save(prompt_server, "p5", _make_entry(PROMPT, [_img("old_00001_.png")]))
    assert [r["status"] for r in report["results"]] == ["skipped_existing"]
