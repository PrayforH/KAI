"""Recognise the bounded follow-up display protocol without exposing its contents."""

import json
import re
from typing import Any, TypeGuard, cast


def _text(value: object, maximum: int) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _valid_input(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    data = cast(dict[str, Any], data)
    if data.get("version") != 1 or isinstance(data.get("version"), bool):
        return False
    if set(data) - {"version", "title", "questions"} or not _text(data.get("title"), 160):
        return False
    questions = data.get("questions")
    if not isinstance(questions, list):
        return False
    questions = cast(list[Any], questions)
    if not 1 <= len(questions) <= 4:
        return False
    ids: set[str] = set()
    for question in questions:
        if not isinstance(question, dict):
            return False
        question = cast(dict[str, Any], question)
        identifier = question.get("id")
        kind = question.get("type")
        if (
            not _text(identifier, 64)
            or not re.fullmatch(r"[\w-]+", identifier, re.ASCII)
            or identifier in ids
        ):
            return False
        if not _text(question.get("label"), 500) or kind not in (
            "single",
            "multi",
            "text",
            "password",
        ):
            return False
        options = question.get("options")
        if options is None:
            options = []
        if not isinstance(options, list):
            return False
        options = cast(list[Any], options)
        if len(options) > 8 or not all(_text(option, 300) for option in options):
            return False
        if len(set(cast(list[str], options))) != len(options):
            return False
        if (kind in ("text", "password") and options) or (
            kind in ("single", "multi") and len(options) < 2
        ):
            return False
        if "sensitive" in question and not isinstance(question["sensitive"], bool):
            return False
        ids.add(identifier)
    return True


def has_conversation_input(text: str) -> bool:
    blocks = list(re.finditer(r"```([^\n]*)\n([\s\S]*?)\n```", text))
    found = 0
    for block in blocks:
        if (
            block[1].strip().lower() not in ("harness-input", "harness", "json", "")
            or len(block[2]) > 16_000
        ):
            continue
        try:
            found += int(_valid_input(json.loads(block[2])))
        except (ValueError, TypeError, RecursionError):
            pass
    decoder = json.JSONDecoder()
    end = 0
    for start in re.finditer(r"(?:^|\n)[ \t]*(?=\{)", text):
        offset = start.end()
        if offset < end or any(block.start() <= offset < block.end() for block in blocks):
            continue
        try:
            data, length = decoder.raw_decode(text[offset : offset + 16_000])
        except (ValueError, TypeError, RecursionError):
            continue
        end = offset + length
        found += int(_valid_input(data))
    return found == 1
