import json
import math
import sys
from pathlib import Path

import pytest
from PIL import Image

from auth_manager import LoginResult
from auth_manager import resolve_codex_binary
from auth_manager import build_codex_environment
from codex_node import CodexExecImageGen
from codex_text_node import CodexExecText
from codex_runner import CodexRunner, RunnerOptions, build_codex_exec_command
from image_io import save_image_inputs_to_comfy_input, save_image_tensor_to_comfy_input
from media_io import append_media_notes, audio_to_spectrogram_reference, prepare_media_references
from output_parser import parse_generated_image_path, parse_last_agent_message
from path_utils import cleanup_generated_source_image, cleanup_task_dir, copy_generated_image_to_output, create_task_dir, parse_image_paths
from progress import progress_for_jsonl_event
from prompt_builder import build_codex_prompt, build_imagegen_prompt
from skill_registry import discover_skill_names, text_skill_options


def test_prompt_builder_forces_imagegen_prefix():
    assert build_imagegen_prompt("make a red cube") == "$imagegen make a red cube"
    assert build_imagegen_prompt("$imagegen\nmake a red cube") == "$imagegen\nmake a red cube"
    assert build_imagegen_prompt("make a red cube", "16:9") == (
        "$imagegen 画面比例 16:9。make a red cube"
    )


def test_prompt_builder_explicitly_adds_selected_skill_by_mode():
    assert build_codex_prompt("draw", mode="imagegen", skill="imagegen") == "$imagegen draw"
    assert build_codex_prompt("draw", mode="imagegen", skill="my-style") == (
        "$my-style\n\n$imagegen draw"
    )
    assert build_codex_prompt("summarize", mode="text", skill="auto") == "summarize"
    assert build_codex_prompt("summarize", mode="text", skill="my-review") == (
        "$my-review\n\nsummarize"
    )
    with pytest.raises(ValueError, match="imagegen skill"):
        build_codex_prompt("summarize", mode="text", skill="imagegen")


def test_skill_registry_discovers_system_and_user_skills(tmp_path):
    codex_home = tmp_path / "codex"
    system_skill = codex_home / "skills" / ".system" / "system-helper"
    user_skill = tmp_path / ".agents" / "skills" / "user-helper"
    invalid_skill = tmp_path / ".agents" / "skills" / "invalid"
    for path in (system_skill, user_skill, invalid_skill):
        path.mkdir(parents=True)
    (system_skill / "SKILL.md").write_text(
        "---\nname: system-helper\ndescription: test\n---\n",
        encoding="utf-8",
    )
    (user_skill / "SKILL.md").write_text(
        "---\nname: user-helper\ndescription: test\n---\n",
        encoding="utf-8",
    )
    (invalid_skill / "SKILL.md").write_text(
        "---\nname: invalid skill name\ndescription: test\n---\n",
        encoding="utf-8",
    )

    names = discover_skill_names(user_home=tmp_path, codex_homes=[codex_home])

    assert names[0] == "auto"
    assert "imagegen" in names
    assert "system-helper" in names
    assert "user-helper" in names
    assert "invalid skill name" not in names


def test_developer_instruction_is_a_separate_codex_config_value():
    command = build_codex_exec_command(
        RunnerOptions(
            prompt="Change the outfit.",
            developer_instructions='Keep the "face" identical.\nPreserve identity.',
        )
    )

    assert (
        'developer_instructions="Keep the \\"face\\" identical.\\nPreserve identity."'
        in command
    )
    assert command[-1] == "$imagegen Change the outfit."


def test_resolve_codex_binary_uses_env_path(tmp_path, monkeypatch):
    codex = tmp_path / "codex"
    codex.write_text("#!/bin/sh\n", encoding="utf-8")
    codex.chmod(0o755)
    monkeypatch.setenv("CODEX_CLI_PATH", str(codex))

    assert resolve_codex_binary("codex") == str(codex)


def test_resolve_codex_binary_keeps_explicit_path(tmp_path, monkeypatch):
    explicit = tmp_path / "custom-codex"
    explicit.write_text("#!/bin/sh\n", encoding="utf-8")
    explicit.chmod(0o755)
    monkeypatch.setenv("CODEX_CLI_PATH", str(tmp_path / "other"))

    assert resolve_codex_binary(str(explicit)) == str(explicit)


def test_build_codex_environment_sets_isolated_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "user_codex"))

    env = build_codex_environment(tmp_path / "isolated_codex")

    assert env["CODEX_HOME"] == str(tmp_path / "isolated_codex")


def test_progress_for_jsonl_event_maps_codex_stages():
    assert progress_for_jsonl_event('{"type":"thread.started"}') == 20
    assert progress_for_jsonl_event('{"type":"turn.started"}') == 35
    assert (
        progress_for_jsonl_event(
            '{"type":"item.completed","item":{"type":"agent_message","text":"使用 imagegen 生成"}}'
        )
        == 55
    )
    assert (
        progress_for_jsonl_event(
            '{"type":"item.started","item":{"type":"command_execution","status":"in_progress"}}'
        )
        == 70
    )
    assert progress_for_jsonl_event('{"type":"turn.completed"}') == 90


def test_parse_image_paths_rejects_missing_file(tmp_path):
    existing = tmp_path / "input.png"
    existing.write_bytes(b"png")

    with pytest.raises(FileNotFoundError):
        parse_image_paths(f"{existing}\n{tmp_path / 'missing.png'}")


def test_build_codex_command_uses_only_supported_flags(tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    last_message = tmp_path / "last_message.txt"
    options = RunnerOptions(
        prompt="draw",
        developer_instructions="Preserve identity.",
        image_paths=[image],
        aspect_ratio="16:9",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        working_directory=tmp_path,
        json_output=True,
        output_last_message_path=last_message,
        sandbox_mode="workspace-write",
        skip_git_repo_check=True,
        ephemeral=True,
    )

    command = build_codex_exec_command(options)

    assert command[:3] == ["codex", "exec", "--cd"]
    assert "--cd" in command
    assert "--json" in command
    assert "--ignore-user-config" not in command
    assert "--ignore-rules" not in command
    assert "--image" in command
    assert str(image) in command
    assert "-m" in command
    assert "gpt-5.6-sol" in command
    assert "-c" in command
    assert "model_reasoning_effort=high" in command
    assert 'developer_instructions="Preserve identity."' in command
    assert "-o" in command
    assert str(last_message) in command
    assert "--skip-git-repo-check" in command
    assert "--ephemeral" in command
    assert "danger-full-access" not in command
    assert "--aspect-ratio" not in command
    assert "aspect_ratio" not in command
    assert not {"seed", "steps", "cfg", "sampler", "scheduler", "checkpoint"} & set(command)
    assert command[-1] == "$imagegen 画面比例 16:9。draw"


def test_build_codex_command_text_mode_never_adds_imagegen():
    command = build_codex_exec_command(
        RunnerOptions(prompt="answer briefly", mode="text", skill="openai-docs")
    )

    assert command[-1] == "$openai-docs\n\nanswer briefly"
    assert "$imagegen" not in command[-1]


def test_task_dir_does_not_create_plugin_input_or_output_dirs(tmp_path):
    task_dir = create_task_dir(tmp_path / "runtime")

    assert task_dir.exists()
    assert not (task_dir / "inputs").exists()
    assert not (task_dir / "outputs").exists()


def test_parse_generated_image_path_prefers_last_message_path(tmp_path):
    generated = tmp_path / "output.png"
    generated.write_bytes(b"png")

    parsed = parse_generated_image_path(f"Saved image to {generated}", tmp_path, tmp_path)

    assert parsed == str(generated)


def test_parse_generated_image_path_scans_comfy_output_directory(tmp_path):
    generated = tmp_path / "ComfyUI_00001_.png"
    generated.write_bytes(b"png")

    parsed = parse_generated_image_path("", tmp_path / "runtime_task", tmp_path)

    assert parsed == str(generated)


def test_parse_generated_image_path_scans_codex_generated_images(tmp_path):
    codex_generated = tmp_path / "codex_home" / "generated_images" / "thread"
    codex_generated.mkdir(parents=True)
    generated = codex_generated / "ig_test.png"
    generated.write_bytes(b"png")

    parsed = parse_generated_image_path(
        "",
        tmp_path / "runtime_task",
        tmp_path / "output",
        additional_output_dirs=[tmp_path / "codex_home" / "generated_images"],
    )

    assert parsed == str(generated)


def test_parse_generated_image_path_ignores_old_output_files(tmp_path):
    generated = tmp_path / "ComfyUI_00001_.png"
    generated.write_bytes(b"png")
    old_time = generated.stat().st_mtime

    parsed = parse_generated_image_path("", tmp_path / "runtime_task", tmp_path, min_mtime=old_time + 1)

    assert parsed == ""


def test_parse_last_agent_message_from_jsonl():
    raw_jsonl = "\n".join(
        [
            '{"type":"thread.started","thread_id":"abc"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"OPENAI_API_KEY MISSING"}}',
        ]
    )

    assert parse_last_agent_message(raw_jsonl) == "OPENAI_API_KEY MISSING"


def test_parse_last_agent_message_ignores_empty_messages():
    raw_jsonl = "\n".join(
        [
            '{"type":"item.completed","item":{"type":"agent_message","text":"generated"}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":""}}',
        ]
    )

    assert parse_last_agent_message(raw_jsonl) == "generated"


def test_save_image_tensor_uses_comfy_input_directory(tmp_path):
    image = pytest.importorskip("numpy").ones((1, 2, 2, 3), dtype="float32")

    saved = save_image_tensor_to_comfy_input(image, tmp_path / "input", "codex_test")

    assert len(saved) == 1
    assert saved[0].parent == tmp_path / "input"
    assert saved[0].suffix == ".png"
    assert saved[0].exists()


def test_save_image_inputs_supports_multiple_ports_and_batches(tmp_path):
    numpy = pytest.importorskip("numpy")
    first = numpy.ones((1, 2, 3, 3), dtype="float32")
    second = numpy.ones((2, 4, 5, 3), dtype="float32")

    saved = save_image_inputs_to_comfy_input([first, second], tmp_path / "input", "codex_test")

    assert len(saved) == 3
    assert len({path.name for path in saved}) == 3
    assert all(path.exists() for path in saved)


def test_copy_generated_image_to_comfy_output_and_cleanup_source(tmp_path):
    source = tmp_path / "codex_home" / "generated_images" / "thread" / "ig_test.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png")
    output_dir = tmp_path / "output"

    copied = copy_generated_image_to_output(source, output_dir, "task_001")

    assert copied.parent == output_dir
    assert copied.name == "codex_imagegen_task_001_ig_test.png"
    assert copied.read_bytes() == b"png"
    assert not source.exists()
    assert not source.parent.exists()


def test_copy_generated_image_does_not_remove_non_empty_thread_dir(tmp_path):
    source = tmp_path / "codex_home" / "generated_images" / "thread" / "ig_test.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png")
    sibling = source.parent / "other.png"
    sibling.write_bytes(b"other")
    output_dir = tmp_path / "output"

    copy_generated_image_to_output(source, output_dir, "task_001")

    assert not source.exists()
    assert sibling.exists()
    assert source.parent.exists()


def test_cleanup_generated_source_image_only_removes_codex_generated_file(tmp_path):
    source = tmp_path / "codex_home" / "generated_images" / "thread" / "ig_test.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png")
    output_file = tmp_path / "output" / "ig_test.png"
    output_file.parent.mkdir()
    output_file.write_bytes(b"png")

    cleanup_generated_source_image(source)
    cleanup_generated_source_image(output_file)

    assert not source.exists()
    assert not source.parent.exists()
    assert output_file.exists()


def test_cleanup_task_dir_removes_successful_run_files(tmp_path):
    task_dir = create_task_dir(tmp_path / "runtime")
    (task_dir / "stdout.jsonl").write_text("{}", encoding="utf-8")

    cleanup_task_dir(task_dir)

    assert not task_dir.exists()


def test_runner_returns_structured_failure_when_codex_missing(tmp_path):
    runner = CodexRunner(
        codex_binary="codex-does-not-exist",
        auth_manager=FakeAuthManager(LoginResult(True, "ok")),
        runtime_dir=tmp_path / "runtime",
        comfy_input_dir=tmp_path / "input",
        comfy_output_dir=tmp_path / "output",
    )

    result = runner.run(prompt="draw", working_directory=str(tmp_path), timeout_seconds=1)

    assert result.success is False
    assert result.exit_code == 127
    assert "not installed" in result.last_message


def test_runner_decodes_codex_streams_as_utf8_on_windows(tmp_path):
    runner = CodexRunner(
        codex_binary="codex",
        runtime_dir=tmp_path / "runtime",
        comfy_input_dir=tmp_path / "input",
        comfy_output_dir=tmp_path / "output",
    )
    stdout_path = tmp_path / "stdout.jsonl"
    stderr_path = tmp_path / "stderr.txt"

    exit_code = runner._run_process(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "sys.stdout.buffer.write('画像を生成しました'.encode('utf-8')); "
                "sys.stderr.buffer.write('警告'.encode('utf-8'))"
            ),
        ],
        stdout_path,
        stderr_path,
        10,
        FakeProgress(),
    )

    assert exit_code == 0
    assert stdout_path.read_text(encoding="utf-8") == "画像を生成しました"
    assert stderr_path.read_text(encoding="utf-8") == "警告"


def test_runner_defaults_working_directory_to_comfy_output_dir(tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    runner = CodexRunner(
        codex_binary="codex-does-not-exist",
        auth_manager=FakeAuthManager(LoginResult(True, "ok")),
        runtime_dir=tmp_path / "runtime",
        comfy_input_dir=tmp_path / "input",
        comfy_output_dir=output_dir,
    )

    result = runner.run(prompt="draw", timeout_seconds=1)
    meta = json.loads((Path(result.task_dir) / "meta.json").read_text(encoding="utf-8"))

    assert meta["working_directory"] == str(output_dir)
    assert meta["codex_home"] == str(tmp_path / "runtime" / "codex_home")


def test_runner_text_mode_cleans_successful_task_directory(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    last_message_path = tmp_path / "text_result.txt"
    runner = CodexRunner(
        codex_binary="codex",
        auth_manager=FakeAuthManager(LoginResult(True, "ok")),
        runtime_dir=tmp_path / "runtime",
        comfy_input_dir=tmp_path / "input",
        comfy_output_dir=output_dir,
    )

    def fake_run_process(command, stdout_path, stderr_path, timeout_seconds, progress):
        last_message_path.write_text("plain text result", encoding="utf-8")
        stdout_path.write_text(
            '{"type":"item.completed","item":{"type":"agent_message","text":"plain text result"}}',
            encoding="utf-8",
        )
        stderr_path.write_text("", encoding="utf-8")
        return 0

    monkeypatch.setattr(runner, "_run_process", fake_run_process)

    result = runner.run(
        prompt="answer",
        mode="text",
        working_directory=str(output_dir),
        output_last_message_path=str(last_message_path),
    )

    assert result.success is True
    assert result.last_message == "plain text result"
    assert result.generated_image_path == ""
    assert not Path(result.task_dir).exists()


def test_node_contract_matches_plan():
    inputs = CodexExecImageGen.INPUT_TYPES()

    assert list(inputs["required"]) == [
        "prompt",
        "instruction",
        "skill",
        "concurrency_count",
        "aspect_ratio",
        "model",
        "reasoning_effort",
    ]
    assert inputs["required"]["concurrency_count"] == (
        ["1", "2", "3", "4", "5", "6", "7", "8"],
        {"default": "1"},
    )
    assert inputs["required"]["prompt"] == ("STRING", {"multiline": True})
    assert inputs["required"]["instruction"] == (
        "STRING",
        {"multiline": True, "default": ""},
    )
    assert inputs["required"]["skill"][0][0] == "auto"
    assert "imagegen" in inputs["required"]["skill"][0]
    assert inputs["optional"]["images"] == ("IMAGE",)
    assert inputs["optional"]["image_8"] == ("IMAGE",)
    assert inputs["optional"]["audio"] == ("AUDIO",)
    assert inputs["optional"]["video"] == ("VIDEO",)
    assert inputs["required"]["aspect_ratio"] == (
        ["none", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"],
        {"default": "none"},
    )
    assert inputs["required"]["model"] == (
        ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
        {"default": "gpt-5.6-sol"},
    )
    assert inputs["required"]["reasoning_effort"] == (
        ["none", "low", "medium", "high", "xhigh", "max"],
        {"default": "medium"},
    )
    assert inputs["optional"]["auto_save_to_output"] == ("BOOLEAN", {"default": False})
    assert inputs["optional"]["cache_mode"] == (
        ["reuse", "always_run"],
        {"default": "reuse"},
    )
    assert "prompt_2" not in inputs["optional"]
    assert "images_2" not in inputs["optional"]
    assert "sandbox_mode" not in inputs["optional"]
    assert CodexExecImageGen.OUTPUT_IS_LIST == (True, False, False, False, False, False, False)
    assert CodexExecImageGen.RETURN_TYPES == (
        "IMAGE",
        "STRING",
        "STRING",
        "STRING",
        "STRING",
        "INT",
        "BOOLEAN",
    )
    assert CodexExecImageGen.RETURN_NAMES == (
        "generated_image",
        "generated_image_path",
        "last_message",
        "raw_jsonl",
        "used_prompt",
        "exit_code",
        "success",
    )
    assert not hasattr(CodexExecImageGen, "NOT_IDEMPOTENT")
    assert CodexExecImageGen.IS_CHANGED(cache_mode="reuse") is False
    assert math.isnan(CodexExecImageGen.IS_CHANGED(cache_mode="always_run"))


def test_text_node_contract_matches_plan():
    inputs = CodexExecText.INPUT_TYPES()

    assert list(inputs["required"]) == [
        "prompt",
        "instruction",
        "skill",
        "model",
        "reasoning_effort",
    ]
    assert inputs["required"]["skill"][0][0] == "auto"
    assert "imagegen" not in inputs["required"]["skill"][0]
    assert inputs["optional"]["images"] == ("IMAGE",)
    assert inputs["optional"]["image_8"] == ("IMAGE",)
    assert inputs["optional"]["audio"] == ("AUDIO",)
    assert inputs["optional"]["video"] == ("VIDEO",)
    assert inputs["optional"]["cache_mode"] == (
        ["reuse", "always_run"],
        {"default": "reuse"},
    )
    assert "concurrency_count" not in inputs["required"]
    assert "aspect_ratio" not in inputs["required"]
    assert CodexExecText.RETURN_TYPES == ("STRING", "STRING", "STRING", "INT", "BOOLEAN")
    assert CodexExecText.RETURN_NAMES == (
        "text",
        "raw_jsonl",
        "used_prompt",
        "exit_code",
        "success",
    )
    assert not hasattr(CodexExecText, "NOT_IDEMPOTENT")
    assert CodexExecText.IS_CHANGED(cache_mode="reuse") is False
    assert math.isnan(CodexExecText.IS_CHANGED(cache_mode="always_run"))


def test_node_raises_when_runner_fails(monkeypatch):
    class FakeRunner:
        def __init__(self, codex_binary="codex"):
            pass

        def run(self, **kwargs):
            return FakeResult(
                generated_image_path="",
                last_message="Codex CLI is not installed or not in PATH.",
                exit_code=127,
                success=False,
            )

    monkeypatch.setattr("codex_node.CodexRunner", FakeRunner)

    with pytest.raises(RuntimeError, match="Codex CLI is not installed"):
        CodexExecImageGen().execute("draw")


def test_node_raises_when_no_generated_image(monkeypatch):
    class FakeRunner:
        def __init__(self, codex_binary="codex"):
            pass

        def run(self, **kwargs):
            return FakeResult(
                generated_image_path="",
                last_message="Finished without a parsed image.",
                exit_code=0,
                success=True,
            )

    monkeypatch.setattr("codex_node.CodexRunner", FakeRunner)

    with pytest.raises(RuntimeError, match="No generated image was found"):
        CodexExecImageGen().execute("draw")


def test_node_suggests_danger_full_access_for_sandbox_network_block(monkeypatch):
    class FakeRunner:
        def __init__(self, codex_binary="codex"):
            pass

        def run(self, **kwargs):
            return FakeResult(
                generated_image_path="",
                last_message="当前环境是只读沙箱，且网络受限，调用 OpenAI Image API 被策略拦截",
                exit_code=0,
                success=True,
            )

    monkeypatch.setattr("codex_node.CodexRunner", FakeRunner)

    with pytest.raises(RuntimeError, match="danger-full-access"):
        CodexExecImageGen().execute("draw")


def test_node_cleans_codex_generated_image_when_not_auto_saving(tmp_path, monkeypatch):
    generated = tmp_path / "runtime" / "codex_home" / "generated_images" / "thread" / "ig_test.png"
    generated.parent.mkdir(parents=True)
    Image.new("RGB", (2, 3), "red").save(generated)

    class FakeRunner:
        def __init__(self, codex_binary="codex"):
            pass

        def run(self, **kwargs):
            assert kwargs["auto_save_to_output"] is False
            return FakeResult(
                generated_image_path=str(generated),
                last_message="generated",
                exit_code=0,
                success=True,
            )

    monkeypatch.setattr("codex_node.CodexRunner", FakeRunner)

    image, generated_path, *_ = CodexExecImageGen().execute("draw")

    assert len(image) == 1
    assert image[0].shape[1:3] == (3, 2)
    assert generated_path == ""
    assert not generated.exists()
    assert not generated.parent.exists()


def test_node_runs_same_prompt_concurrently_with_shared_references(tmp_path, monkeypatch):
    generated_paths = []
    calls = []
    numpy = pytest.importorskip("numpy")
    first_reference = numpy.ones((1, 2, 2, 3), dtype="float32")
    second_reference = numpy.ones((1, 3, 3, 3), dtype="float32")

    class FakeRunner:
        def __init__(self, codex_binary="codex"):
            pass

        def run(self, **kwargs):
            index = len(calls) + 1
            calls.append(kwargs)
            generated = tmp_path / "runtime" / "codex_home" / "generated_images" / f"thread_{index}" / f"ig_{index}.png"
            generated.parent.mkdir(parents=True)
            Image.new("RGB", (index + 1, index + 2), "red").save(generated)
            generated_paths.append(generated)
            return FakeResult(
                generated_image_path=str(generated),
                last_message=f"generated {index}",
                exit_code=0,
                success=True,
                used_prompt=f"$imagegen draw {index}",
            )

    monkeypatch.setattr("codex_node.CodexRunner", FakeRunner)

    image, generated_path, last_message, _, used_prompt, exit_code, success = CodexExecImageGen().execute(
        "draw",
        instruction="Preserve identity.",
        images=first_reference,
        image_2=second_reference,
        concurrency_count="2",
    )

    assert len(image) == 2
    assert tuple(image[0].shape) == (1, 3, 2, 3)
    assert tuple(image[1].shape) == (1, 4, 3, 3)
    assert generated_path == ""
    assert "[1] generated" in last_message
    assert "[2] generated" in last_message
    assert "[1] $imagegen draw" in used_prompt
    assert exit_code == 0
    assert success is True
    assert all(call["prompt"] == "draw" for call in calls)
    assert all(call["instruction"] == "Preserve identity." for call in calls)
    assert all(call["mode"] == "imagegen" for call in calls)
    assert all(call["skill"] == "auto" for call in calls)
    assert all(
        call["images"][0] is first_reference and call["images"][1] is second_reference
        for call in calls
    )
    assert all(not path.exists() for path in generated_paths)


def test_node_requires_one_prompt_for_all_concurrent_runs():
    with pytest.raises(ValueError, match="prompt is required"):
        CodexExecImageGen().execute("", concurrency_count="2")


def test_text_node_returns_text_and_diagnostics_with_shared_references(monkeypatch):
    calls = []
    numpy = pytest.importorskip("numpy")
    first_reference = numpy.ones((1, 2, 2, 3), dtype="float32")
    second_reference = numpy.ones((1, 3, 3, 3), dtype="float32")

    class FakeRunner:
        def __init__(self, codex_binary="codex"):
            pass

        def run(self, **kwargs):
            calls.append(kwargs)
            result = FakeResult(
                generated_image_path="",
                last_message="text answer",
                exit_code=0,
                success=True,
                used_prompt="describe",
            )
            result.raw_jsonl = '{"type":"turn.completed"}'
            return result

    monkeypatch.setattr("codex_text_node.CodexRunner", FakeRunner)

    output = CodexExecText().execute(
        "describe",
        instruction="Be precise.",
        images=first_reference,
        image_2=second_reference,
    )

    assert output == (
        "text answer",
        '{"type":"turn.completed"}',
        "describe",
        0,
        True,
    )
    assert calls[0]["mode"] == "text"
    assert calls[0]["skill"] == "auto"
    assert calls[0]["instruction"] == "Be precise."
    assert calls[0]["images"][0] is first_reference
    assert calls[0]["images"][1] is second_reference


def test_text_node_rejects_imagegen_and_missing_skills():
    assert "imagegen" not in text_skill_options()
    with pytest.raises(ValueError, match="imagegen skill"):
        CodexExecText().execute("describe", skill="imagegen")
    with pytest.raises(ValueError, match="not available"):
        CodexExecText().execute("describe", skill="definitely-missing-skill")


def test_audio_reference_becomes_spectrogram_and_honest_prompt_note():
    numpy = pytest.importorskip("numpy")
    sample_rate = 16_000
    time = numpy.arange(sample_rate, dtype="float32") / sample_rate
    waveform = numpy.sin(2 * numpy.pi * 440 * time)[None, None, :]

    spectrogram, duration, returned_rate = audio_to_spectrogram_reference(
        {"waveform": waveform, "sample_rate": sample_rate}
    )
    prompt = append_media_notes(
        "draw",
        prepare_media_references([], audio={"waveform": waveform, "sample_rate": sample_rate}).prompt_notes,
    )

    assert spectrogram.shape == (1, 256, 512, 3)
    assert duration == pytest.approx(1.0)
    assert returned_rate == sample_rate
    assert "spectrogram" in prompt
    assert "not available natively" in prompt


def test_video_reference_samples_at_most_four_chronological_frames():
    numpy = pytest.importorskip("numpy")

    class Components:
        images = numpy.arange(10 * 2 * 2 * 3, dtype="float32").reshape(10, 2, 2, 3)

    class FakeVideo:
        def get_components(self):
            return Components()

    prepared = prepare_media_references([], video=FakeVideo())

    assert len(prepared.image_inputs) == 1
    assert prepared.image_inputs[0].shape == (4, 2, 2, 3)
    assert "4 chronological frames" in prepared.prompt_notes[0]


class FakeAuthManager:
    def __init__(self, result):
        self.result = result

    def ensure_login(self, interactive=True):
        return self.result


class FakeProgress:
    def update_absolute(self, value, total=None, preview=None):
        pass


class FakeResult:
    def __init__(self, generated_image_path, last_message, exit_code, success, used_prompt="$imagegen draw"):
        self.generated_image_path = generated_image_path
        self.last_message = last_message
        self.raw_jsonl = ""
        self.used_prompt = used_prompt
        self.exit_code = exit_code
        self.success = success
