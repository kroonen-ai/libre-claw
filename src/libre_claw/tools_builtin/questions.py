# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Structured user input through the active surface's question service."""

import json
from typing import Any

from libre_claw.core.questions import validate_questions
from libre_claw.core.tools import BaseTool, ToolResult, register_tool


@register_tool
class AskUserQuestionTool(BaseTool):
    name = "ask_user_question"
    description = "Ask the user for missing information or a choice. Wait for their explicit answer before continuing dependent work."
    permission_level = "allow"
    read_only = True
    parameters = {"questions": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
        "type": "object", "required": ["id", "question"], "properties": {
            "id": {"type": "string"}, "question": {"type": "string"}, "header": {"type": "string"},
            "multiSelect": {"type": "boolean"},
            "options": {"type": "array", "items": {"type": "object", "required": ["label"],
                "properties": {"label": {"type": "string"}, "description": {"type": "string"}}}},
        },
    }}}
    required = ("questions",)

    async def execute(self, questions: list[dict[str, Any]]) -> ToolResult:
        handler = self.context.shared_state.get("user_question_handler")
        if not callable(handler):
            return ToolResult(error="User questions require an active interactive task.")
        result = await handler(validate_questions(questions))
        return ToolResult(content=json.dumps(result))
