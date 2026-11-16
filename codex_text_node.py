from __future__ import annotations

try:
    from .codex_node import MAX_IMAGE_INPUTS, MODEL_OPTIONS, REASONING_EFFORT_OPTIONS
    from .codex_runner import CodexRunner
    from .media_io import append_media_notes, prepare_media_references
    from .skill_registry import text_skill_options, validate_skill_selection
except ImportError:
    from codex_node import MAX_IMAGE_INPUTS, MODEL_OPTIONS, REASONING_EFFORT_OPTIONS
    from codex_runner import CodexRunner
    from media_io import append_media_notes, prepare_media_references
    from skill_registry import text_skill_options, validate_skill_selection


class CodexExecText:
    CATEGORY = "Codex"
    FUNCTION = "execute"
    RETURN_TYPES = ("STRING", "STRING", "STRING", "INT", "BOOLEAN")
    RETURN_NAMES = ("text", "raw_jsonl", "used_prompt", "exit_code", "success")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True}),
                "instruction": ("STRING", {"multiline": True, "default": ""}),
                "skill": (text_skill_options(), {"default": "auto"}),
                "model": (MODEL_OPTIONS, {"default": "gpt-5.6-sol"}),
                "reasoning_effort": (REASONING_EFFORT_OPTIONS, {"default": "medium"}),
            },
            "optional": {
                "images": ("IMAGE",),
                **{
                    f"image_{index}": ("IMAGE",)
                    for index in range(2, MAX_IMAGE_INPUTS + 1)
                },
                "audio": ("AUDIO",),
                "video": ("VIDEO",),
                "working_directory": ("STRING", {"default": ""}),
                "json_output": ("BOOLEAN", {"default": True}),
                "output_last_message_path": ("STRING", {"default": ""}),
                "skip_git_repo_check": ("BOOLEAN", {"default": True}),
                "ephemeral": ("BOOLEAN", {"default": True}),
                "cache_mode": (["reuse", "always_run"], {"default": "reuse"}),
            },
        }

    @classmethod
    def IS_CHANGED(cls, cache_mode="reuse", *args, **kwargs):
        return float("NaN") if cache_mode == "always_run" else False

    def execute(
        self,
        prompt,
        instruction="",
        skill="auto",
        images=None,
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        working_directory="",
        json_output=True,
        output_last_message_path="",
        skip_git_repo_check=True,
        ephemeral=True,
        cache_mode="reuse",
        **kwargs,
    ):
        if not str(prompt or "").strip():
            raise ValueError("prompt is required.")
        selected_skill = validate_skill_selection(skill, allow_imagegen=False)
        prepared_media = prepare_media_references(
            [
                images,
                *[kwargs.get(f"image_{index}") for index in range(2, MAX_IMAGE_INPUTS + 1)],
            ],
            audio=kwargs.get("audio"),
            video=kwargs.get("video"),
        )
        effective_prompt = append_media_notes(str(prompt), prepared_media.prompt_notes)
        result = CodexRunner().run(
            prompt=effective_prompt,
            instruction=str(instruction or ""),
            mode="text",
            skill=selected_skill,
            images=prepared_media.image_inputs,
            model=model,
            reasoning_effort=reasoning_effort,
            working_directory=working_directory,
            json_output=json_output,
            output_last_message_path=output_last_message_path,
            sandbox_mode="danger-full-access",
            skip_git_repo_check=skip_git_repo_check,
            ephemeral=ephemeral,
        )
        return (
            result.last_message,
            result.raw_jsonl,
            result.used_prompt,
            result.exit_code,
            result.success,
        )
