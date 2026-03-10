import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import aiohttp
import html2text


def _get_logger():
    # Lazy import to avoid circular imports during Dynaconf initialization.
    try:
        from pr_agent.log import get_logger

        return get_logger()
    except Exception:
        import logging

        return logging.getLogger(__name__)


MAX_TICKET_CHARACTERS = 10000
DEFAULT_TIMEOUT_SECONDS = 10


_JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}-[1-9]\d{0,6}$")

# Prefix-only extraction:
# - PR title example: "OPS-1234 new feature", allow minor variants like "[OPS-1234] ..." or "OPS-1234: ...".
_TITLE_PREFIX_RE = re.compile(r"^\s*[\[(]?(?P<key>[A-Z][A-Z0-9]{1,9}-[1-9]\d{0,6})[\])]?([\s:]|$)")
_BRANCH_PREFIX_RE = re.compile(r"^(?P<key>[A-Z][A-Z0-9]{1,9}-[1-9]\d{0,6})(?:[-_/].*)?$")


@dataclass(frozen=True)
class JiraCloudAuth:
    site_base_url: str
    api_base_url: str
    api_token: str
    auth_mode: str
    email: str = ""
    cloud_id: str = ""


@dataclass(frozen=True)
class JiraTicketFetchResult:
    status: str
    key: Optional[str] = None
    ticket: Optional[Dict[str, Any]] = None
    note: str = ""


def _normalize_jira_base_url(raw_url: str) -> str:
    raw_url = (raw_url or "").strip()
    if not raw_url:
        return ""

    if not raw_url.startswith(("http://", "https://")):
        raw_url = f"https://{raw_url}"

    parsed = urlparse(raw_url)
    # Keep only scheme + netloc + path (drop query/fragment)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc
    path = (parsed.path or "").rstrip("/")

    # Cloud UI sometimes uses the /jira path. REST APIs are served from the root.
    if netloc.endswith(".atlassian.net") and path == "/jira":
        path = ""

    normalized = urlunparse((scheme, netloc, path, "", "", "")).rstrip("/")
    return normalized


def _build_jira_api_base_url(cloud_id: str) -> str:
    return f"https://api.atlassian.com/ex/jira/{cloud_id}"


def _load_auth_from_env() -> Optional[JiraCloudAuth]:
    base_url = _normalize_jira_base_url(os.getenv("JIRA_BASE_URL", ""))
    email = (os.getenv("JIRA_API_EMAIL", "") or "").strip()
    api_token = (os.getenv("JIRA_API_TOKEN", "") or "").strip()
    cloud_id = (os.getenv("JIRA_CLOUD_ID", "") or "").strip()
    auth_type = (os.getenv("JIRA_AUTH_TYPE", "") or "").strip().lower()

    if not base_url or not api_token:
        return None

    # Scoped Atlassian API tokens use api.atlassian.com URLs with Basic auth and require a cloud id.
    # Implicit scoped detection: if both JIRA_CLOUD_ID and JIRA_API_EMAIL are set, prefer scoped mode.
    if auth_type == "scoped" or (not auth_type and cloud_id and email):
        if not email or not cloud_id:
            return None
        return JiraCloudAuth(
            site_base_url=base_url,
            api_base_url=_build_jira_api_base_url(cloud_id),
            api_token=api_token,
            auth_mode="scoped",
            email=email,
            cloud_id=cloud_id,
        )

    if auth_type in {"bearer", "oauth", "oauth2", "oauth_3lo"}:
        return JiraCloudAuth(
            site_base_url=base_url,
            api_base_url=_build_jira_api_base_url(cloud_id) if cloud_id else "",
            api_token=api_token,
            auth_mode="bearer",
            email=email,
            cloud_id=cloud_id,
        )

    if auth_type not in {"", "basic"}:
        _get_logger().warning(f"Unrecognized JIRA_AUTH_TYPE '{auth_type}'")
        return None

    if not email:
        return None

    return JiraCloudAuth(
        site_base_url=base_url,
        api_base_url=base_url,
        api_token=api_token,
        auth_mode="basic",
        email=email,
    )


def _has_any_jira_env() -> bool:
    return any(
        (
            (os.getenv("JIRA_BASE_URL", "") or "").strip(),
            (os.getenv("JIRA_API_EMAIL", "") or "").strip(),
            (os.getenv("JIRA_API_TOKEN", "") or "").strip(),
            (os.getenv("JIRA_CLOUD_ID", "") or "").strip(),
            (os.getenv("JIRA_AUTH_TYPE", "") or "").strip(),
        )
    )


def extract_jira_ticket_key_from_pr_title(pr_title: str) -> Optional[str]:
    if not pr_title:
        return None
    match = _TITLE_PREFIX_RE.match(pr_title.strip())
    if not match:
        return None
    return match.group("key")


def extract_jira_ticket_key_from_branch(branch: str) -> Optional[str]:
    if not branch:
        return None
    branch = branch.strip()
    if branch.startswith("refs/heads/"):
        branch = branch[len("refs/heads/") :]
    match = _BRANCH_PREFIX_RE.match(branch)
    if not match:
        return None
    return match.group("key")


def extract_jira_ticket_key(pr_title: str, branch: str) -> Optional[str]:
    # precedence: PR title first, then branch
    return extract_jira_ticket_key_from_pr_title(pr_title) or extract_jira_ticket_key_from_branch(branch)


def _build_ticket_mismatch_note(title_key: str, branch_key: str) -> str:
    return (
        f"PR title references Jira ticket `{title_key}` but branch name references `{branch_key}`. "
        "Please verify that the correct Jira ticket is used in the PR metadata."
    )


def _append_note(note: str, extra_note: str) -> str:
    note = (note or "").strip()
    extra_note = (extra_note or "").strip()
    if not note:
        return extra_note
    if not extra_note:
        return note
    return f"{note}\n\n{extra_note}"


def _clip_text(text: str, limit: int = MAX_TICKET_CHARACTERS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _adf_to_text(adf: Any) -> str:
    """Best-effort extraction of human-readable text from Atlassian Document Format (ADF)."""
    parts: List[str] = []

    def walk(node: Any, depth: int = 0):
        if depth > 50:
            return
        if node is None:
            return
        if isinstance(node, str):
            parts.append(node)
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
            return
        if isinstance(node, dict):
            node_type = node.get("type")
            if node_type == "text" and "text" in node:
                parts.append(str(node.get("text") or ""))
                return
            content = node.get("content")
            if content is not None:
                walk(content, depth + 1)
                # Add lightweight paragraph separation when we see block-ish nodes
                if node_type in {"paragraph", "heading", "blockquote", "listItem"}:
                    parts.append("\n")
            return

    walk(adf)
    text = "".join(parts)
    # Normalize excessive whitespace/newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _jira_issue_url(base_url: str, key: str) -> str:
    return f"{base_url.rstrip('/')}/browse/{key}"


def _extract_resource_urls(resources: Any) -> List[str]:
    urls: List[str] = []
    if not isinstance(resources, list):
        return urls
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        url = resource.get("url")
        if isinstance(url, str) and url:
            urls.append(_normalize_jira_base_url(url))
    return urls


def _build_ticket_compliance_note(key: str, reason: str) -> str:
    reason_to_message = {
        "missing_config": "Jira Cloud configuration is missing",
        "not_found": "the Jira ticket was not found",
        "auth_failed": "Jira authentication failed",
        "fetch_error": "the Jira ticket could not be fetched",
    }
    message = reason_to_message.get(reason, "the Jira ticket could not be fetched")
    return (
        f"Jira ticket `{key}` was detected in the PR title or branch, but {message}, "
        "so ticket compliance analysis was skipped."
    )


def _truncate_for_log(value: str, limit: int = 300) -> str:
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


async def _extract_error_message(resp: aiohttp.ClientResponse) -> str:
    try:
        body = await resp.text()
    except Exception:
        return ""

    body = (body or "").replace("\n", " ").strip()
    if not body:
        return ""

    try:
        parsed = json.loads(body)
    except Exception:
        return _truncate_for_log(body)

    error_messages = parsed.get("errorMessages") if isinstance(parsed, dict) else None
    if isinstance(error_messages, list) and error_messages:
        return _truncate_for_log("; ".join([str(message) for message in error_messages if message]))

    errors = parsed.get("errors") if isinstance(parsed, dict) else None
    if isinstance(errors, dict) and errors:
        return _truncate_for_log("; ".join([f"{k}: {v}" for k, v in errors.items()]))

    message = parsed.get("message") if isinstance(parsed, dict) else None
    if message:
        return _truncate_for_log(str(message))

    return _truncate_for_log(body)


async def _discover_cloud_id(session: aiohttp.ClientSession, site_base_url: str) -> Optional[str]:
    discovery_url = "https://api.atlassian.com/oauth/token/accessible-resources"
    normalized_site_base_url = _normalize_jira_base_url(site_base_url)
    _get_logger().info(f"Discovering Jira cloud id for {normalized_site_base_url}")

    async with session.get(discovery_url) as resp:
        if resp.status in (401, 403):
            error_message = await _extract_error_message(resp)
            if error_message:
                raise PermissionError(f"Jira cloud id discovery failed (HTTP {resp.status}): {error_message}")
            raise PermissionError(f"Jira cloud id discovery failed (HTTP {resp.status})")
        resp.raise_for_status()
        resources = await resp.json()

    for resource in resources if isinstance(resources, list) else []:
        if not isinstance(resource, dict):
            continue
        resource_url = _normalize_jira_base_url(str(resource.get("url") or ""))
        if resource_url == normalized_site_base_url:
            cloud_id = str(resource.get("id") or "").strip()
            if cloud_id:
                _get_logger().info(f"Discovered Jira cloud id {cloud_id} for {normalized_site_base_url}")
                return cloud_id

    resource_urls = _extract_resource_urls(resources)
    _get_logger().warning(
        f"Could not match Jira site {normalized_site_base_url} in accessible resources: {resource_urls}"
    )
    return None


async def _resolve_auth(session: aiohttp.ClientSession, auth: JiraCloudAuth) -> Optional[JiraCloudAuth]:
    if auth.auth_mode == "scoped":
        # api_base_url and cloud_id are validated by _load_auth_from_env for scoped tokens.
        return auth

    if auth.auth_mode != "bearer":
        return auth

    if auth.cloud_id and auth.api_base_url:
        _get_logger().info(f"Using configured Jira cloud id {auth.cloud_id} for {auth.site_base_url}")
        return auth

    cloud_id = await _discover_cloud_id(session, auth.site_base_url)
    if not cloud_id:
        return None

    return JiraCloudAuth(
        site_base_url=auth.site_base_url,
        api_base_url=_build_jira_api_base_url(cloud_id),
        api_token=auth.api_token,
        auth_mode=auth.auth_mode,
        email=auth.email,
        cloud_id=cloud_id,
    )


async def _fetch_issue_json(session: aiohttp.ClientSession, auth: JiraCloudAuth, key: str) -> Optional[Dict[str, Any]]:
    url = f"{auth.api_base_url.rstrip('/')}/rest/api/3/issue/{key}"
    params = {
        "fields": "summary,description,labels,subtasks",
        "expand": "renderedFields",
    }

    _get_logger().info(f"Requesting Jira ticket {key} using {auth.auth_mode} auth from {auth.api_base_url}")

    async with session.get(url, params=params) as resp:
        if resp.status == 404:
            error_message = await _extract_error_message(resp)
            if error_message:
                _get_logger().warning(
                    f"Jira issue lookup for {key} at {auth.api_base_url} returned 404: {error_message}"
                )
            else:
                _get_logger().warning(f"Jira issue lookup for {key} at {auth.api_base_url} returned 404")
            return None
        if resp.status in (401, 403):
            # Include Jira's capped error message in logs for auth diagnostics.
            error_message = await _extract_error_message(resp)
            if error_message:
                raise PermissionError(f"Jira auth failed (HTTP {resp.status}): {error_message}")
            raise PermissionError(f"Jira auth failed (HTTP {resp.status})")
        if resp.status >= 400:
            error_message = await _extract_error_message(resp)
            if error_message:
                _get_logger().warning(
                    f"Jira issue lookup for {key} at {auth.api_base_url} returned HTTP {resp.status}: {error_message}"
                )
            else:
                _get_logger().warning(f"Jira issue lookup for {key} at {auth.api_base_url} returned HTTP {resp.status}")
        resp.raise_for_status()
        return await resp.json()


def _render_description_to_text(issue: Dict[str, Any]) -> str:
    rendered_fields = issue.get("renderedFields") or {}
    rendered_html = rendered_fields.get("description")
    if isinstance(rendered_html, str) and rendered_html.strip():
        try:
            # html2text produces markdown-ish plain text, which is a good prompt format.
            return html2text.html2text(rendered_html).strip()
        except Exception:
            # Fall back to raw HTML if conversion fails.
            return rendered_html

    fields = issue.get("fields") or {}
    desc = fields.get("description")
    if isinstance(desc, str):
        return desc
    if isinstance(desc, (dict, list)):
        text = _adf_to_text(desc)
        if text:
            return text
        return json.dumps(desc, ensure_ascii=True)
    return ""


def _extract_labels(issue: Dict[str, Any]) -> str:
    fields = issue.get("fields") or {}
    labels = fields.get("labels") or []
    if not isinstance(labels, list):
        return ""
    return ", ".join(str(label) for label in labels if label is not None)


def _extract_subtasks_as_sub_issues(issue: Dict[str, Any], base_url: str, limit: int = 5) -> List[Dict[str, str]]:
    fields = issue.get("fields") or {}
    subtasks = fields.get("subtasks") or []
    if not isinstance(subtasks, list) or not subtasks:
        return []

    sub_issues: List[Dict[str, str]] = []
    for subtask in subtasks[:limit]:
        if not isinstance(subtask, dict):
            continue
        key = subtask.get("key")
        if not isinstance(key, str) or not _JIRA_KEY_RE.match(key):
            continue
        summary = ""
        sub_fields = subtask.get("fields")
        if isinstance(sub_fields, dict):
            summary_val = sub_fields.get("summary")
            if isinstance(summary_val, str):
                summary = summary_val
        sub_issues.append(
            {
                "ticket_url": _jira_issue_url(base_url, key),
                "title": summary or key,
                "body": "",
                "labels": "",
            }
        )

    return sub_issues


async def fetch_jira_ticket_context(pr_title: str, branch: str) -> JiraTicketFetchResult:
    title_key = extract_jira_ticket_key_from_pr_title(pr_title)
    branch_key = extract_jira_ticket_key_from_branch(branch)
    key = title_key or branch_key
    mismatch_note = ""
    if title_key and branch_key and title_key != branch_key:
        mismatch_note = _build_ticket_mismatch_note(title_key, branch_key)
    if not key:
        _get_logger().info("No Jira ticket key found in PR title or branch")
        return JiraTicketFetchResult(status="no_key")

    _get_logger().info(f"Detected Jira ticket key {key} from PR metadata")

    auth = _load_auth_from_env()
    if not auth:
        if not _has_any_jira_env():
            _get_logger().info(f"Detected Jira ticket key {key}, but Jira integration is not configured")
            return JiraTicketFetchResult(status="not_configured", key=key, note=mismatch_note)
        _get_logger().warning(f"Detected Jira ticket key {key}, but Jira Cloud env configuration is missing")
        return JiraTicketFetchResult(
            status="missing_config",
            key=key,
            note=_append_note(_build_ticket_compliance_note(key, "missing_config"), mismatch_note),
        )

    timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS)
    session_kwargs: Dict[str, Any] = {"timeout": timeout, "headers": {"Accept": "application/json"}}
    if auth.auth_mode in {"basic", "scoped"}:
        session_kwargs["auth"] = aiohttp.BasicAuth(login=auth.email, password=auth.api_token)
    else:
        session_kwargs["headers"].update(
            {
                "Authorization": f"Bearer {auth.api_token}",
            }
        )
    _get_logger().info(f"Using Jira auth mode {auth.auth_mode} for {auth.site_base_url}")

    try:
        async with aiohttp.ClientSession(**session_kwargs) as session:
            resolved_auth = await _resolve_auth(session, auth)
            if not resolved_auth:
                return JiraTicketFetchResult(
                    status="fetch_error",
                    key=key,
                    note=_append_note(_build_ticket_compliance_note(key, "fetch_error"), mismatch_note),
                )

            issue = await _fetch_issue_json(session, resolved_auth, key)
            if not issue:
                _get_logger().warning(f"Jira ticket {key} was not found")
                return JiraTicketFetchResult(
                    status="not_found",
                    key=key,
                    note=_append_note(_build_ticket_compliance_note(key, "not_found"), mismatch_note),
                )

            fields = issue.get("fields") or {}
            summary = fields.get("summary") if isinstance(fields.get("summary"), str) else ""
            body = _clip_text(_render_description_to_text(issue))
            labels = _extract_labels(issue)
            sub_issues = _extract_subtasks_as_sub_issues(issue, resolved_auth.site_base_url)

            ticket = {
                "ticket_id": key,
                "ticket_url": _jira_issue_url(resolved_auth.site_base_url, key),
                "title": summary or key,
                "body": body,
                "labels": labels,
            }
            if sub_issues:
                ticket["sub_issues"] = sub_issues
            _get_logger().info(f"Fetched Jira ticket {key} for PR ticket context")
            return JiraTicketFetchResult(status="fetched", key=key, ticket=ticket, note=mismatch_note)
    except PermissionError as e:
        # Do not include raw exception details that could accidentally expose auth state.
        _get_logger().warning(f"Jira ticket fetch skipped: {e}")
        return JiraTicketFetchResult(
            status="auth_failed",
            key=key,
            note=_append_note(_build_ticket_compliance_note(key, "auth_failed"), mismatch_note),
        )
    except aiohttp.ClientResponseError as e:
        _get_logger().warning(f"Jira ticket fetch failed (HTTP {e.status}) for {key}")
        return JiraTicketFetchResult(
            status="fetch_error",
            key=key,
            note=_append_note(_build_ticket_compliance_note(key, "fetch_error"), mismatch_note),
        )
    except Exception as e:
        _get_logger().warning(f"Jira ticket fetch failed for {key}: {type(e).__name__}")
        return JiraTicketFetchResult(
            status="fetch_error",
            key=key,
            note=_append_note(_build_ticket_compliance_note(key, "fetch_error"), mismatch_note),
        )


async def try_fetch_jira_ticket(pr_title: str, branch: str) -> Optional[Dict[str, Any]]:
    result = await fetch_jira_ticket_context(pr_title, branch)
    return result.ticket
