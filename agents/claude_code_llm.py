"""
CrewAI 1.x 相容的 Claude Code LLM wrapper。
直接以 subprocess 呼叫 `claude -p`，繞過 claude_code_sdk 的 Windows 編碼問題。
走您的 Claude Code 訂閱，免 API key。
"""
from __future__ import annotations

import os
import subprocess

from crewai.llms.base_llm import BaseLLM


class ClaudeCodeLLM(BaseLLM):
    llm_type: str = "claude_code"
    # BaseLLM 是 pydantic-style;這裡用 object.__setattr__ 強塞,
    # 在 class body 標型別讓 pyright/IDE 認得屬性。
    _max_turns: int
    _timeout: int

    def __init__(
        self,
        model: str = "sonnet",
        max_turns: int = 1,
        timeout: int = 300,
        **kw,
    ):
        super().__init__(model=model, provider="anthropic", **kw)
        object.__setattr__(self, "_max_turns", max_turns)
        object.__setattr__(self, "_timeout", timeout)

    def _render_prompt(self, messages) -> str:
        if isinstance(messages, str):
            return messages
        parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                parts.append(f"[SYSTEM]\n{content}")
            elif role == "assistant":
                parts.append(f"[ASSISTANT]\n{content}")
            else:
                parts.append(f"[USER]\n{content}")
        return "\n\n".join(parts)

    def call(
        self,
        messages,
        tools=None,
        callbacks=None,
        available_functions=None,
        from_task=None,
        from_agent=None,
        response_model=None,
    ):
        prompt = self._render_prompt(messages)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        # prompt 經 stdin 傳入，避免 Windows 命令列長度上限
        # --tools "" 讓 Claude Code 當純 LLM 用（不開任何工具）；保留 OAuth 以走訂閱
        cmd = [
            "claude",
            "-p",
            "--model",
            self.model,
            "--output-format",
            "text",
            "--tools",
            "",
            "--append-system-prompt",
            "You are serving as a pure language model. Respond to the user's message directly without using any tools. Reply in the language the user used.",
        ]

        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            return "[LLM timeout]"

        if result.returncode != 0:
            err_snip = (result.stderr or result.stdout or "").strip()[:500]
            return f"[LLM error rc={result.returncode}] {err_snip}"
        return result.stdout.strip()

    def supports_function_calling(self) -> bool:
        return False

    def supports_stop_words(self) -> bool:
        return False

    def get_context_window_size(self) -> int:
        return 180000
