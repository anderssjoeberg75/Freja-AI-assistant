"""Recovers JSON that an LLM cut off mid-value by hitting its token cap (num_predict /
maxOutputTokens) before finishing a long field — the failure mode behind
`/api/trainer/checkin`'s "Unterminated string starting at: line ... column ..." crash
(T-004). A well-formed response never touches the repair path; only a `json.loads()`
failure does.
"""
import json


def parse_llm_json(text: str) -> dict:
    """Parses `text` as JSON, stripping markdown code fences first. If parsing fails,
    attempts to close whatever string/object/array was left open by truncation and
    re-parses once. Raises the original JSONDecodeError (not the repaired attempt's) if
    the repair doesn't produce valid JSON, so callers see the real cause."""
    cleaned = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as original_error:
        repaired = _close_truncated_json(cleaned)
        if repaired is None:
            raise original_error
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            raise original_error


def _close_truncated_json(text: str):
    """Walks `text` tracking open strings/objects/arrays. Returns a version with them
    closed, or None if nothing was left open (the failure is something other than
    truncation, so repair would just paper over a real error)."""
    in_string = False
    escape = False
    stack = []
    for ch in text:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()

    if not in_string and not stack:
        return None

    repaired = text
    if in_string:
        if escape:
            repaired = repaired[:-1]
        repaired += '"'

    repaired = repaired.rstrip().rstrip(",")

    # A key cut off before its value ever started (`..."c":`) can't be closed - it has
    # to be dropped, back to the previous comma or the enclosing opener.
    if repaired.endswith(":"):
        last_comma = repaired.rfind(",")
        last_opener = max(repaired.rfind("{"), repaired.rfind("["))
        if last_comma > last_opener:
            repaired = repaired[:last_comma]
        elif last_opener != -1:
            repaired = repaired[:last_opener + 1]
        else:
            return None

    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"
    return repaired
