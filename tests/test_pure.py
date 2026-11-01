"""
Pruebas de los módulos que no dependen de ComfyUI.

Ejecutar desde la carpeta NIL_ComfyUI-Workfile_traceability:
    python -m pytest tests
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workfile import config  # noqa: E402
from workfile.hashing import HashCache, sha256_file  # noqa: E402
from workfile.history import (  # noqa: E402
    HistoryFormatError,
    class_types_in_prompt,
    iter_output_files,
    iter_string_inputs,
    parse_history_entry,
)
from workfile.paths import UnsafePathError, is_within_directory, resolve_output_path, workfile_dir_for  # noqa: E402
from workfile.writer import atomic_write_json, write_workfile_folder  # noqa: E402

PROMPT = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd15.safetensors"}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "un gato", "clip": ["4", 1]}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "proj/shot"}},
}
WORKFLOW = {"nodes": [], "links": []}


def make_entry(status="success", with_workflow=True):
    extra = {"client_id": "abc", "extra_pnginfo": {"workflow": WORKFLOW} if with_workflow else {}}
    return {
        "prompt": (0, "pid-1", PROMPT, extra, ["9"]),
        "outputs": {
            "9": {"images": [
                {"filename": "shot_00001_.png", "subfolder": "proj", "type": "output"},
                {"filename": "shot_00002_.png", "subfolder": "proj", "type": "output"},
            ]},
            "12": {"images": [{"filename": "x_temp_abcde_00001_.png", "subfolder": "", "type": "temp"}],
                   "animated": [False]},
        },
        "status": {"status_str": status, "completed": status == "success", "messages": []},
    }


# --- history -----------------------------------------------------------------

def test_parse_history_ok():
    record = parse_history_entry("pid-1", make_entry())
    assert record.succeeded
    assert record.prompt == PROMPT
    assert record.workflow == WORKFLOW
    assert record.warnings == []


def test_parse_history_accepts_list_from_json():
    entry = json.loads(json.dumps(make_entry()))  # la tupla se convierte en lista
    assert parse_history_entry("pid-1", entry).prompt == PROMPT


def test_parse_history_without_workflow_warns():
    record = parse_history_entry("pid-1", make_entry(with_workflow=False))
    assert record.workflow is None
    assert any("workflow" in w for w in record.warnings)


def test_parse_history_error_status():
    assert not parse_history_entry("pid-1", make_entry(status="error")).succeeded


def test_parse_history_bad_shape():
    with pytest.raises(HistoryFormatError):
        parse_history_entry("pid-1", {"prompt": "nada"})


def test_iter_output_files_all_keys_and_types():
    files = list(iter_output_files(make_entry()["outputs"]))
    assert [f.filename for f in files] == ["shot_00001_.png", "shot_00002_.png", "x_temp_abcde_00001_.png"]
    assert [f.type for f in files] == ["output", "output", "temp"]


def test_iter_string_inputs_skips_links():
    values = {(n, name) for n, _, name, _ in iter_string_inputs(PROMPT)}
    assert ("6", "clip") not in values          # enlace [id, slot]
    assert ("4", "ckpt_name") in values


def test_class_types():
    assert class_types_in_prompt(PROMPT) == {"CheckpointLoaderSimple", "CLIPTextEncode", "SaveImage"}


# --- paths -------------------------------------------------------------------

def test_workfile_dir_name(tmp_path):
    assert workfile_dir_for(tmp_path / "proj" / "ComfyUI_00001_.png").name == "ComfyUI_00001__workfile"
    assert workfile_dir_for(tmp_path / "v.final.mp4").name == "v.final_workfile"


def test_resolve_output_path_ok(tmp_path):
    p = resolve_output_path(str(tmp_path), "proj/shot010", "a.png")
    assert p == tmp_path / "proj" / "shot010" / "a.png"


@pytest.mark.parametrize("subfolder,filename", [("../fuera", "a.png"), ("", ".."), ("/etc", "a.png")])
def test_resolve_output_path_rejects_escape(tmp_path, subfolder, filename):
    with pytest.raises(UnsafePathError):
        resolve_output_path(str(tmp_path / "output"), subfolder, filename)


def test_filename_cannot_carry_folders(tmp_path):
    # Las carpetas del nombre se descartan: solo cuenta subfolder.
    assert resolve_output_path(str(tmp_path), "", "../../a.png") == tmp_path / "a.png"


@pytest.mark.parametrize("resolve", [True, False])
def test_is_within_directory(tmp_path, resolve):
    base = tmp_path / "input"
    base.mkdir()
    assert is_within_directory(str(base), str(base / "a.png"), resolve_symlinks=resolve)
    assert is_within_directory(str(base), str(base / "sub" / ".." / "a.png"), resolve_symlinks=resolve)
    assert not is_within_directory(str(base), str(base / ".." / "secret.txt"), resolve_symlinks=resolve)
    assert not is_within_directory(str(base), str(tmp_path / "input2" / "a.png"), resolve_symlinks=resolve)


# --- hashing -----------------------------------------------------------------

def test_hash_cache_reuses_and_detects_change(tmp_path):
    f = tmp_path / "m.bin"
    f.write_bytes(b"abc")
    cache = HashCache(tmp_path / "cache.json")
    digest, size = cache.get_or_compute(f)
    assert digest == sha256_file(f) and size == 3
    cache.save()

    reloaded = HashCache(tmp_path / "cache.json")
    assert reloaded.get_or_compute(f)[0] == digest   # desde la caché

    f.write_bytes(b"abcd")                            # tamaño distinto -> recalcula
    assert reloaded.get_or_compute(f)[0] == sha256_file(f) != digest


def test_hash_cache_tolerates_corrupt_file(tmp_path):
    (tmp_path / "cache.json").write_text("{no es json")
    HashCache(tmp_path / "cache.json")  # no lanza


# --- writer ------------------------------------------------------------------

def test_atomic_write_json_no_leftovers(tmp_path):
    atomic_write_json(tmp_path / "a.json", {"texto": "ñandú"})
    assert json.loads((tmp_path / "a.json").read_text(encoding="utf-8")) == {"texto": "ñandú"}
    assert os.listdir(tmp_path) == ["a.json"]


def test_write_folder_and_skip_existing(tmp_path):
    folder = tmp_path / "x_workfile"
    r1 = write_workfile_folder(folder, {"prompt.json": {"a": 1}, "workflow.json": None})
    assert r1.status == "written" and r1.files == ["prompt.json"]
    assert sorted(os.listdir(folder)) == ["prompt.json"]
    r2 = write_workfile_folder(folder, {"prompt.json": {"a": 2}})
    assert r2.status == "skipped_existing"
    assert json.loads((folder / "prompt.json").read_text()) == {"a": 1}
    assert [n for n in os.listdir(tmp_path) if n.endswith(".tmp")] == []


def test_config_consistent_with_js():
    js = (Path(__file__).resolve().parents[1] / "web" / "js" / "workfile.js").read_text(encoding="utf-8")
    assert f'"{config.ROUTE_SAVE}"' in js
