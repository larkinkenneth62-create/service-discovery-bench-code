from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[2] / "experiments" / "llm_v0_2_qwen_sse_selection_v1_5" / "code" / "output_contracts_v1_5.py"
SPEC = importlib.util.spec_from_file_location("contracts_v1_5_tested", PATH)
assert SPEC and SPEC.loader
C = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = C
SPEC.loader.exec_module(C)


def response(value, *, reasoning=False):
    message = {"content": value if isinstance(value, str) else json.dumps(value)}
    if reasoning:
        message["reasoning_content"] = "kept separate"
    return {"choices": [{"message": message}]}


@pytest.mark.parametrize("ranking", [["e", "d", "c", "b", "a"], ["a", "c", "e", "b", "d"]])
def test_top5_valid_orderings(ranking):
    parsed = C.parse_topk_response(response({"ranked_candidate_ids": ranking}), list("abcdef"), 5)
    assert parsed.valid


def test_topk_fewer_than_five_and_reasoning_separate():
    parsed = C.parse_topk_response(response({"ranked_candidate_ids": ["b", "a"]}, reasoning=True), ["a", "b"], 2)
    assert parsed.valid and parsed.reasoning_present


@pytest.mark.parametrize("value,code", [
    ({"ranked_candidate_ids": ["a", "b", "c", "d"]}, "TOPK_LENGTH_MISMATCH"),
    ({"ranked_candidate_ids": ["a", "b", "c", "d", "e", "f"]}, "TOPK_LENGTH_MISMATCH"),
    ({"ranked_candidate_ids": ["a", "a", "b", "c", "d"]}, "DUPLICATE_CANDIDATE_ID"),
    ({"ranked_candidate_ids": ["a", "b", "c", "d", "x"]}, "UNKNOWN_CANDIDATE_ID"),
    ({"ranked_candidate_ids": ["a", "b", "c", "d", "e"], "extra": []}, "UNEXPECTED_TOP_LEVEL_FIELD"),
])
def test_top5_invalid_structures(value, code):
    parsed = C.parse_topk_response(response(value), list("abcdef"), 5)
    assert not parsed.valid and parsed.error_code == code


@pytest.mark.parametrize("content", ["answer: {}", "```json\n{}\n```", "{broken"])
def test_top5_rejects_prose_fences_and_invalid_json(content):
    assert not C.parse_topk_response(response(content), ["a"], 1).valid


@pytest.mark.parametrize("selected", [["a", "c"], ["b"], []])
def test_selected_set_valid(selected):
    parsed = C.parse_selected_set_response(response({"selected_candidate_ids": selected}), ["a", "b", "c"])
    assert parsed.valid and parsed.data["selected_candidate_ids"] == selected


@pytest.mark.parametrize("value,code", [
    ({"selected_candidate_ids": ["a", "a"]}, "DUPLICATE_CANDIDATE_ID"),
    ({"selected_candidate_ids": ["x"]}, "UNKNOWN_CANDIDATE_ID"),
    ({"selected_candidate_ids": [], "ranked_candidate_ids": []}, "UNEXPECTED_TOP_LEVEL_FIELD"),
    ({"selected_candidate_ids": "a"}, "FIELD_NOT_ARRAY"),
])
def test_selected_set_invalid(value, code):
    parsed = C.parse_selected_set_response(response(value), ["a", "b"])
    assert not parsed.valid and parsed.error_code == code


def test_contract_registry_mapping_is_fail_closed():
    assert C.contract_for("machine", "anything") == C.TOP5_RANKING_V1
    assert C.contract_for("native", "single_service") == C.TOP5_RANKING_V1
    assert C.contract_for("native", "multi_api") == C.SELECTED_SET_V1
    assert C.contract_for("native", "composable_service") == C.SELECTED_SET_V1
    with pytest.raises(ValueError):
        C.contract_for("native", "unknown")
