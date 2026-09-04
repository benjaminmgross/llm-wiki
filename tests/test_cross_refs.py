from pathlib import Path

import pytest

from mdwiki.cross_refs import materialize_cross_refs
from mdwiki.plan import CrossRef, Plan, Update


def _plan(*refs: CrossRef) -> Plan:
    return Plan(verdict="ingest", rationale="test", updates=(), new_pages=(), cross_refs=refs)


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    concepts = tmp_path / "wiki" / "concepts"
    concepts.mkdir(parents=True)
    (concepts / "source.md").write_text("# Source\n")
    (concepts / "target.md").write_text("# Target\n")
    (concepts / "other.md").write_text("# Other\n")
    return tmp_path


def test_materialization_deduplicates_and_orders_edges_idempotently(wiki: Path) -> None:
    refs = (
        CrossRef("wiki/concepts/source.md", "wiki/concepts/target.md", "Zulu"),
        CrossRef("wiki/concepts/source.md", "wiki/concepts/other.md", "Other"),
        CrossRef("wiki/concepts/source.md", "wiki/concepts/target.md", "Alpha"),
    )
    first = materialize_cross_refs(wiki_root=wiki, plan=_plan(*refs))["wiki/concepts/source.md"]
    (wiki / "wiki/concepts/source.md").write_text(first)
    second = materialize_cross_refs(wiki_root=wiki, plan=_plan(*refs))["wiki/concepts/source.md"]

    assert first == second
    assert first.count("(target.md)") == 1
    assert first.index("(other.md)") < first.index("(target.md)")


def test_updated_page_preserves_existing_managed_edges(wiki: Path) -> None:
    source = wiki / "wiki/concepts/source.md"
    source.write_text(
        "# Source\n\nOld body.\n\n"
        "<!-- mdwiki:cross-refs -->\n"
        "## Related\n\n"
        "- [Existing target](target.md)\n"
        "<!-- /mdwiki:cross-refs -->\n"
    )
    plan = Plan(
        verdict="ingest",
        rationale="test",
        updates=(Update(page="wiki/concepts/source.md", content="# Source\n\nRevised body.\n", claims=()),),
        new_pages=(),
        cross_refs=(CrossRef("wiki/concepts/source.md", "wiki/concepts/other.md", "New target"),),
    )

    body = materialize_cross_refs(wiki_root=wiki, plan=plan)["wiki/concepts/source.md"]

    assert "Revised body." in body
    assert "Old body." not in body
    assert "- [Existing target](target.md)" in body
    assert "- [New target](other.md)" in body


def test_updated_page_preserves_managed_edges_without_new_outbound_refs(wiki: Path) -> None:
    source = wiki / "wiki/concepts/source.md"
    source.write_text(
        "# Source\n\nOld body.\n\n"
        "<!-- mdwiki:cross-refs -->\n"
        "## Related\n\n"
        "- [Existing target](target.md)\n"
        "<!-- /mdwiki:cross-refs -->\n"
    )
    plan = Plan(
        verdict="ingest",
        rationale="test",
        updates=(Update(page="wiki/concepts/source.md", content="# Source\n\nRevised body.\n", claims=()),),
        new_pages=(),
        cross_refs=(),
    )

    first = materialize_cross_refs(wiki_root=wiki, plan=plan)["wiki/concepts/source.md"]
    source.write_text(first)
    second = materialize_cross_refs(wiki_root=wiki, plan=plan)["wiki/concepts/source.md"]

    assert "Revised body." in first
    assert "- [Existing target](target.md)" in first
    assert second == first


def test_update_does_not_resurrect_removed_ordinary_link_and_suppresses_managed_duplicate(wiki: Path) -> None:
    source = wiki / "wiki/concepts/source.md"
    source.write_text(
        "# Source\n\nOld [ordinary link](other.md).\n\n"
        "<!-- mdwiki:cross-refs -->\n"
        "## Related\n\n"
        "- [Managed target](target.md)\n"
        "<!-- /mdwiki:cross-refs -->\n"
    )
    plan = Plan(
        verdict="ingest",
        rationale="test",
        updates=(
            Update(
                page="wiki/concepts/source.md",
                content="# Source\n\nRevised [ordinary target](target.md).\n",
                claims=(),
            ),
        ),
        new_pages=(),
        cross_refs=(),
    )

    body = materialize_cross_refs(wiki_root=wiki, plan=plan)["wiki/concepts/source.md"]

    assert "other.md" not in body
    assert body.count("(target.md)") == 1
    assert "mdwiki:cross-refs" not in body


def test_ordinary_markdown_link_prevents_redundant_managed_link(wiki: Path) -> None:
    source = wiki / "wiki/concepts/source.md"
    source.write_text("# Source\n\nSee [the target](target.md).\n")

    body = materialize_cross_refs(
        wiki_root=wiki,
        plan=_plan(CrossRef("wiki/concepts/source.md", "wiki/concepts/target.md", "Target")),
    )["wiki/concepts/source.md"]

    assert "mdwiki:cross-refs" not in body


def test_reference_style_link_prevents_redundant_managed_link(wiki: Path) -> None:
    source = wiki / "wiki/concepts/source.md"
    original = '# Source\n\nSee [the target][target-ref].\n\n[target-ref]: target.md "Context"\n'
    source.write_text(original)
    ref = CrossRef("wiki/concepts/source.md", "wiki/concepts/target.md", "Target")

    first = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]
    source.write_text(first)
    second = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]

    assert first == original
    assert second == first
    assert "mdwiki:cross-refs" not in first


def test_first_materialization_preserves_every_existing_byte(wiki: Path) -> None:
    source = wiki / "wiki/concepts/source.md"
    original = "# Source\n\nMeaningful trailing spaces stay.  \n\t"
    source.write_text(original)

    body = materialize_cross_refs(
        wiki_root=wiki,
        plan=_plan(CrossRef("wiki/concepts/source.md", "wiki/concepts/target.md", "Target")),
    )["wiki/concepts/source.md"]

    assert body.startswith(f"{original}\n\n<!-- mdwiki:cross-refs -->")


def test_replacement_preserves_bytes_outside_owned_block(wiki: Path) -> None:
    source = wiki / "wiki/concepts/source.md"
    prefix = "# Source\n\nBefore.  \n\n"
    owned = "<!-- mdwiki:cross-refs -->\n## Related\n\n- [Other](other.md)\n<!-- /mdwiki:cross-refs -->\n"
    suffix = " \nAfter.  \n\t"
    source.write_text(f"{prefix}{owned}{suffix}")

    ref = CrossRef("wiki/concepts/source.md", "wiki/concepts/target.md", "Target")
    first = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]
    source.write_text(first)
    second = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]

    assert first.startswith(f"{prefix}{suffix}\n\n<!-- mdwiki:cross-refs -->")
    assert second == first


@pytest.mark.parametrize(
    "example",
    [
        "  <!-- mdwiki:cross-refs -->\nUser text.\n<!-- /mdwiki:cross-refs -->\n",
        "> <!-- mdwiki:cross-refs -->\n> User text.\n> <!-- /mdwiki:cross-refs -->\n",
        "- <!-- mdwiki:cross-refs -->\n  User text.\n  <!-- /mdwiki:cross-refs -->\n",
    ],
)
def test_marker_examples_inside_other_markdown_are_not_owned(wiki: Path, example: str) -> None:
    source = wiki / "wiki/concepts/source.md"
    original = f"# Source\n\n{example}"
    source.write_text(original)

    body = materialize_cross_refs(
        wiki_root=wiki,
        plan=_plan(CrossRef("wiki/concepts/source.md", "wiki/concepts/target.md", "Target")),
    )["wiki/concepts/source.md"]

    assert body.startswith(original)
    assert "User text." in body
    assert "- [Target](target.md)" in body


@pytest.mark.parametrize(
    ("destination", "target_name"),
    [
        ('target.md "Helpful title"', "target.md"),
        ("target.md 'Helpful title'", "target.md"),
        ("target.md (Helpful title)", "target.md"),
        ('target(v2).md "Helpful title"', "target(v2).md"),
        ('<target.md> "Helpful title"', "target.md"),
        ("<target.md> 'Helpful title'", "target.md"),
        ("<target.md> (Helpful title)", "target.md"),
    ],
)
def test_ordinary_markdown_link_title_prevents_redundant_managed_link(wiki: Path, destination: str, target_name: str) -> None:
    source = wiki / "wiki/concepts/source.md"
    (wiki / "wiki" / "concepts" / target_name).write_text("# Target\n")
    original = f"# Source\n\nSee [the target]({destination}).\n"
    source.write_text(original)
    ref = CrossRef("wiki/concepts/source.md", f"wiki/concepts/{target_name}", "Target")

    first = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]
    source.write_text(first)
    second = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]

    assert first == original
    assert second == first
    assert "mdwiki:cross-refs" not in first


@pytest.mark.parametrize(
    "destination",
    ['target.md "Helpful title"', "target.md 'Helpful title'", "target.md (Helpful title)", '<target.md> "Helpful title"'],
)
def test_ordinary_titled_link_removes_redundant_existing_managed_link(wiki: Path, destination: str) -> None:
    source = wiki / "wiki/concepts/source.md"
    source.write_text(
        f"# Source\n\nSee [the target]({destination}).\n\n"
        "<!-- mdwiki:cross-refs -->\n"
        "## Related\n\n"
        "- [Redundant target](target.md)\n"
        "- [Other](other.md)\n"
        "<!-- /mdwiki:cross-refs -->\n"
    )
    ref = CrossRef("wiki/concepts/source.md", "wiki/concepts/target.md", "Target")

    first = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]
    source.write_text(first)
    second = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]

    assert second == first
    assert first.count("- [Redundant target](target.md)") == 0
    assert destination in first
    assert "- [Other](other.md)" in first


def test_ordinary_link_removes_redundant_only_managed_block(wiki: Path) -> None:
    source = wiki / "wiki/concepts/source.md"
    original = (
        "# Source\n\n"
        "See [the target](target.md).\n\n"
        "<!-- mdwiki:cross-refs -->\n"
        "## Related\n\n"
        "- [Redundant target](target.md)\n"
        "<!-- /mdwiki:cross-refs -->\n"
    )
    source.write_text(original)

    body = materialize_cross_refs(
        wiki_root=wiki,
        plan=_plan(CrossRef("wiki/concepts/source.md", "wiki/concepts/target.md", "Target")),
    )["wiki/concepts/source.md"]

    assert body == "# Source\n\nSee [the target](target.md).\n\n"
    assert "mdwiki:cross-refs" not in body


@pytest.mark.parametrize(
    ("destination", "target_name"),
    [("target(v2).md", "target(v2).md"), ("<target (v2).md>", "target (v2).md")],
)
def test_ordinary_markdown_link_with_parenthesized_destination_is_idempotent(wiki: Path, destination: str, target_name: str) -> None:
    target = wiki / "wiki/concepts" / target_name
    target.write_text("# Target v2\n")
    source = wiki / "wiki/concepts/source.md"
    original = f"# Source\n\nSee [the target]({destination}).\n"
    source.write_text(original)
    ref = CrossRef("wiki/concepts/source.md", f"wiki/concepts/{target_name}", "Target v2")

    first = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]
    source.write_text(first)
    second = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]

    assert first == original
    assert second == first
    assert "mdwiki:cross-refs" not in first


@pytest.mark.parametrize(
    ("destination", "target_name", "encoded_target"),
    [
        ("target(v2).md", "target(v2).md", "target%28v2%29.md"),
        ("<target (v2).md>", "target (v2).md", "target%20%28v2%29.md"),
    ],
)
def test_ordinary_link_removes_redundant_existing_managed_link(wiki: Path, destination: str, target_name: str, encoded_target: str) -> None:
    target = wiki / "wiki/concepts" / target_name
    target.write_text("# Target v2\n")
    source = wiki / "wiki/concepts/source.md"
    source.write_text(
        f"# Source\n\nSee [the target]({destination}).\n\n"
        "<!-- mdwiki:cross-refs -->\n"
        "## Related\n\n"
        f"- [Redundant target]({encoded_target})\n"
        "- [Other](other.md)\n"
        "<!-- /mdwiki:cross-refs -->\n"
    )
    ref = CrossRef("wiki/concepts/source.md", f"wiki/concepts/{target_name}", "Target v2")

    first = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]
    source.write_text(first)
    second = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]

    assert second == first
    assert first.count(encoded_target) == 0
    assert first.count(destination) == 1
    assert "- [Other](other.md)" in first


def test_link_inside_fenced_example_does_not_suppress_real_edge(wiki: Path) -> None:
    source = wiki / "wiki/concepts/source.md"
    source.write_text("# Source\n\n```md\n[example](target.md)\n```\n")

    body = materialize_cross_refs(
        wiki_root=wiki,
        plan=_plan(CrossRef("wiki/concepts/source.md", "wiki/concepts/target.md", "Target")),
    )["wiki/concepts/source.md"]

    assert body.count("(target.md)") == 2
    assert "- [Target](target.md)" in body


def test_complete_managed_block_inside_fence_is_preserved_idempotently(wiki: Path) -> None:
    source = wiki / "wiki/concepts/source.md"
    fenced_block = "```md\n<!-- mdwiki:cross-refs -->\n## Related\n\n- [Example](other.md)\n<!-- /mdwiki:cross-refs -->\n```\n"
    source.write_text(f"# Source\n\n{fenced_block}")
    ref = CrossRef("wiki/concepts/source.md", "wiki/concepts/target.md", "Target")

    first = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]
    source.write_text(first)
    second = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]

    assert first == second
    assert fenced_block in first
    assert first.count("<!-- mdwiki:cross-refs -->") == 2
    assert first.count("- [Example](other.md)") == 1
    assert first.count("- [Target](target.md)") == 1


def test_trailing_backslash_anchor_is_escaped_as_safe_markdown(wiki: Path) -> None:
    ref = CrossRef("wiki/concepts/source.md", "wiki/concepts/target.md", "Target\\")

    body = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]

    assert "- [Target\\\\](target.md)" in body


@pytest.mark.parametrize(
    "malformed",
    ["Example: <!-- /mdwiki:cross-refs -->", "<!-- mdwiki:cross-refs -->\nExample without managed heading"],
)
def test_malformed_marker_text_is_not_used_as_insertion_point(wiki: Path, malformed: str) -> None:
    source = wiki / "wiki/concepts/source.md"
    source.write_text(f"# Source\n\n{malformed}\n")

    body = materialize_cross_refs(
        wiki_root=wiki,
        plan=_plan(CrossRef("wiki/concepts/source.md", "wiki/concepts/target.md", "Target")),
    )["wiki/concepts/source.md"]

    assert malformed in body
    assert body.rstrip().endswith("<!-- /mdwiki:cross-refs -->")
    assert "- [Target](target.md)" in body

    source.write_text(body)
    repeated = materialize_cross_refs(
        wiki_root=wiki,
        plan=_plan(CrossRef("wiki/concepts/source.md", "wiki/concepts/target.md", "Target")),
    )["wiki/concepts/source.md"]
    assert repeated == body
    assert malformed in repeated


@pytest.mark.parametrize(
    "target_name",
    ["target with spaces.md", "target (v2).md", "target#section.md", "target%done.md", "café.md"],
)
def test_special_character_target_round_trips_idempotently(wiki: Path, target_name: str) -> None:
    target_path = wiki / "wiki" / "concepts" / target_name
    target_path.write_text("# Target\n")
    ref = CrossRef("wiki/concepts/source.md", f"wiki/concepts/{target_name}", "Special target")

    first = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]
    (wiki / "wiki/concepts/source.md").write_text(first)
    second = materialize_cross_refs(wiki_root=wiki, plan=_plan(ref))["wiki/concepts/source.md"]

    assert second == first
    assert first.count("<!-- mdwiki:cross-refs -->") == 1


def test_multiple_owned_blocks_collapse_to_one_canonical_union(wiki: Path) -> None:
    source = wiki / "wiki/concepts/source.md"
    source.write_text(
        "# Source\n\n"
        "<!-- mdwiki:cross-refs -->\n## Related\n\n- [Target](target.md)\n<!-- /mdwiki:cross-refs -->\n\n"
        "Between.\n\n"
        "<!-- mdwiki:cross-refs -->\n## Related\n\n- [Other](other.md)\n- [Duplicate](target.md)\n<!-- /mdwiki:cross-refs -->\n"
    )
    refs = (CrossRef("wiki/concepts/source.md", "wiki/concepts/target.md", "Ignored duplicate"),)

    first = materialize_cross_refs(wiki_root=wiki, plan=_plan(*refs))["wiki/concepts/source.md"]
    source.write_text(first)
    second = materialize_cross_refs(wiki_root=wiki, plan=_plan(*refs))["wiki/concepts/source.md"]

    assert second == first
    assert first.count("<!-- mdwiki:cross-refs -->") == 1
    assert first.count("(target.md)") == 1
    assert "- [Other](other.md)" in first
    assert "Between." in first


@pytest.mark.parametrize(
    "fenced",
    [
        "````md\n[example](target.md)\n```\n````\n",
        "```md\n[example](target.md)\n~~~\n```\n",
        "~~~md\n[example](target.md)\n```\n~~~\n",
        "```md\n[example](target.md)\n",
    ],
)
def test_fence_stripping_requires_matching_delimiter_and_length(wiki: Path, fenced: str) -> None:
    source = wiki / "wiki/concepts/source.md"
    source.write_text(f"# Source\n\n{fenced}")

    body = materialize_cross_refs(
        wiki_root=wiki,
        plan=_plan(CrossRef("wiki/concepts/source.md", "wiki/concepts/target.md", "Target")),
    )["wiki/concepts/source.md"]

    assert "- [Target](target.md)" in body
