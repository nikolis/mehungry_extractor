"""LLM extraction of phase-tagged dietary recommendations from study text.

Uses the Anthropic Messages API with a single forced tool (``emit_findings``) whose
input schema matches :class:`mehungry_extractor.models.Findings`, mirroring the
``AI.Agent`` tool-use pattern on the Elixir side. Output is validated with pydantic
before it leaves this module.
"""

from __future__ import annotations

import os

import anthropic

from .models import Findings

DEFAULT_MODEL = os.environ.get("EXTRACTOR_MODEL", "claude-sonnet-5")
MAX_TEXT_CHARS = 60_000

_TOOL = {
    "name": "emit_findings",
    "description": "Record the phase-tagged dietary recommendations found in the study text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "raw_term": {"type": "string"},
                        "target_kind": {"type": "string", "enum": ["compound", "nutrient", "food_pattern"]},
                        "direction": {
                            "type": "string",
                            "enum": ["avoid", "limit", "caution", "encourage", "monitor", "neutral"],
                        },
                        "condition_state_slug": {"type": "string"},
                        "severity": {"type": "string", "enum": ["low", "moderate", "high", "severe"]},
                        "confidence": {"type": "number"},
                        "evidence_snippet": {"type": "string"},
                    },
                    "required": ["raw_term", "target_kind", "direction", "condition_state_slug"],
                },
            }
        },
        "required": ["findings"],
    },
}

_SYSTEM = """You are a clinical-nutrition evidence extractor. You read a research paper about a \
health condition and extract the dietary recommendations it supports, tagging each with the \
disease PHASE it applies to.

Rules:
- Extract ONLY what the text actually supports. If it makes no dietary claim, return no findings.
- Phase is critical: the same food is often advised differently in an active flare vs remission. \
Use one of the condition's provided state slugs when the text ties the advice to a phase; use \
"general" only when the paper genuinely does not distinguish a phase.
- Classify each target as: compound (a specific bioactive chemical), nutrient (a macro/micro \
nutrient like fiber, omega-3, sodium), or food_pattern (a dietary pattern/food class like \
low-residue, low-FODMAP, raw vegetables).
- direction is the paper's advice for that target in that phase.
- Include a short verbatim evidence_snippet for each finding.
- Be conservative: these feed a human review queue, not automatic advice."""


def extract(condition: dict, states: list[dict], text: str, chemical_hints: list[str] | None = None,
            model: str = DEFAULT_MODEL, client: anthropic.Anthropic | None = None) -> Findings:
    """Extract validated :class:`Findings` for one (study, condition) pair."""
    if not text.strip():
        return Findings(findings=[])

    client = client or anthropic.Anthropic()
    user = _build_prompt(condition, states, text, chemical_hints or [])

    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        system=_SYSTEM,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "emit_findings"},
        messages=[{"role": "user", "content": user}],
    )

    for block in resp.content:
        if block.type == "tool_use" and block.name == "emit_findings":
            return Findings.model_validate(block.input)
    return Findings(findings=[])


def _build_prompt(condition: dict, states: list[dict], text: str, hints: list[str]) -> str:
    state_lines = "\n".join(f"  - {s['slug']}: {s['name']} — {s.get('description', '')}" for s in states)
    if not state_lines:
        state_lines = "  (this condition has no defined phases — use 'general')"

    hint_line = ""
    if hints:
        hint_line = "\nChemical entities detected in the text (hints, not exhaustive): " + ", ".join(hints)

    return (
        f"CONDITION: {condition['name']}\n"
        f"Known synonyms: {', '.join(condition.get('synonyms', [])) or '—'}\n"
        f"Disease phases (state slugs you may tag advice with):\n{state_lines}\n"
        f"{hint_line}\n\n"
        f"STUDY TEXT:\n{text[:MAX_TEXT_CHARS]}"
    )
