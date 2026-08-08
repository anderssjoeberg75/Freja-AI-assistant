"""T-004: /api/trainer/checkin (and any other generate_json caller) must not 500 with a raw
json.JSONDecodeError just because the LLM hit its token cap mid-value. These tests reproduce
the actual failure mode - Ollama/Gemini truncating output at max_tokens/num_predict - and
confirm parse_llm_json recovers what it can and only gives up on genuinely broken input.
"""
import json

import pytest

from backend.services.json_repair import parse_llm_json


def test_well_formed_json_parses_normally():
    assert parse_llm_json('{"a": 1, "b": "two"}') == {"a": 1, "b": "two"}


def test_strips_markdown_code_fences():
    assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_recovers_from_a_string_cut_off_mid_value():
    """The T-004 case: a long trailing text field (e.g. `briefing`) truncated when the
    model hit num_predict/maxOutputTokens before closing the string."""
    truncated = '{"sleep_summary": "ok", "briefing": "Idag ser bra ut men kom ihåg att'
    result = parse_llm_json(truncated)
    assert result["sleep_summary"] == "ok"
    assert result["briefing"].startswith("Idag ser bra ut")


def test_recovers_from_truncation_inside_a_nested_object():
    truncated = '{"outer": {"inner": "cut off here'
    result = parse_llm_json(truncated)
    assert result == {"outer": {"inner": "cut off here"}}


def test_recovers_from_truncation_inside_a_list_of_objects():
    truncated = '{"items": [{"name": "a"}, {"name": "b'
    result = parse_llm_json(truncated)
    assert result == {"items": [{"name": "a"}, {"name": "b"}]}


def test_recovers_from_a_dangling_comma_before_the_cut():
    truncated = '{"a": "b", "c": "d",'
    result = parse_llm_json(truncated)
    assert result == {"a": "b", "c": "d"}


def test_recovers_from_a_key_with_no_value_yet():
    truncated = '{"a": "b", "c":'
    result = parse_llm_json(truncated)
    assert result == {"a": "b"}


def test_recovers_when_the_dangling_key_is_the_only_content():
    truncated = '{"c":'
    result = parse_llm_json(truncated)
    assert result == {}


def test_raises_the_original_error_for_input_that_is_not_just_truncated():
    """Malformed-but-complete JSON (e.g. a stray comma in the middle) is a real error, not
    a truncation - repair must not silently swallow it into something misleading."""
    broken = '{"a": 1,, "b": 2}'
    with pytest.raises(json.JSONDecodeError):
        parse_llm_json(broken)


def test_recovers_from_truncation_after_escaping_backslash():
    truncated = '{"path": "C:\\\\Users\\\\'
    result = parse_llm_json(truncated)
    assert result == {"path": "C:\\Users\\"}


def test_raises_the_original_error_for_empty_input():
    with pytest.raises(json.JSONDecodeError):
        parse_llm_json("")


def test_recovers_from_partial_unicode_escape_two_hex():
    # T-074: truncation mid-unicode escape sequence should strip the incomplete
    # escape rather than leaving broken JSON.
    truncated = '{"text": "caf\\u00'
    result = parse_llm_json(truncated)
    assert result == {"text": "caf"}


def test_recovers_from_partial_unicode_escape_no_hex():
    # Truncation right after backslash-u with no hex digits at all.
    truncated = '{"text": "caf\\u'
    result = parse_llm_json(truncated)
    assert result == {"text": "caf"}


def test_recovers_from_partial_unicode_escape_three_hex():
    # Truncation with 3 of 4 required hex digits.
    truncated = '{"text": "caf\\u000'
    result = parse_llm_json(truncated)
    assert result == {"text": "caf"}


def test_complete_unicode_escape_not_stripped():
    # A complete backslash-u escape must NOT be stripped.
    well_formed = '{"text": "caf\\u00e9"}'
    result = parse_llm_json(well_formed)
    assert result == {"text": "caf\u00e9"}

