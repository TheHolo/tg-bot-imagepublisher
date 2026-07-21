from app.utils.tags import normalize_tag, normalize_tags


def test_tag_normalization_and_deduplication():
    assert normalize_tags(["#Digital-Art", "DIGITAL_ART", " city! "]) == ["digital_art", "city"]


def test_tag_limits():
    assert normalize_tags(["abcdef", "second"], max_tags=1, max_length=3) == ["abc"]


def test_unicode_tags_are_supported():
    assert normalize_tag("#Ночной город") == "ночной_город"
