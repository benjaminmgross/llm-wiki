"""Tests for the shared CommonMark adapter."""

from mdwiki.markdown import markdown_inline_links, markdown_links


def test_markdown_inline_links_delegates_commonmark_forms_to_parser() -> None:
    content = """# Links

[Inline *label*](inline(v2).md "Context") and [Reference][ref].

`[code](ignored.md)`

```md
[fenced](ignored.md)
```

[ref]: <target file.md> 'Reference title'
"""

    assert markdown_inline_links(content) == [
        ("Inline label", "inline(v2).md"),
        ("Reference", "target%20file.md"),
    ]

    links = markdown_links(content)
    assert links[0].source_markup == '[Inline *label*](inline(v2).md "Context")'
    assert links[0].source_text == "Inline *label*"
    assert links[1].source_markup == "[Reference][ref]"
    assert links[1].source_text == "Reference"
