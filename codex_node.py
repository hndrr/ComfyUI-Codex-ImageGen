from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

try:
    from .codex_runner import CodexRunner
    from .image_io import empty_image_tensor, load_image_tensor_from_path
    from .media_io import append_media_notes, prepare_media_references
    from .path_utils import cleanup_generated_source_image
    from .prompt_builder import ALLOWED_ASPECT_RATIOS
    from .skill_registry import image_skill_options, validate_skill_selection
except ImportError:
    from codex_runner import CodexRunner
    from image_io import empty_image_tensor, load_image_tensor_from_path
    from media_io import append_media_notes, prepare_media_references
    from path_utils import cleanup_generated_source_image
    from prompt_builder import ALLOWED_ASPECT_RATIOS
    from skill_registry import image_skill_options, validate_skill_selection


MAX_CONCURRENCY = 8
CONCURRENCY_OPTIONS = [str(value) for value in range(1, MAX_CONCURRENCY + 1)]
MODEL_OPTIONS = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
REASONING_EFFORT_OPTIONS = ["none", "low", "medium", "high", "xhigh", "max"]
MAX_IMAGE_INPUTS = 8


@dataclass(frozen=True)
class _GenerationRequest:
    index: int
    prompt: str
    instruction: str
    images: object | None


class CodexExecImageGen:
    CATEGORY = "Codex/ImageGen"
    FUNCTION = "execute"
    OUTPUT_IS_LIST = (True, False, False, False, False, False, False)
    RETURN_TYPES = ("IMAGE", "STRING", "STRING", "STRING", "STRING", "INT", "BOOLEAN")
    RETURN_NAMES = (
        "generated_image",
        "generated_image_path",
        "last_message",
        "raw_jsonl",
        "used_prompt",
        "exit_code",
        "success",
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True}),
                "instruction": ("STRING", {"multiline": True, "default": ""}),
                "skill": (image_skill_options(), {"default": "auto"}),
                "concurrency_count": (CONCURRENCY_OPTIONS, {"default": "1"}),
                "aspect_ratio": (list(ALLOWED_ASPECT_RATIOS), {"default": "none"}),
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
                "auto_save_to_output": ("BOOLEAN", {"default": False}),
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
        concurrency_count="1",
        aspect_ratio="none",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        working_directory="",
        json_output=True,
        auto_save_to_output=False,
        output_last_message_path="",
        skip_git_repo_check=True,
        ephemeral=True,
        cache_mode="reuse",
        **kwargs,
    ):
        selected_skill = validate_skill_selection(skill, allow_imagegen=True)
        prepared_media = prepare_media_references(
            [
                images,
                *[kwargs.get(f"image_{index}") for index in range(2, MAX_IMAGE_INPUTS + 1)],
            ],
            audio=kwargs.get("audio"),
            video=kwargs.get("video"),
        )
        effective_prompt = append_media_notes(str(prompt), prepared_media.prompt_notes)
        requests = _build_generation_requests(
            effective_prompt,
            prepared_media.image_inputs,
            concurrency_count,
            instruction=instruction,
        )
        results = _run_generation_requests(
            requests,
            aspect_ratio=aspect_ratio,
            model=model,
            reasoning_effort=reasoning_effort,
            working_directory=working_directory,
            json_output=json_output,
            auto_save_to_output=auto_save_to_output,
            output_last_message_path=output_last_message_path,
            skip_git_repo_check=skip_git_repo_check,
            ephemeral=ephemeral,
            skill=selected_skill,
        )
        return _build_node_output(results, auto_save_to_output)


def _build_generation_requests(prompt, images, concurrency_count, instruction="") -> list[_GenerationRequest]:
    count = int(concurrency_count)
    if count < 1 or count > MAX_CONCURRENCY:
        raise ValueError(f"concurrency_count must be between 1 and {MAX_CONCURRENCY}.")
    if not str(prompt or "").strip():
        raise ValueError("prompt is required.")
    return [
        _GenerationRequest(
            index=index,
            prompt=str(prompt),
            instruction=str(instruction or ""),
            images=images,
        )
        for index in range(1, count + 1)
    ]


def _run_generation_requests(requests: list[_GenerationRequest], **options):
    results = [None] * len(requests)
    with ThreadPoolExecutor(max_workers=len(requests)) as executor:
        futures = {
            executor.submit(_run_single_generation, request, **options): request
            for request in requests
        }
        for future in as_completed(futures):
            request = futures[future]
            try:
                results[request.index - 1] = future.result()
            except Exception as exc:
                raise RuntimeError(f"Generation task {request.index} failed: {exc}") from exc
    return results


def _run_single_generation(request: _GenerationRequest, **options):
    output_last_message_path = options["output_last_message_path"]
    if str(output_last_message_path or "").strip():
        output_last_message_path = _indexed_path(output_last_message_path, request.index)

    result = CodexRunner().run(
        prompt=request.prompt,
        instruction=request.instruction,
        mode="imagegen",
        skill=options["skill"],
        images=request.images,
        aspect_ratio=options["aspect_ratio"],
        model=options["model"],
        reasoning_effort=options["reasoning_effort"],
        working_directory=options["working_directory"],
        json_output=options["json_output"],
        auto_save_to_output=options["auto_save_to_output"],
        output_last_message_path=output_last_message_path,
        sandbox_mode="danger-full-access",
        skip_git_repo_check=options["skip_git_repo_check"],
        ephemeral=options["ephemeral"],
    )
    if not result.success:
        raise RuntimeError(
            f"Codex exec failed with exit code {result.exit_code}: {result.last_message}"
        )
    if not result.generated_image_path:
        hint = _build_no_image_hint(result.last_message)
        raise RuntimeError(
            "No generated image was found. "
            f"Codex exit code: {result.exit_code}. Last message: {result.last_message}{hint}"
        )
    return result


def _build_node_output(results, auto_save_to_output):
    generated_images = []
    generated_image_paths = []
    last_messages = []
    raw_jsonl_values = []
    used_prompts = []
    success_values = []

    for result in results:
        generated_image_path = result.generated_image_path
        generated_images.append(
            load_image_tensor_from_path(generated_image_path)
            if generated_image_path
            else empty_image_tensor()
        )
        if not auto_save_to_output:
            cleanup_generated_source_image(generated_image_path)
            generated_image_path = ""
        generated_image_paths.append(generated_image_path)
        last_messages.append(result.last_message)
        raw_jsonl_values.append(result.raw_jsonl)
        used_prompts.append(result.used_prompt)
        success_values.append(result.success)

    return (
        generated_images,
        "\n".join(path for path in generated_image_paths if path),
        _join_indexed(last_messages),
        _join_indexed(raw_jsonl_values),
        _join_indexed(used_prompts),
        0 if all(success_values) else 1,
        all(success_values),
    )


def _join_indexed(values: list[str]) -> str:
    if len(values) == 1:
        return values[0]
    return "\n\n".join(f"[{index}] {value}" for index, value in enumerate(values, start=1))


def _indexed_path(path: str, index: int) -> str:
    if index == 1:
        return path
    value = Path(path).expanduser()
    return str(value.with_name(f"{value.stem}_{index}{value.suffix}"))


def _build_no_image_hint(last_message: str) -> str:
    text = last_message or ""
    blocked_markers = ("只读沙箱", "网络受限", "network", "read-only", "sandbox", "策略拦截")
    if any(marker.lower() in text.lower() for marker in blocked_markers):
        return (
            "\nHint: image generation needs write and network access. "
            "The node already runs Codex with --sandbox danger-full-access. "
            "If it is still blocked, start ComfyUI from an environment where Codex CLI has network access."
        )
    return ""
