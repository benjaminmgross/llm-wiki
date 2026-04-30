"""Tests for the manifest module."""

import yaml


def test_generate_manifest_creates_valid_yaml():
    """
    Given: A hierarchy structure
    When: Generating manifest
    Then: Output is valid YAML with expected structure
    """
    from mdwiki.manifest import ManifestGenerator

    hierarchy = {
        'themes': [{
            'theme': 'Authentication',
            'confidence': 0.85,
            'documents': [{
                'name': 'oauth.md',
                'sections': [{
                    'id': 'test.md/OAuth Setup',
                    'heading': 'OAuth Setup',
                    'summary': 'How to configure OAuth',
                    'keywords': ['oauth', 'token'],
                    'source': 'test.md',
                    'modified': '2025-12-08',
                }]
            }]
        }],
        'orphans': []
    }

    generator = ManifestGenerator(source_dir='./docs', threshold=0.5)
    manifest_str = generator.generate(hierarchy, total_sections=1, duplicates_removed=0)

    # Should be valid YAML
    parsed = yaml.safe_load(manifest_str)
    assert 'hierarchy' in parsed
    assert parsed['hierarchy'][0]['theme'] == 'Authentication'


def test_parse_manifest_loads_edited_yaml():
    """
    Given: An edited manifest YAML
    When: Parsing it
    Then: Returns structured data for synthesis
    """
    from mdwiki.manifest import ManifestParser

    yaml_content = """
hierarchy:
  - theme: Auth
    documents:
      - name: custom-name.md
        sections:
          - id: test.md/OAuth
            heading: OAuth
orphans: []
"""

    parser = ManifestParser()
    result = parser.parse(yaml_content)

    assert result['hierarchy'][0]['documents'][0]['name'] == 'custom-name.md'


def test_generate_manifest_includes_metadata():
    """
    Given: A hierarchy structure
    When: Generating manifest
    Then: Includes source, threshold, timestamps
    """
    from mdwiki.manifest import ManifestGenerator

    hierarchy = {'themes': [], 'orphans': []}

    generator = ManifestGenerator(source_dir='/path/to/docs', threshold=0.7)
    manifest_str = generator.generate(hierarchy, total_sections=5, duplicates_removed=2)

    parsed = yaml.safe_load(manifest_str)
    assert parsed['source'] == '/path/to/docs'
    assert parsed['threshold'] == 0.7
    assert parsed['total_sections'] == 5
    assert parsed['duplicates_removed'] == 2
    assert 'generated' in parsed


def test_parse_manifest_from_file(tmp_path):
    """
    Given: A manifest YAML file
    When: Parsing from file path
    Then: Returns parsed content
    """
    from mdwiki.manifest import ManifestParser

    manifest_file = tmp_path / "manifest.yaml"
    manifest_file.write_text("""
hierarchy:
  - theme: Testing
    documents: []
orphans: []
""")

    parser = ManifestParser()
    result = parser.parse_file(str(manifest_file))

    assert result['hierarchy'][0]['theme'] == 'Testing'
