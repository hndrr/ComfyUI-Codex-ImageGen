from __future__ import annotations

import os
import re
from pathlib import Path


AUTO_SKILL = "auto"
IMAGEGEN_SKILL = "imagegen"
SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def discover_skill_names(
    *,
    user_home: Path | None = None,
    codex_homes: list[Path] | None = None,
) -> tuple[str, ...]:
    home = user_home or Path.home()
    roots = [home / ".agents" / "skills"]

    if codex_homes is None:
        candidates: list[Path] = []
        configured = os.environ.get("CODEX_HOME", "").strip()
        if configured:
            candidates.append(Path(configured).expanduser())
        candidates.extend(
            [
                home / ".codex",
                Path(__file__).resolve().parent / "runtime" / "codex_home",
            ]
        )
    else:
        candidates = codex_homes

    roots.extend(candidate / "skills" / ".system" for candidate in candidates)
    names = {IMAGEGEN_SKILL}
    for root in roots:
        names.update(_discover_names_under(root))
    return (AUTO_SKILL, *sorted(names, key=str.casefold))


def image_skill_options() -> list[str]:
    return list(discover_skill_names())


def text_skill_options() -> list[str]:
    return [name for name in discover_skill_names() if name != IMAGEGEN_SKILL]


def validate_skill_selection(skill: str, *, allow_imagegen: bool) -> str:
    selected = str(skill or AUTO_SKILL).strip()
    allowed = image_skill_options() if allow_imagegen else text_skill_options()
    if selected not in allowed:
        if selected == IMAGEGEN_SKILL and not allow_imagegen:
            raise ValueError("The imagegen skill is not available in Codex Text.")
        raise ValueError(
            f"Selected Codex skill is not available: {selected}. "
            "Install it under ~/.agents/skills and reload ComfyUI."
        )
    return selected


def _discover_names_under(root: Path) -> set[str]:
    if not root.exists() or not root.is_dir():
        return set()
    names: set[str] = set()
    for skill_file in root.rglob("SKILL.md"):
        name = _read_skill_name(skill_file)
        if name:
            names.add(name)
    return names


def _read_skill_name(skill_file: Path) -> str | None:
    try:
        lines = skill_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        value = line.strip()
        if value == "---":
            break
        match = re.match(r"^name\s*:\s*(.+?)\s*$", value)
        if not match:
            continue
        name = match.group(1).strip().strip('"').strip("'")
        return name if SKILL_NAME_PATTERN.fullmatch(name) else None
    return None
