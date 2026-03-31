import copy
import logging


VALID_CONFIDENCE_VALUES = {"high", "medium", "low"}
VALID_EVIDENCE_TYPES = {"diff", "ticket", "inferred"}
FINDINGS_FILTER_MODE_OFF = "off"
FINDINGS_FILTER_MODE_DROP_LOW_CONFIDENCE_INFERRED = "drop_low_confidence_inferred"
LOGGER = logging.getLogger(__name__)


def normalize_code_suggestions_output(
    data: dict,
    findings_metadata: bool = False,
    filter_mode: str = FINDINGS_FILTER_MODE_OFF,
) -> dict:
    if not isinstance(data, dict) or "code_suggestions" not in data or not isinstance(data["code_suggestions"], list):
        return data

    if filter_mode != FINDINGS_FILTER_MODE_OFF and not findings_metadata:
        _log_warning("findings_filter_mode has no effect when findings_metadata is false")
        filter_mode = FINDINGS_FILTER_MODE_OFF
    elif filter_mode not in {FINDINGS_FILTER_MODE_OFF, FINDINGS_FILTER_MODE_DROP_LOW_CONFIDENCE_INFERRED}:
        _log_warning(f"Unknown findings_filter_mode: {filter_mode!r}, defaulting to off")
        filter_mode = FINDINGS_FILTER_MODE_OFF

    normalized_data = copy.deepcopy(data)
    suggestions = normalized_data.get("code_suggestions")
    if not isinstance(suggestions, list):
        return normalized_data

    normalized_suggestions = []
    for suggestion in suggestions:
        normalized_suggestion = normalize_suggestion(suggestion, findings_metadata=findings_metadata)
        if normalized_suggestion:
            normalized_suggestions.append(normalized_suggestion)

    filtered_suggestions, suppressed_count = filter_suggestions(normalized_suggestions, filter_mode=filter_mode)
    if suppressed_count:
        _log_debug("Suppressed low-confidence inferred code suggestions", artifact={"count": suppressed_count})
    normalized_data["code_suggestions"] = filtered_suggestions

    return normalized_data


def normalize_suggestion(suggestion: dict, findings_metadata: bool = False) -> dict | None:
    if not isinstance(suggestion, dict):
        return None

    normalized_suggestion = copy.deepcopy(suggestion)

    if findings_metadata:
        confidence = normalize_enum_value(suggestion.get("confidence"), VALID_CONFIDENCE_VALUES)
        if confidence:
            normalized_suggestion["confidence"] = confidence
        else:
            normalized_suggestion.pop("confidence", None)

        evidence_type = normalize_enum_value(suggestion.get("evidence_type"), VALID_EVIDENCE_TYPES)
        if evidence_type:
            normalized_suggestion["evidence_type"] = evidence_type
        else:
            normalized_suggestion.pop("evidence_type", None)
    else:
        normalized_suggestion.pop("confidence", None)
        normalized_suggestion.pop("evidence_type", None)

    return normalized_suggestion


def normalize_enum_value(value, valid_values: set[str]) -> str | None:
    if value is None:
        return None

    normalized_value = str(value).strip().lower()
    if normalized_value in valid_values:
        return normalized_value

    return None


def filter_suggestions(suggestions: list[dict], filter_mode: str = FINDINGS_FILTER_MODE_OFF) -> tuple[list[dict], int]:
    if filter_mode != FINDINGS_FILTER_MODE_DROP_LOW_CONFIDENCE_INFERRED:
        return suggestions, 0

    filtered_suggestions = []
    suppressed_count = 0
    for suggestion in suggestions:
        if suggestion.get("confidence") == "low" and suggestion.get("evidence_type") == "inferred":
            suppressed_count += 1
            continue
        filtered_suggestions.append(suggestion)

    return filtered_suggestions, suppressed_count


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
