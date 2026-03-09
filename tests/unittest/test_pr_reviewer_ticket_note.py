from pr_agent.tools.pr_reviewer import append_ticket_compliance_note
from pr_agent.tools.pr_reviewer import build_suspected_ticket_mismatch_note


def test_append_ticket_compliance_note():
    note = "First note"
    extra_note = "Second note"

    result = append_ticket_compliance_note(note, extra_note)

    assert result == "First note\n\nSecond note"


def test_build_suspected_ticket_mismatch_note_when_all_requirements_are_non_compliant():
    result = build_suspected_ticket_mismatch_note(
        [
            {
                "fully_compliant_requirements": "",
                "not_compliant_requirements": "- Requirement 1\n",
            }
        ]
    )

    assert "wrong Jira ticket" in result


def test_build_suspected_ticket_mismatch_note_skips_when_any_requirement_is_compliant():
    result = build_suspected_ticket_mismatch_note(
        [
            {
                "fully_compliant_requirements": "- Requirement 1\n",
                "not_compliant_requirements": "",
            }
        ]
    )

    assert result == ""


def test_build_suspected_ticket_mismatch_note_ignores_empty_ticket_entries():
    result = build_suspected_ticket_mismatch_note(
        [
            {
                "fully_compliant_requirements": "",
                "not_compliant_requirements": "- Requirement 1\n",
            },
            {
                "fully_compliant_requirements": "",
                "not_compliant_requirements": "",
            },
        ]
    )

    assert "wrong Jira ticket" in result


def test_build_suspected_ticket_mismatch_note_ignores_placeholder_bullets():
    result = build_suspected_ticket_mismatch_note(
        [
            {
                "fully_compliant_requirements": "-\n",
                "not_compliant_requirements": "- Requirement 1\n",
            }
        ]
    )

    assert "wrong Jira ticket" in result


def test_build_suspected_ticket_mismatch_note_ignores_none_placeholders():
    result = build_suspected_ticket_mismatch_note(
        [
            {
                "fully_compliant_requirements": "(none)\n",
                "not_compliant_requirements": "- Requirement 1\n",
            }
        ]
    )

    assert "wrong Jira ticket" in result


def test_build_suspected_ticket_mismatch_note_skips_non_dict_entries():
    result = build_suspected_ticket_mismatch_note(
        [
            {
                "fully_compliant_requirements": "",
                "not_compliant_requirements": "- Requirement 1\n",
            },
            "unexpected",
        ]
    )

    assert "wrong Jira ticket" in result
