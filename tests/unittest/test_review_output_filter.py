import pr_agent.algo.review_output_filter as review_output_filter


class TestReviewOutputFilter:
    def test_normalize_review_output_caps_findings_and_keeps_valid_metadata(self):
        data = {
            "review": {
                "key_issues_to_review": [
                    {
                        "relevant_file": "src/one.py",
                        "issue_header": "Possible Bug",
                        "issue_content": "Issue one",
                        "confidence": "HIGH",
                        "evidence_type": "DIFF",
                        "start_line": 10,
                        "end_line": 12,
                        "unknown": "preserved value",
                    },
                    {
                        "relevant_file": "src/two.py",
                        "issue_header": "Possible Issue",
                        "issue_content": "Issue two",
                        "confidence": "unsupported",
                        "evidence_type": "ticket",
                        "start_line": 20,
                        "end_line": 21,
                    },
                    {
                        "relevant_file": "src/three.py",
                        "issue_header": "Possible Issue",
                        "issue_content": "Issue three",
                        "confidence": "low",
                        "evidence_type": "inferred",
                        "start_line": 30,
                        "end_line": 31,
                    },
                ]
            }
        }

        normalized = review_output_filter.normalize_review_output(
            data,
            max_findings=2,
            findings_metadata=True,
            filter_mode="off",
        )

        issues = normalized["review"]["key_issues_to_review"]
        assert len(issues) == 2
        assert issues[0]["confidence"] == "high"
        assert issues[0]["evidence_type"] == "diff"
        assert issues[0]["unknown"] == "preserved value"
        assert "confidence" not in issues[1]
        assert issues[1]["evidence_type"] == "ticket"

    def test_normalize_review_output_can_drop_low_confidence_inferred_findings(self):
        data = {
            "review": {
                "key_issues_to_review": [
                    {
                        "relevant_file": "src/one.py",
                        "issue_header": "Possible Bug",
                        "issue_content": "Keep me",
                        "confidence": "medium",
                        "evidence_type": "diff",
                        "start_line": 10,
                        "end_line": 12,
                    },
                    {
                        "relevant_file": "src/two.py",
                        "issue_header": "Possible Issue",
                        "issue_content": "Drop me",
                        "confidence": "low",
                        "evidence_type": "inferred",
                        "start_line": 20,
                        "end_line": 21,
                    },
                ]
            }
        }

        normalized = review_output_filter.normalize_review_output(
            data,
            max_findings=5,
            findings_metadata=True,
            filter_mode="drop_low_confidence_inferred",
        )

        issues = normalized["review"]["key_issues_to_review"]
        assert len(issues) == 1
        assert issues[0]["relevant_file"] == "src/one.py"

    def test_normalize_review_output_keeps_all_findings_when_max_findings_is_zero(self):
        data = {
            "review": {
                "key_issues_to_review": [
                    {
                        "relevant_file": "src/one.py",
                        "issue_header": "Possible Bug",
                        "issue_content": "Issue one",
                        "start_line": 10,
                        "end_line": 12,
                    },
                    {
                        "relevant_file": "src/two.py",
                        "issue_header": "Possible Issue",
                        "issue_content": "Issue two",
                        "start_line": 20,
                        "end_line": 21,
                    },
                ]
            }
        }

        normalized = review_output_filter.normalize_review_output(data, max_findings=0)

        issues = normalized["review"]["key_issues_to_review"]
        assert len(issues) == 2

    def test_normalize_review_output_strips_metadata_when_feature_is_disabled(self):
        data = {
            "review": {
                "key_issues_to_review": [
                    {
                        "relevant_file": "src/one.py",
                        "issue_header": "Possible Bug",
                        "issue_content": "Issue one",
                        "confidence": "high",
                        "evidence_type": "diff",
                        "start_line": 10,
                        "end_line": 12,
                    }
                ]
            }
        }

        normalized = review_output_filter.normalize_review_output(data, max_findings=5, findings_metadata=False)

        issue = normalized["review"]["key_issues_to_review"][0]
        assert "confidence" not in issue
        assert "evidence_type" not in issue

    def test_normalize_review_output_disables_filter_when_metadata_is_disabled(self, monkeypatch):
        warnings = []
        data = {
            "review": {
                "key_issues_to_review": [
                    {
                        "relevant_file": "src/one.py",
                        "issue_header": "Possible Bug",
                        "issue_content": "Keep me",
                        "confidence": "low",
                        "evidence_type": "inferred",
                        "start_line": 10,
                        "end_line": 12,
                    }
                ]
            }
        }
        monkeypatch.setattr(review_output_filter, "_log_warning", warnings.append)

        normalized = review_output_filter.normalize_review_output(
            data,
            max_findings=5,
            findings_metadata=False,
            filter_mode="drop_low_confidence_inferred",
        )

        assert warnings == ["findings_filter_mode has no effect when findings_metadata is false"]
        assert len(normalized["review"]["key_issues_to_review"]) == 1

    def test_normalize_review_output_warns_on_unknown_filter_mode(self, monkeypatch):
        warnings = []
        data = {
            "review": {
                "key_issues_to_review": [
                    {
                        "relevant_file": "src/one.py",
                        "issue_header": "Possible Bug",
                        "issue_content": "Keep me",
                        "confidence": "low",
                        "evidence_type": "inferred",
                        "start_line": 10,
                        "end_line": 12,
                    }
                ]
            }
        }
        monkeypatch.setattr(review_output_filter, "_log_warning", warnings.append)

        normalized = review_output_filter.normalize_review_output(
            data,
            max_findings=5,
            findings_metadata=True,
            filter_mode="unknown_mode",
        )

        assert warnings == ["Unknown findings_filter_mode: 'unknown_mode', defaulting to off"]
        assert len(normalized["review"]["key_issues_to_review"]) == 1
