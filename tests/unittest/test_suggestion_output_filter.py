import pr_agent.algo.suggestion_output_filter as suggestion_output_filter


class TestSuggestionOutputFilter:
    def test_normalize_code_suggestions_output_keeps_valid_metadata(self):
        data = {
            "code_suggestions": [
                {
                    "relevant_file": "src/one.py",
                    "label": "possible issue",
                    "suggestion_content": "Issue one",
                    "confidence": "HIGH",
                    "evidence_type": "DIFF",
                    "unknown": "preserved value",
                },
                {
                    "relevant_file": "src/two.py",
                    "label": "possible issue",
                    "suggestion_content": "Issue two",
                    "confidence": "unsupported",
                    "evidence_type": "ticket",
                },
            ]
        }

        normalized = suggestion_output_filter.normalize_code_suggestions_output(
            data,
            findings_metadata=True,
            filter_mode="off",
        )

        suggestions = normalized["code_suggestions"]
        assert suggestions[0]["confidence"] == "high"
        assert suggestions[0]["evidence_type"] == "diff"
        assert suggestions[0]["unknown"] == "preserved value"
        assert "confidence" not in suggestions[1]
        assert suggestions[1]["evidence_type"] == "ticket"

    def test_normalize_code_suggestions_output_can_drop_low_confidence_inferred(self):
        data = {
            "code_suggestions": [
                {
                    "relevant_file": "src/one.py",
                    "label": "possible issue",
                    "suggestion_content": "Keep me",
                    "confidence": "medium",
                    "evidence_type": "diff",
                },
                {
                    "relevant_file": "src/two.py",
                    "label": "possible issue",
                    "suggestion_content": "Drop me",
                    "confidence": "low",
                    "evidence_type": "inferred",
                },
            ]
        }

        normalized = suggestion_output_filter.normalize_code_suggestions_output(
            data,
            findings_metadata=True,
            filter_mode="drop_low_confidence_inferred",
        )

        suggestions = normalized["code_suggestions"]
        assert len(suggestions) == 1
        assert suggestions[0]["relevant_file"] == "src/one.py"

    def test_normalize_code_suggestions_output_strips_metadata_when_disabled(self):
        data = {
            "code_suggestions": [
                {
                    "relevant_file": "src/one.py",
                    "label": "possible issue",
                    "suggestion_content": "Issue one",
                    "confidence": "high",
                    "evidence_type": "diff",
                }
            ]
        }

        normalized = suggestion_output_filter.normalize_code_suggestions_output(
            data,
            findings_metadata=False,
        )

        suggestion = normalized["code_suggestions"][0]
        assert "confidence" not in suggestion
        assert "evidence_type" not in suggestion

    def test_normalize_code_suggestions_output_warns_on_unknown_filter_mode(self, monkeypatch):
        warnings = []
        data = {
            "code_suggestions": [
                {
                    "relevant_file": "src/one.py",
                    "label": "possible issue",
                    "suggestion_content": "Keep me",
                    "confidence": "low",
                    "evidence_type": "inferred",
                }
            ]
        }
        monkeypatch.setattr(suggestion_output_filter, "_log_warning", warnings.append)

        normalized = suggestion_output_filter.normalize_code_suggestions_output(
            data,
            findings_metadata=True,
            filter_mode="unknown_mode",
        )

        assert warnings == ["Unknown findings_filter_mode: 'unknown_mode', defaulting to off"]
        assert len(normalized["code_suggestions"]) == 1
