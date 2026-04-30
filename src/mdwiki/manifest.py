"""
Generate and parse YAML manifests for human review.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import yaml


class ManifestGenerator:
    """Generate YAML manifests from hierarchy."""

    def __init__(self, source_dir: str, threshold: float):
        self.source_dir = source_dir
        self.threshold = threshold

    def generate(
        self,
        hierarchy: dict[str, Any],
        total_sections: int,
        duplicates_removed: int,
    ) -> str:
        """
        Generate YAML manifest string.

        Parameters
        ----------
        hierarchy : dict
            Output from TreeBuilder.build_hierarchy().
        total_sections : int
            Total sections before deduplication.
        duplicates_removed : int
            Number of duplicates found.

        Returns
        -------
        str
            YAML manifest string.
        """
        manifest = {
            'generated': datetime.now().isoformat(),
            'source': self.source_dir,
            'threshold': self.threshold,
            'total_sections': total_sections,
            'duplicates_removed': duplicates_removed,
            'hierarchy': hierarchy['themes'],
            'orphans': hierarchy['orphans'],
        }

        return yaml.dump(manifest, default_flow_style=False, sort_keys=False, allow_unicode=True)


class ManifestParser:
    """Parse edited YAML manifests."""

    def parse(self, yaml_content: str) -> dict[str, Any]:
        """
        Parse YAML manifest string.

        Parameters
        ----------
        yaml_content : str
            YAML manifest content.

        Returns
        -------
        dict
            Parsed manifest structure.
        """
        return yaml.safe_load(yaml_content)

    def parse_file(self, filepath: str) -> dict[str, Any]:
        """Parse manifest from file path."""
        with open(filepath, encoding='utf-8') as f:
            return self.parse(f.read())
