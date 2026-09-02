from src.llm.json_extract import extract_json


def test_returns_bare_json_unchanged():
    assert extract_json('{"a": 1}') == '{"a": 1}'


def test_strips_json_fence():
    text = '```json\n{"a": 1}\n```'
    assert extract_json(text) == '{"a": 1}'


def test_strips_bare_fence_without_json_tag():
    text = '```\n{"a": 1}\n```'
    assert extract_json(text) == '{"a": 1}'


def test_extracts_first_balanced_object_from_surrounding_prose():
    text = 'Sure, here is the JSON:\n{"a": 1, "b": {"c": 2}}\nHope that helps!'
    assert extract_json(text) == '{"a": 1, "b": {"c": 2}}'


def test_non_json_text_passed_through_for_caller_to_fail_on():
    assert extract_json("this is not json at all") == "this is not json at all"
