# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Structured, explicitly answered questions from an executing agent tool."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any


@dataclass
class AgentUserQuestionRequest:
    request_id: str
    questions: list[dict[str, Any]]
    future: asyncio.Future[dict[str, Any]]


def validate_questions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise ValueError("Ask between one and eight questions.")
    questions: list[dict[str, Any]] = []
    ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Questions must be objects.")
        question = {}
        for field, maximum in (("id", 100), ("question", 4000), ("header", 200)):
            text = item.get(field, "")
            if not isinstance(text, str) or len(text) > maximum or (field != "header" and not text.strip()):
                raise ValueError("Question identifiers and text must be nonempty bounded strings.")
            question[field] = text
        if question["id"] in ids:
            raise ValueError("Question identifiers must be unique.")
        ids.add(question["id"])
        options = item.get("options", [])
        if not isinstance(options, list) or len(options) > 12:
            raise ValueError("A question accepts at most twelve options.")
        choices = []
        for option in options:
            if not isinstance(option, dict) or not isinstance(option.get("label"), str) or not option["label"].strip() or len(option["label"]) > 200:
                raise ValueError("Question options require bounded labels.")
            description = option.get("description", "")
            if not isinstance(description, str) or len(description) > 2000:
                raise ValueError("Question option descriptions are too long.")
            choices.append({"label": option["label"], "description": description})
        if len({choice["label"] for choice in choices}) != len(choices):
            raise ValueError("Question option labels must be unique.")
        multiple = item.get("multiSelect", False)
        if not isinstance(multiple, bool):
            raise ValueError("multiSelect must be boolean.")
        question.update(options=choices, multiSelect=multiple)
        questions.append(question)
    if len(json.dumps(questions).encode()) > 64 * 1024:
        raise ValueError("Questions exceed their size limit.")
    return questions


def validate_answers(questions: list[dict[str, Any]], value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"answers"} or not isinstance(value["answers"], list):
        raise ValueError("Send an answers array.")
    by_id = {question["id"]: question for question in questions}
    if len(value["answers"]) != len(questions):
        raise ValueError("Answer each pending question.")
    answers = []
    seen: set[str] = set()
    for item in value["answers"]:
        if not isinstance(item, dict) or set(item) - {"id", "selected", "custom"}:
            raise ValueError("Invalid question answer.")
        identifier = item.get("id")
        if not isinstance(identifier, str) or identifier not in by_id or identifier in seen:
            raise ValueError("Answers must name each question exactly once.")
        seen.add(identifier)
        question = by_id[identifier]
        selected = item.get("selected", [])
        choices = {option["label"] for option in question["options"]}
        if not isinstance(selected, list) or not all(isinstance(option, str) and option in choices for option in selected) or len(set(selected)) != len(selected):
            raise ValueError("Select only the offered choices.")
        if not question["multiSelect"] and len(selected) > 1:
            raise ValueError("This question accepts only one choice.")
        custom = item.get("custom", "")
        if not isinstance(custom, str) or len(custom) > 16000 or not (selected or custom.strip()):
            raise ValueError("Provide a choice or a written answer.")
        answer = {"id": identifier, "selected": selected}
        if custom:
            answer["custom"] = custom
        answers.append(answer)
    return {"answers": answers}


def text_answer(questions: list[dict[str, Any]], text: str) -> dict[str, Any]:
    if len(questions) != 1:
        return validate_answers(questions, json.loads(text))
    question = questions[0]
    labels = [option["label"] for option in question["options"]]
    clean = text.strip()
    if clean.startswith("{"):
        try:
            structured = json.loads(clean)
        except ValueError:
            structured = None
        if isinstance(structured, dict) and "answers" in structured:
            return validate_answers(questions, structured)
    if clean.isdecimal() and 1 <= int(clean) <= len(labels):
        clean = labels[int(clean) - 1]
    item = {"id": question["id"], "selected": [clean] if clean in labels else []}
    if not item["selected"]:
        item["custom"] = text
    return validate_answers(questions, {"answers": [item]})
