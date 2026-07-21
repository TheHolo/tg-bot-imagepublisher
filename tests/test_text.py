from app.utils.text import plain_text, shorten


def test_html_description_is_converted_to_plain_text():
    assert plain_text("<p>Hello<br>world &amp; friends</p>") == "Hello world & friends"


def test_description_is_shortened_on_word_boundary():
    assert shorten("one two three four", 14) == "one two three…"
