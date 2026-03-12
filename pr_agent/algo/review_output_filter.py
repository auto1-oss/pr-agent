import copy
import logging


VALID_CONFIDENCE_VALUES = {"high", "medium", "low"}
VALID_EVIDENCE_TYPES = {"diff", "ticket", "inferred"}
FINDINGS_FILTER_MODE_OFF = "off"
FINDINGS_FILTER_MODE_DROP_LOW_CONFIDENCE_INFERRED = "drop_low_confidence_inferred"
LOGGER = logging.getLogger(__name__)


def normalize_review_output(
    data: dict,
    max_findings: int,
    findings_metadata: bool = False,
    filter_mode: str = FINDINGS_FILTER_MODE_OFF,
) -> dict:
    if not isinstance(data, dict) or "review" not in data or not isinstance(data["review"], dict):
        return data

    if filter_mode != FINDINGS_FILTER_MODE_OFF and not findings_metadata:
        _log_warning("findings_filter_mode has no effect when findings_metadata is false")
        filter_mode = FINDINGS_FILTER_MODE_OFF
    elif filter_mode not in {FINDINGS_FILTER_MODE_OFF, FINDINGS_FILTER_MODE_DROP_LOW_CONFIDENCE_INFERRED}:
        _log_warning(f"Unknown findings_filter_mode: {filter_mode!r}, defaulting to off")
        filter_mode = FINDINGS_FILTER_MODE_OFF

    normalized_data = copy.deepcopy(data)
    issues = normalized_data["review"].get("key_issues_to_review")
    if not isinstance(issues, list):
        return normalized_data

    normalized_issues = []
    for issue in issues:
        normalized_issue = normalize_issue(issue, findings_metadata=findings_metadata)
        if normalized_issue:
            normalized_issues.append(normalized_issue)

    filtered_issues, suppressed_count = filter_issues(normalized_issues, filter_mode=filter_mode)
    if suppressed_count:
        _log_debug("Suppressed low-confidence inferred findings", artifact={"count": suppressed_count})
    if max_findings > 0:
        filtered_issues = filtered_issues[:max_findings]
    normalized_data["review"]["key_issues_to_review"] = filtered_issues

    return normalized_data


def normalize_issue(issue: dict, findings_metadata: bool = False) -> dict | None:
    if not isinstance(issue, dict):
        return None

    normalized_issue = copy.deepcopy(issue)
    normalized_issue["relevant_file"] = issue.get("relevant_file", "")
    normalized_issue["issue_header"] = issue.get("issue_header", "")
    normalized_issue["issue_content"] = issue.get("issue_content", "")

    if findings_metadata:
        confidence = normalize_enum_value(issue.get("confidence"), VALID_CONFIDENCE_VALUES)
        if confidence:
            normalized_issue["confidence"] = confidence
        else:
            normalized_issue.pop("confidence", None)

        evidence_type = normalize_enum_value(issue.get("evidence_type"), VALID_EVIDENCE_TYPES)
        if evidence_type:
            normalized_issue["evidence_type"] = evidence_type
        else:
            normalized_issue.pop("evidence_type", None)
    else:
        normalized_issue.pop("confidence", None)
        normalized_issue.pop("evidence_type", None)

    normalized_issue["start_line"] = issue.get("start_line", 0)
    normalized_issue["end_line"] = issue.get("end_line", 0)

    return normalized_issue


def normalize_enum_value(value, valid_values: set[str]) -> str | None:
    if value is None:
        return None

    normalized_value = str(value).strip().lower()
    if normalized_value in valid_values:
        return normalized_value

    return None


def filter_issues(issues: list[dict], filter_mode: str = FINDINGS_FILTER_MODE_OFF) -> tuple[list[dict], int]:
    if filter_mode != FINDINGS_FILTER_MODE_DROP_LOW_CONFIDENCE_INFERRED:
        return issues, 0

    filtered_issues = []
    suppressed_count = 0
    for issue in issues:
        if issue.get("confidence") == "low" and issue.get("evidence_type") == "inferred":
            suppressed_count += 1
            continue
        filtered_issues.append(issue)

    return filtered_issues, suppressed_count


def _log_warning(message: str) -> None:
    logger = _resolve_logger()
    logger.warning(message)


def _log_debug(message: str, artifact: dict) -> None:
    logger = _resolve_logger()
    try:
        logger.debug(message, artifact=artifact)
    except TypeError:
        logger.debug(message, extra={"artifact": artifact})


def _resolve_logger():
    try:
        from pr_agent.log import get_logger

        return get_logger()
    except Exception:
        return LOGGER
