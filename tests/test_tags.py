from app.utils.tags import merge_tags, normalize_tag, normalize_tags


def test_tag_normalization_and_deduplication():
    assert normalize_tags(["#Digital-Art", "DIGITAL_ART", " city! "]) == ["digital_art", "city"]


def test_tag_limits():
    assert normalize_tags(["abcdef", "second"], max_tags=1, max_length=3) == ["abc"]


def test_unicode_tags_are_supported():
    assert normalize_tag("#Ночной город") == "ночной_город"


def test_source_tags_are_merged_after_user_tags_without_duplicates():
    assert merge_tags(["landscape", "ai"], ["AI", "狼と香辛料", "ホロ"]) == [
        "landscape", "ai", "狼と香辛料", "ホロ"
    ]


def test_user_tags_take_priority_when_limit_is_reached():
    assert merge_tags(["user_one", "user_two"], ["source"], max_tags=2) == ["user_one", "user_two"]
