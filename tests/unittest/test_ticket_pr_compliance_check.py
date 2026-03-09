import asyncio

from pr_agent.config_loader import get_settings
from pr_agent.tools import ticket_pr_compliance_check


class FakeGitProvider:
    def __init__(self, title: str, branch: str):
        self.pr = type("PR", (), {"title": title})()
        self._branch = branch

    def get_pr_branch(self):
        return self._branch


def test_extract_tickets_prefers_title_ticket_on_mismatch(monkeypatch):
    async def fake_fetch_jira_ticket_context(pr_title, branch):
        if pr_title == "FAKE-1234 add sample feature flow":
            return type(
                "Result",
                (),
                {
                    "note": (
                        "PR title references Jira ticket `FAKE-1234` but branch name references `MOCK-5678`. "
                        "Please verify that the correct Jira ticket is used in the PR metadata."
                    ),
                    "ticket": {
                        "ticket_id": "FAKE-1234",
                        "ticket_url": "https://example.com/FAKE-1234",
                        "title": "Wrong ticket",
                        "body": "",
                        "labels": "",
                    },
                },
            )()

        raise AssertionError(f"Unexpected fetch args: {pr_title!r}, {branch!r}")

    monkeypatch.setattr(ticket_pr_compliance_check, "fetch_jira_ticket_context", fake_fetch_jira_ticket_context)

    tickets, note = asyncio.run(
        ticket_pr_compliance_check.extract_tickets(
            FakeGitProvider(
                "FAKE-1234 add sample feature flow",
                "MOCK-5678-sample-feature-flow",
            )
        )
    )

    assert [ticket["ticket_id"] for ticket in tickets] == ["FAKE-1234"]
    assert "FAKE-1234" in note
    assert "MOCK-5678" in note
    assert note.count("PR title references Jira ticket") == 1


def test_extract_and_cache_pr_tickets_preserves_cached_ticket_note(monkeypatch):
    async def fake_extract_tickets(_git_provider):
        return (
            [{"ticket_id": "FAKE-5678", "ticket_url": "https://example.com/FAKE-5678", "title": "Ticket"}],
            "cached note",
        )

    monkeypatch.setattr(ticket_pr_compliance_check, "extract_tickets", fake_extract_tickets)

    settings = get_settings()
    previous_require_ticket_analysis = settings.get("pr_reviewer.require_ticket_analysis_review", False)
    previous_related_tickets = settings.get("related_tickets", [])
    previous_ticket_note = settings.get("ticket_compliance_note", "")

    try:
        settings.set("pr_reviewer.require_ticket_analysis_review", True)
        settings.set("related_tickets", [])
        settings.set("ticket_compliance_note", "")

        first_vars = {}
        asyncio.run(ticket_pr_compliance_check.extract_and_cache_pr_tickets(object(), first_vars))

        second_vars = {}
        asyncio.run(ticket_pr_compliance_check.extract_and_cache_pr_tickets(object(), second_vars))

        assert first_vars["ticket_compliance_note"] == "cached note"
        assert second_vars["ticket_compliance_note"] == "cached note"
    finally:
        settings.set("pr_reviewer.require_ticket_analysis_review", previous_require_ticket_analysis)
        settings.set("related_tickets", previous_related_tickets)
        settings.set("ticket_compliance_note", previous_ticket_note)


def test_extract_tickets_preserves_title_fetch_note_on_mismatch(monkeypatch):
    async def fake_fetch_jira_ticket_context(pr_title, branch):
        if pr_title == "FAKE-1234 add sample feature flow":
            return type(
                "Result",
                (),
                {
                    "note": "Title ticket note",
                    "ticket": {
                        "ticket_id": "FAKE-1234",
                        "ticket_url": "https://example.com/FAKE-1234",
                        "title": "Wrong ticket",
                        "body": "",
                        "labels": "",
                    },
                },
            )()

        raise AssertionError(f"Unexpected fetch args: {pr_title!r}, {branch!r}")

    monkeypatch.setattr(ticket_pr_compliance_check, "fetch_jira_ticket_context", fake_fetch_jira_ticket_context)

    tickets, note = asyncio.run(
        ticket_pr_compliance_check.extract_tickets(
            FakeGitProvider(
                "FAKE-1234 add sample feature flow",
                "MOCK-5678-sample-feature-flow",
            )
        )
    )

    assert [ticket["ticket_id"] for ticket in tickets] == ["FAKE-1234"]
    assert "Title ticket note" in note
    assert "MOCK-5678" not in note


def test_extract_tickets_preserves_title_failure_note_on_mismatch(monkeypatch):
    async def fake_fetch_jira_ticket_context(pr_title, branch):
        if pr_title == "FAKE-1234 add sample feature flow":
            return type(
                "Result",
                (),
                {
                    "note": (
                        "Jira ticket `FAKE-1234` was detected in the PR title but was not found.\n\n"
                        "PR title references Jira ticket `FAKE-1234` but branch name references `MOCK-5678`. "
                        "Please verify that the correct Jira ticket is used in the PR metadata."
                    ),
                    "ticket": None,
                },
            )()

        raise AssertionError(f"Unexpected fetch args: {pr_title!r}, {branch!r}")

    monkeypatch.setattr(ticket_pr_compliance_check, "fetch_jira_ticket_context", fake_fetch_jira_ticket_context)

    tickets, note = asyncio.run(
        ticket_pr_compliance_check.extract_tickets(
            FakeGitProvider(
                "FAKE-1234 add sample feature flow",
                "MOCK-5678-sample-feature-flow",
            )
        )
    )

    assert tickets is None
    assert "FAKE-1234" in note
    assert "MOCK-5678" in note
    assert "not found" in note
    assert note.count("PR title references Jira ticket") == 1
