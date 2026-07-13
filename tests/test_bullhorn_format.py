"""Tests for the Bullhorn clipboard formatting converter."""

from bullhorn_format import to_html, to_plaintext


SAMPLE = (
    "_Analyzed as: **Screening Call**_\n"
    "\n"
    "# Interview with John Smith\n"
    "**Recruiter:** Jane Doe\n"
    "**Date:** 2026-07-10 14:30 UTC\n"
    "\n"
    "## Work Status\n"
    "- Candidate's U.S. work status: U.S. citizen; no sponsorship needed.\n"
    "\n"
    "## Technical Screening Questions\n"
    "1. What is a closure?\n"
    '"A closure is when a function remembers its scope."\n'
    "2. Explain <T> generics & bounds.\n"
    "Not discussed.\n"
)


class TestToHtml:
    def test_h1_and_h2_become_headings(self):
        html = to_html(SAMPLE)
        assert "<h1" in html and "Interview with John Smith" in html
        assert "<h2" in html and "Work Status" in html
        assert "<h2" in html and "Technical Screening Questions" in html

    def test_bold_becomes_strong(self):
        html = to_html("**Recruiter:** Jane Doe")
        assert "<strong>Recruiter:</strong>" in html
        assert "**" not in html

    def test_bullets_become_unordered_list(self):
        html = to_html("## S\n- one\n- two\n")
        assert "<ul" in html
        assert html.count("<li") == 2
        assert "one" in html and "two" in html

    def test_numbered_become_ordered_list(self):
        html = to_html("1. first\n2. second\n")
        assert "<ol" in html
        assert html.count("<li") == 2

    def test_switching_list_type_closes_previous(self):
        html = to_html("- bullet\n1. number\n")
        assert "<ul" in html and "<ol" in html

    def test_content_is_html_escaped(self):
        # Angle brackets / ampersands from a verbatim quote must not become tags.
        html = to_html("2. Explain <T> generics & bounds.")
        assert "&lt;T&gt;" in html
        assert "&amp;" in html
        assert "<T>" not in html

    def test_no_raw_markdown_symbols_leak(self):
        html = to_html(SAMPLE)
        assert "##" not in html
        assert "**" not in html

    def test_empty_input(self):
        assert to_html("") == ""
        assert to_html(None) == ""


class TestToPlaintext:
    def test_strips_heading_hashes(self):
        text = to_plaintext("# Interview with John\n## Work Status")
        assert "Interview with John" in text
        assert "Work Status" in text
        assert "#" not in text

    def test_strips_bold_markers(self):
        text = to_plaintext("**Recruiter:** Jane Doe")
        assert "Recruiter: Jane Doe" in text
        assert "*" not in text

    def test_bullets_use_bullet_char(self):
        text = to_plaintext("- one\n- two")
        assert "• one" in text
        assert "• two" in text
        assert "- one" not in text

    def test_numbered_preserved(self):
        text = to_plaintext("1. first\n2. second")
        assert "1. first" in text
        assert "2. second" in text

    def test_underscore_in_word_preserved(self):
        # Underscores inside identifiers (common in verbatim tech answers)
        # must not be swallowed as italic markers.
        text = to_plaintext("Used model gpt_4o_mini in the pipeline.")
        assert "gpt_4o_mini" in text

    def test_no_markdown_symbols_leak(self):
        text = to_plaintext(SAMPLE)
        assert "##" not in text
        assert "**" not in text

    def test_collapses_excess_blank_lines(self):
        text = to_plaintext("a\n\n\n\n\nb")
        assert "a\n\nb" == text

    def test_empty_input(self):
        assert to_plaintext("") == ""
        assert to_plaintext(None) == ""
