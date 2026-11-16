from .codex_node import CodexExecImageGen
from .codex_text_node import CodexExecText


NODE_CLASS_MAPPINGS = {
    "CodexExecImageGen": CodexExecImageGen,
    "CodexExecText": CodexExecText,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CodexExecImageGen": "Codex ImageGen",
    "CodexExecText": "Codex Text",
}

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
