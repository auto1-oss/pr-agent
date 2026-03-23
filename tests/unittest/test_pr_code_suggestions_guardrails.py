from types import SimpleNamespace

import pytest

from pr_agent.tools.pr_code_suggestions import PRCodeSuggestions


class FakeSection(SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)


@pytest.mark.asyncio
async def test_analyze_self_reflection_response_carries_metadata(monkeypatch):
    fake_settings = SimpleNamespace(
        config=SimpleNamespace(publish_output=False),
        pr_code_suggestions=FakeSection(commitable_code_suggestions=False, findings_filter_mode="off"),
    )
    monkeypatch.setattr("pr_agent.tools.pr_code_suggestions.get_settings", lambda: fake_settings)

    tool = PRCodeSuggestions.__new__(PRCodeSuggestions)
    tool.findings_metadata = True
    tool.validate_one_liner_suggestion_not_repeating_code = lambda suggestion: suggestion

    data = {
        "code_suggestions": [
            {
                "relevant_file": "src/example.py",
                "label": "possible issue",
                "existing_code": "value = foo()",
                "improved_code": "value = bar()",
                "one_sentence_summary": "Tighten validation",
                "suggestion_content": "Use the validated value.",
            }
        ]
    }
    response_reflect = """
code_suggestions:
- suggestion_summary: |
    Tighten validation
  relevant_file: |
    src/example.py
  relevant_lines_start: 12
  relevant_lines_end: 13
  suggestion_score: 8
  confidence: |
    HIGH
  evidence_type: |
    DIFF
  why: |
    The suggestion is directly supported by the changed lines.
"""

    await tool.analyze_self_reflection_response(data, response_reflect)

    suggestion = data["code_suggestions"][0]
    assert suggestion["score"] == 8
    assert suggestion["confidence"].strip().lower() == "high"
    assert suggestion["evidence_type"].strip().lower() == "diff"
    assert suggestion["relevant_lines_start"] == 12
    assert suggestion["relevant_lines_end"] == 13


def test_normalize_code_suggestions_output_uses_filter_mode(monkeypatch):
    fake_settings = SimpleNamespace(
        config=SimpleNamespace(publish_output=False),
        pr_code_suggestions=FakeSection(findings_filter_mode="drop_low_confidence_inferred"),
    )
    monkeypatch.setattr("pr_agent.tools.pr_code_suggestions.get_settings", lambda: fake_settings)

    tool = PRCodeSuggestions.__new__(PRCodeSuggestions)
    tool.findings_metadata = True

    data = {
        "code_suggestions": [
            {
                "relevant_file": "src/keep.py",
                "label": "possible issue",
                "suggestion_content": "Keep me.",
                "confidence": "medium",
                "evidence_type": "diff",
            },
            {
                "relevant_file": "src/drop.py",
                "label": "possible issue",
                "suggestion_content": "Drop me.",
                "confidence": "low",
                "evidence_type": "inferred",
            },
        ]
    }

    normalized = tool._normalize_code_suggestions_output(data)

    assert [suggestion["relevant_file"] for suggestion in normalized["code_suggestions"]] == ["src/keep.py"]


def test_append_small_file_context_to_diff_lists_skips_when_legacy_no_line_numbers_missing():
    tool = PRCodeSuggestions.__new__(PRCodeSuggestions)
    tool.small_file_context = True
    tool.patches_diff_list = ["## File: 'src/example.py'"]

    tool._append_small_file_context_to_diff_lists()

    assert tool.patches_diff_list == ["## File: 'src/example.py'"]


@pytest.mark.asyncio
async def test_prepare_prediction_main_refreshes_no_line_chunks_after_fallback(monkeypatch):
    fake_settings = SimpleNamespace(
        pr_code_suggestions=FakeSection(
            decouple_hunks=False,
            max_number_of_calls=3,
            parallel_calls=False,
            suggestions_score_threshold=0,
            findings_filter_mode="off",
        )
    )
    monkeypatch.setattr("pr_agent.tools.pr_code_suggestions.get_settings", lambda: fake_settings)

    tool = PRCodeSuggestions.__new__(PRCodeSuggestions)
    tool.git_provider = object()
    tool.token_handler = object()
    tool.small_file_context = False
    tool.findings_metadata = False
    tool.remove_line_numbers = lambda patches: [f"normalized:{patch}" for patch in patches]

    calls = []

    async def fake_get_prediction(model, patches_diff, patches_diff_no_line_numbers):
        calls.append((patches_diff, patches_diff_no_line_numbers))
        return {"code_suggestions": [{"score": 1}]}

    async def fake_convert(_patches_diff_list_no_line_numbers, _model):
        return []

    tool._get_prediction = fake_get_prediction
    tool.convert_to_decoupled_with_line_numbers = fake_convert

    responses = {
        False: ["no-line-chunk"],
        True: ["line-chunk"],
    }

    monkeypatch.setattr(
        "pr_agent.tools.pr_code_suggestions.get_pr_multi_diffs",
        lambda _git_provider, _token_handler, _model, max_calls, add_line_numbers: responses[add_line_numbers],
    )

    await tool.prepare_prediction_main("fake-model")

    assert calls == [("line-chunk", "normalized:line-chunk")]
