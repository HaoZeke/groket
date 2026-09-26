"""Default JSON Schema for structured LLM review output."""

from __future__ import annotations

from ...models import JsonObject

REVIEW_FINDINGS_SCHEMA: JsonObject = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "findings", "all_clear"],
    "properties": {
        "summary": {
            "type": "string",
            "description": "1-3 sentence overall assessment of the session.",
        },
        "all_clear": {
            "type": "boolean",
            "description": "True only when no material issues were found.",
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "severity",
                    "title",
                    "what_model_did",
                    "what_should_have_done",
                    "why_mistake",
                    "evidence",
                ],
                "properties": {
                    "id": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "title": {"type": "string"},
                    "category": {"type": "string"},
                    "what_model_did": {"type": "string"},
                    "what_should_have_done": {"type": "string"},
                    "why_mistake": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["event_index"],
                            "properties": {
                                "event_index": {"type": "integer"},
                                "tool_call_id": {"type": "string"},
                                "note": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}
