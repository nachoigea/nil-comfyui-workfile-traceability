# Simulación mínima de folder_paths de ComfyUI (mismas firmas que el original).
import os
BASE = os.environ["FAKE_COMFY_BASE"]
folder_names_and_paths = {
    "checkpoints": ([os.path.join(BASE, "models", "checkpoints")], {".safetensors"}),
    "loras": ([os.path.join(BASE, "models", "loras")], {".safetensors"}),
    "custom_nodes": ([os.path.join(BASE, "custom_nodes")], set()),
}
def get_output_directory(): return os.path.join(BASE, "output")
def get_input_directory(): return os.path.join(BASE, "input")
def get_temp_directory(): return os.path.join(BASE, "temp")
def get_user_directory(): return os.path.join(BASE, "user")
def get_system_user_directory(name="system"): return os.path.join(get_user_directory(), "__" + name)
def get_directory_by_type(t): return {"output": get_output_directory(), "temp": get_temp_directory(), "input": get_input_directory()}.get(t)
def get_full_path(folder, filename):
    if folder not in folder_names_and_paths: return None
    filename = os.path.relpath(os.path.join("/", filename), "/")
    for x in folder_names_and_paths[folder][0]:
        p = os.path.join(x, filename)
        if os.path.isfile(p): return p
    return None
# Igual que ComfyUI: las anotaciones van al FINAL ("img.png [output]") y
# get_annotated_filepath NO valida que la ruta quede dentro de la carpeta.
def annotated_filepath(name):
    if name.endswith("[output]"): return name[:-9], get_output_directory()
    if name.endswith("[input]"): return name[:-8], get_input_directory()
    if name.endswith("[temp]"): return name[:-7], get_temp_directory()
    return name, None
def get_annotated_filepath(name, default_dir=None):
    name, base_dir = annotated_filepath(name)
    if base_dir is None:
        base_dir = default_dir if default_dir is not None else get_input_directory()
    return os.path.join(base_dir, name)
