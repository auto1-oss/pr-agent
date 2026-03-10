import asyncio

from pr_agent.tickets import jira_cloud


class TestJiraCloudTicketKeyExtraction:
    def test_extract_from_pr_title_prefix(self):
        assert jira_cloud.extract_jira_ticket_key("FAKE-1234 new feature", "") == "FAKE-1234"

    def test_reject_zero_issue_number(self):
        assert jira_cloud.extract_jira_ticket_key("FAKE-0 invalid", "FAKE-0-invalid") is None

    def test_extract_from_branch_prefix(self):
        assert jira_cloud.extract_jira_ticket_key("", "FAKE-1234-new-feature") == "FAKE-1234"

    def test_precedence_title_over_branch(self):
        assert jira_cloud.extract_jira_ticket_key("FAKE-1111 something", "MOCK-2222-new") == "FAKE-1111"

    def test_fetch_jira_ticket_context_returns_mismatch_note_when_title_and_branch_differ(self, monkeypatch):
        monkeypatch.delenv("JIRA_BASE_URL", raising=False)
        monkeypatch.delenv("JIRA_API_EMAIL", raising=False)
        monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
        monkeypatch.delenv("JIRA_AUTH_TYPE", raising=False)
        monkeypatch.delenv("JIRA_CLOUD_ID", raising=False)

        result = asyncio.run(jira_cloud.fetch_jira_ticket_context("FAKE-1111 something", "MOCK-2222-new"))

        assert result.status == "not_configured"
        assert result.key == "FAKE-1111"
        assert "FAKE-1111" in result.note
        assert "MOCK-2222" in result.note
        assert "Please verify" in result.note

    def test_branch_refs_heads_prefix(self):
        assert jira_cloud.extract_jira_ticket_key("", "refs/heads/FAKE-1234-new") == "FAKE-1234"

    def test_normalize_atlassian_jira_path(self):
        assert (
            jira_cloud._normalize_jira_base_url("https://example.atlassian.net/jira") == "https://example.atlassian.net"
        )


class TestJiraCloudFetch:
    def test_extract_error_message_from_jira_error_payload(self):
        class FakeResponse:
            async def text(self):
                return '{"errorMessages": ["Issue does not exist or you do not have permission to see it."]}'

        message = asyncio.run(jira_cloud._extract_error_message(FakeResponse()))
        assert message == "Issue does not exist or you do not have permission to see it."

    def test_render_description_to_text_from_adf(self):
        issue = {
            "renderedFields": {},
            "fields": {
                "description": {
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Hello"},
                                {"type": "text", "text": " Jira"},
                            ],
                        }
                    ],
                }
            },
        }

        assert jira_cloud._render_description_to_text(issue) == "Hello Jira"

    def test_try_fetch_jira_ticket_maps_fields(self, monkeypatch):
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net/jira")
        monkeypatch.setenv("JIRA_API_EMAIL", "user@example.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "token")

        async def fake_fetch_issue_json(session, auth, key):
            assert auth.site_base_url == "https://example.atlassian.net"
            assert auth.api_base_url == "https://example.atlassian.net"
            assert auth.auth_mode == "basic"
            assert key == "FAKE-1234"
            return {
                "fields": {
                    "summary": "Implement Jira support",
                    "labels": ["backend", "review"],
                    "subtasks": [
                        {"key": "SUB-2", "fields": {"summary": "Subtask 1"}},
                        {"key": "SUB-3", "fields": {"summary": "Subtask 2"}},
                    ],
                },
                "renderedFields": {"description": "<p>Hello <b>world</b></p>"},
            }

        monkeypatch.setattr(jira_cloud, "_fetch_issue_json", fake_fetch_issue_json)

        ticket = asyncio.run(jira_cloud.try_fetch_jira_ticket("FAKE-1234 new feature", "MOCK-9999-ignored"))
        assert ticket is not None
        assert ticket["ticket_id"] == "FAKE-1234"
        assert ticket["ticket_url"] == "https://example.atlassian.net/browse/FAKE-1234"
        assert ticket["title"] == "Implement Jira support"
        assert "Hello" in ticket["body"]
        assert ticket["labels"] == "backend, review"

        assert "sub_issues" in ticket
        assert ticket["sub_issues"][0]["ticket_url"] == "https://example.atlassian.net/browse/SUB-2"
        assert ticket["sub_issues"][0]["title"] == "Subtask 1"
        assert ticket["sub_issues"][0]["body"] == ""
        assert ticket["sub_issues"][0]["labels"] == ""

    def test_fetch_jira_ticket_context_uses_scoped_token_with_cloud_id(self, monkeypatch):
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.setenv("JIRA_API_EMAIL", "user@example.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "token")
        monkeypatch.setenv("JIRA_AUTH_TYPE", "scoped")
        monkeypatch.setenv("JIRA_CLOUD_ID", "2672de8d-bce7-4cb5-9e65-128f96b0bd9a")

        async def fake_fetch_issue_json(session, auth, key):
            assert auth.auth_mode == "scoped"
            assert auth.site_base_url == "https://example.atlassian.net"
            assert auth.api_base_url == "https://api.atlassian.com/ex/jira/2672de8d-bce7-4cb5-9e65-128f96b0bd9a"
            assert key == "FAKE-1234"
            return {
                "fields": {
                    "summary": "Implement Jira support",
                    "labels": [],
                    "subtasks": [],
                },
                "renderedFields": {"description": "<p>Hello world</p>"},
            }

        monkeypatch.setattr(jira_cloud, "_fetch_issue_json", fake_fetch_issue_json)

        result = asyncio.run(jira_cloud.fetch_jira_ticket_context("FAKE-1234 new feature", "MOCK-9999-ignored"))
        assert result.status == "fetched"
        assert result.ticket is not None
        assert result.ticket["ticket_url"] == "https://example.atlassian.net/browse/FAKE-1234"

    def test_fetch_jira_ticket_context_returns_mismatch_note_when_ticket_is_fetched(self, monkeypatch):
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.setenv("JIRA_API_EMAIL", "user@example.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "token")

        async def fake_fetch_issue_json(session, auth, key):
            return {
                "fields": {
                    "summary": "Implement Jira support",
                    "labels": [],
                    "subtasks": [],
                },
                "renderedFields": {"description": "<p>Hello world</p>"},
            }

        monkeypatch.setattr(jira_cloud, "_fetch_issue_json", fake_fetch_issue_json)

        result = asyncio.run(jira_cloud.fetch_jira_ticket_context("FAKE-1234 new feature", "MOCK-9999-ignored"))

        assert result.status == "fetched"
        assert result.ticket is not None
        assert "FAKE-1234" in result.note
        assert "MOCK-9999" in result.note

    def test_fetch_jira_ticket_context_uses_scoped_token_with_implicit_detection(self, monkeypatch):
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.setenv("JIRA_API_EMAIL", "user@example.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "token")
        monkeypatch.delenv("JIRA_AUTH_TYPE", raising=False)
        monkeypatch.setenv("JIRA_CLOUD_ID", "2672de8d-bce7-4cb5-9e65-128f96b0bd9a")

        async def fake_fetch_issue_json(session, auth, key):
            assert auth.auth_mode == "scoped"
            assert auth.api_base_url == "https://api.atlassian.com/ex/jira/2672de8d-bce7-4cb5-9e65-128f96b0bd9a"
            return {
                "fields": {
                    "summary": "Implement Jira support",
                    "labels": [],
                    "subtasks": [],
                },
                "renderedFields": {"description": "<p>Hello world</p>"},
            }

        monkeypatch.setattr(jira_cloud, "_fetch_issue_json", fake_fetch_issue_json)

        result = asyncio.run(jira_cloud.fetch_jira_ticket_context("FAKE-1234 new feature", "MOCK-9999-ignored"))
        assert result.status == "fetched"
        assert result.ticket is not None

    def test_fetch_jira_ticket_context_discovers_cloud_id_for_bearer_token(self, monkeypatch):
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.setenv("JIRA_API_TOKEN", "token")
        monkeypatch.setenv("JIRA_AUTH_TYPE", "oauth")
        monkeypatch.delenv("JIRA_CLOUD_ID", raising=False)
        monkeypatch.delenv("JIRA_API_EMAIL", raising=False)

        async def fake_discover_cloud_id(session, site_base_url):
            assert site_base_url == "https://example.atlassian.net"
            return "2672de8d-bce7-4cb5-9e65-128f96b0bd9a"

        async def fake_fetch_issue_json(session, auth, key):
            assert auth.auth_mode == "bearer"
            assert auth.api_base_url == "https://api.atlassian.com/ex/jira/2672de8d-bce7-4cb5-9e65-128f96b0bd9a"
            return {
                "fields": {
                    "summary": "Implement Jira support",
                    "labels": [],
                    "subtasks": [],
                },
                "renderedFields": {"description": "<p>Hello world</p>"},
            }

        monkeypatch.setattr(jira_cloud, "_discover_cloud_id", fake_discover_cloud_id)
        monkeypatch.setattr(jira_cloud, "_fetch_issue_json", fake_fetch_issue_json)

        result = asyncio.run(jira_cloud.fetch_jira_ticket_context("FAKE-1234 new feature", "MOCK-9999-ignored"))
        assert result.status == "fetched"
        assert result.ticket is not None

    def test_explicit_bearer_auth_takes_precedence_over_implicit_scoped_detection(self, monkeypatch):
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.setenv("JIRA_API_EMAIL", "user@example.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "token")
        monkeypatch.setenv("JIRA_AUTH_TYPE", "bearer")
        monkeypatch.setenv("JIRA_CLOUD_ID", "2672de8d-bce7-4cb5-9e65-128f96b0bd9a")

        async def fake_fetch_issue_json(session, auth, key):
            assert auth.auth_mode == "bearer"
            assert auth.api_base_url == "https://api.atlassian.com/ex/jira/2672de8d-bce7-4cb5-9e65-128f96b0bd9a"
            return {
                "fields": {
                    "summary": "Implement Jira support",
                    "labels": [],
                    "subtasks": [],
                },
                "renderedFields": {"description": "<p>Hello world</p>"},
            }

        monkeypatch.setattr(jira_cloud, "_fetch_issue_json", fake_fetch_issue_json)

        result = asyncio.run(jira_cloud.fetch_jira_ticket_context("FAKE-1234 new feature", "MOCK-9999-ignored"))
        assert result.status == "fetched"
        assert result.ticket is not None

    def test_fetch_jira_ticket_context_returns_note_when_scoped_token_missing_cloud_id(self, monkeypatch):
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.setenv("JIRA_API_EMAIL", "user@example.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "token")
        monkeypatch.setenv("JIRA_AUTH_TYPE", "scoped")
        monkeypatch.delenv("JIRA_CLOUD_ID", raising=False)

        result = asyncio.run(jira_cloud.fetch_jira_ticket_context("FAKE-1234 new feature", "FAKE-1234-new"))
        assert result.status == "missing_config"
        assert result.key == "FAKE-1234"
        assert result.ticket is None
        assert "configuration is missing" in result.note

    def test_fetch_jira_ticket_context_returns_note_when_scoped_token_missing_email(self, monkeypatch):
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.delenv("JIRA_API_EMAIL", raising=False)
        monkeypatch.setenv("JIRA_API_TOKEN", "token")
        monkeypatch.setenv("JIRA_AUTH_TYPE", "scoped")
        monkeypatch.setenv("JIRA_CLOUD_ID", "2672de8d-bce7-4cb5-9e65-128f96b0bd9a")

        result = asyncio.run(jira_cloud.fetch_jira_ticket_context("FAKE-1234 new feature", "FAKE-1234-new"))
        assert result.status == "missing_config"
        assert result.key == "FAKE-1234"
        assert result.ticket is None
        assert "configuration is missing" in result.note

    def test_fetch_jira_ticket_context_returns_note_when_auth_type_is_unknown(self, monkeypatch):
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.setenv("JIRA_API_EMAIL", "user@example.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "token")
        monkeypatch.setenv("JIRA_AUTH_TYPE", "pat")

        result = asyncio.run(jira_cloud.fetch_jira_ticket_context("FAKE-1234 new feature", "FAKE-1234-new"))
        assert result.status == "missing_config"
        assert result.key == "FAKE-1234"
        assert result.ticket is None
        assert "configuration is missing" in result.note

    def test_try_fetch_jira_ticket_returns_none_without_env(self, monkeypatch):
        monkeypatch.delenv("JIRA_BASE_URL", raising=False)
        monkeypatch.delenv("JIRA_API_EMAIL", raising=False)
        monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
        monkeypatch.delenv("JIRA_AUTH_TYPE", raising=False)
        monkeypatch.delenv("JIRA_CLOUD_ID", raising=False)

        ticket = asyncio.run(jira_cloud.try_fetch_jira_ticket("FAKE-1234 new feature", "FAKE-1234-new"))
        assert ticket is None

    def test_fetch_jira_ticket_context_returns_note_when_ticket_missing(self, monkeypatch):
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.setenv("JIRA_API_EMAIL", "user@example.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "token")

        async def fake_fetch_issue_json(session, auth, key):
            return None

        monkeypatch.setattr(jira_cloud, "_fetch_issue_json", fake_fetch_issue_json)

        result = asyncio.run(jira_cloud.fetch_jira_ticket_context("FAKE-1234 new feature", "MOCK-9999-ignored"))
        assert result.status == "not_found"
        assert result.key == "FAKE-1234"
        assert result.ticket is None
        assert "FAKE-1234" in result.note
        assert "ticket compliance analysis was skipped" in result.note

    def test_fetch_jira_ticket_context_returns_note_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("JIRA_BASE_URL", raising=False)
        monkeypatch.delenv("JIRA_API_EMAIL", raising=False)
        monkeypatch.setenv("JIRA_API_TOKEN", "token")
        monkeypatch.delenv("JIRA_AUTH_TYPE", raising=False)
        monkeypatch.delenv("JIRA_CLOUD_ID", raising=False)

        result = asyncio.run(jira_cloud.fetch_jira_ticket_context("FAKE-1234 new feature", "FAKE-1234-new"))
        assert result.status == "missing_config"
        assert result.key == "FAKE-1234"
        assert result.ticket is None
        assert "configuration is missing" in result.note

    def test_fetch_jira_ticket_context_returns_no_note_when_jira_not_configured(self, monkeypatch):
        monkeypatch.delenv("JIRA_BASE_URL", raising=False)
        monkeypatch.delenv("JIRA_API_EMAIL", raising=False)
        monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
        monkeypatch.delenv("JIRA_AUTH_TYPE", raising=False)
        monkeypatch.delenv("JIRA_CLOUD_ID", raising=False)

        result = asyncio.run(jira_cloud.fetch_jira_ticket_context("FAKE-1234 new feature", "FAKE-1234-new"))
        assert result.status == "not_configured"
        assert result.key == "FAKE-1234"
        assert result.ticket is None
        assert result.note == ""
