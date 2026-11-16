IMAGEGEN_PREFIX = "$imagegen"
AUTO_SKILL = "auto"
IMAGEGEN_MODE = "imagegen"
TEXT_MODE = "text"
ALLOWED_RUN_MODES = (IMAGEGEN_MODE, TEXT_MODE)
ASPECT_RATIO_NONE = "none"
ALLOWED_ASPECT_RATIOS = (ASPECT_RATIO_NONE, "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3")


def build_imagegen_prompt(user_prompt: str, aspect_ratio: str = ASPECT_RATIO_NONE) -> str:
    prompt = (user_prompt or "").strip()
    if aspect_ratio not in ALLOWED_ASPECT_RATIOS:
        raise ValueError(f"Unsupported aspect_ratio: {aspect_ratio}")
    if prompt.startswith(IMAGEGEN_PREFIX):
        return prompt

    if aspect_ratio != ASPECT_RATIO_NONE:
        prompt = f"画面比例 {aspect_ratio}。{prompt}"
    return f"{IMAGEGEN_PREFIX} {prompt}"


def build_codex_prompt(
    user_prompt: str,
    *,
    mode: str = IMAGEGEN_MODE,
    skill: str = AUTO_SKILL,
    aspect_ratio: str = ASPECT_RATIO_NONE,
) -> str:
    selected_skill = (skill or AUTO_SKILL).strip()
    if mode not in ALLOWED_RUN_MODES:
        raise ValueError(f"Unsupported Codex run mode: {mode}")

    if mode == IMAGEGEN_MODE:
        prompt = build_imagegen_prompt(user_prompt, aspect_ratio)
        if selected_skill in (AUTO_SKILL, "imagegen"):
            return prompt
        return _prepend_skill(prompt, selected_skill)

    if aspect_ratio != ASPECT_RATIO_NONE:
        raise ValueError("aspect_ratio is only supported in imagegen mode.")
    if selected_skill == "imagegen":
        raise ValueError("The imagegen skill is not supported in text mode.")
    prompt = (user_prompt or "").strip()
    if selected_skill == AUTO_SKILL:
        return prompt
    return _prepend_skill(prompt, selected_skill)


def _prepend_skill(prompt: str, skill: str) -> str:
    marker = f"${skill}"
    if prompt == marker or prompt.startswith(marker + "\n") or prompt.startswith(marker + " "):
        return prompt
    return f"{marker}\n\n{prompt}"
