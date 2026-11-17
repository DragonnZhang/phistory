from __future__ import annotations

from claude_tap.prompt_snapshot import PromptSnapshot


def matches_prompt_surface(
    snapshot: PromptSnapshot,
    *,
    minimum_tools: int = 0,
    required_system_text: tuple[str, ...] = (),
    required_user_text: tuple[str, ...] = (),
) -> bool:
    instructions = snapshot.system_prompt + "\n" + snapshot.developer_prompt
    return (
        len(snapshot.tools) >= minimum_tools
        and all(marker in instructions for marker in required_system_text)
        and all(marker in snapshot.user_message for marker in required_user_text)
    )
