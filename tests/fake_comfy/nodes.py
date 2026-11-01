class CheckpointLoaderSimple: pass
class SaveImage: pass
class FancyNode: RELATIVE_PYTHON_MODULE = "custom_nodes.ComfyUI-Fancy"
NODE_CLASS_MAPPINGS = {"CheckpointLoaderSimple": CheckpointLoaderSimple, "SaveImage": SaveImage, "FancyNode": FancyNode}
import os
LOADED_MODULE_DIRS = {"ComfyUI-Fancy": os.path.join(os.environ["FAKE_COMFY_BASE"], "custom_nodes", "ComfyUI-Fancy")}
