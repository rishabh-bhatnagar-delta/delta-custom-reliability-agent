"""Loads prompt templates from the prompts/ directory."""

import os

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "prompts")


def load_prompt(name: str) -> str:
    """Load a prompt file by name (without extension)."""
    filepath = os.path.join(_PROMPTS_DIR, f"{name}.md")
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


REPORT_GENERATION_PROMPT = load_prompt("report_generation")
