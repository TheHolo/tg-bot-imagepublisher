import re
import unicodedata

_INVALID = re.compile(r"[^\w]+", re.UNICODE)


def normalize_tag(value: str, max_length: int = 64) -> str:
    value = unicodedata.normalize("NFKC", value).strip().lstrip("#").casefold()
    value = _INVALID.sub("_", value).strip("_")
    return value[:max_length].rstrip("_")


def normalize_tags(values: list[str], max_tags: int = 20, max_length: int = 64) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        tag = normalize_tag(raw, max_length)
        if tag and tag not in seen:
            result.append(tag)
            seen.add(tag)
        if len(result) >= max_tags:
            break
    return result


def hashtags(values: list[str]) -> str:
    return " ".join(f"#{tag}" for tag in values)


def merge_tags(
    user_tags: list[str], source_tags: list[str], max_tags: int = 20, max_length: int = 64
) -> list[str]:
    """Merge tags while preserving user priority, order, and configured limits."""
    return normalize_tags([*user_tags, *source_tags], max_tags=max_tags, max_length=max_length)
