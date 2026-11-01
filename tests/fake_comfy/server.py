# Simulación de PromptServer: routes (RouteTableDef de aiohttp) y prompt_queue.get_history
from aiohttp import web
class _Queue:
    def __init__(self): self.history = {}
    def get_history(self, prompt_id=None):
        return {prompt_id: self.history[prompt_id]} if prompt_id in self.history else {}
class PromptServer:
    instance = None
    def __init__(self):
        PromptServer.instance = self
        self.routes = web.RouteTableDef()
        self.prompt_queue = _Queue()
