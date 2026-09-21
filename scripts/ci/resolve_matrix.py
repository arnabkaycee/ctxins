#!/usr/bin/env python3
"""Resolve agent CLI package versions dynamically for GitHub Actions test matrix.

Queries npm (and local stubs) to compute [latest, N-1, N-2, N-3] release versions
for:
- pi (@earendil-works/pi-coding-agent)
- opencode v2 (opencode-ai >= 1.0.0)
- opencode v1 (opencode-ai < 1.0.0)
- claude (@anthropic-ai/claude-code)
- agy (antigravity-cli / local binary)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List

TOOL_CONFIGS: Dict[str, Dict[str, Any]] = {
    "claude": {
        "package": "@anthropic-ai/claude-code",
        "registry": "npm",
        "binary": "claude",
        "command_template": "claude -p 'ping'",
        "version_filter": lambda v: not re.search(r"[a-zA-Z-]", v),
    },
    "opencode-v2": {
        "package": "opencode-ai",
        "registry": "npm",
        "binary": "opencode",
        "command_template": "opencode --version",
        "version_filter": lambda v: v.startswith("1.") and not re.search(r"[a-zA-Z-]", v),
    },
    "opencode-v1": {
        "package": "opencode-ai",
        "registry": "npm",
        "binary": "opencode",
        "command_template": "opencode --version",
        "version_filter": lambda v: v.startswith("0.") and not re.search(r"[a-zA-Z-]", v),
    },
    "pi": {
        "package": "@earendil-works/pi-coding-agent",
        "registry": "npm",
        "binary": "pi",
        "command_template": "pi --version",
        "version_filter": lambda v: not re.search(r"[a-zA-Z-]", v),
    },
    "agy": {
        "package": "agy",
        "registry": "system",
        "binary": "agy",
        "command_template": "agy --version",
        "version_filter": lambda v: True,
    },
}


def _parse_semver(v: str) -> tuple[int, ...]:
    """Extract numeric components of semver string for sorting."""
    nums = re.findall(r"\d+", v)
    return tuple(int(n) for n in nums)


def fetch_npm_versions(package_name: str) -> List[str]:
    """Fetch published versions for an npm package."""
    try:
        res = subprocess.run(
            ["npm", "view", package_name, "versions", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        if isinstance(data, str):
            return [data]
        elif isinstance(data, list):
            return data
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to fetch npm versions for {package_name}: {e}\n")
    return []


def resolve_tool_versions(tool: str, depth: int = 4) -> List[Dict[str, Any]]:
    """Resolve target versions for a specific tool."""
    cfg = TOOL_CONFIGS.get(tool)
    if not cfg:
        return []

    registry = cfg["registry"]
    package = cfg["package"]
    v_filter = cfg["version_filter"]

    if registry == "npm":
        all_versions = fetch_npm_versions(package)
        valid = [v for v in all_versions if v_filter(v)]
        valid.sort(key=_parse_semver)
        if not valid:
            selected = ["latest"]
        else:
            selected = valid[-depth:][::-1]
    else:
        selected = ["latest"]

    entries: List[Dict[str, Any]] = []
    labels = ["latest", "N-1", "N-2", "N-3"]
    for idx, ver in enumerate(selected):
        label = labels[idx] if idx < len(labels) else f"N-{idx}"
        entries.append(
            {
                "tool": tool,
                "package": package,
                "version": ver,
                "tier": label,
                "binary": cfg["binary"],
                "command_template": cfg["command_template"],
            }
        )

    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve matrix of CLI tools and versions.")
    parser.add_argument(
        "--tools",
        default="claude,opencode-v2,opencode-v1,pi,agy",
        help="Comma-separated tools to resolve (default: claude,opencode-v2,opencode-v1,pi,agy)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=4,
        help="Number of versions to resolve per tool (default: 4 for latest and N-3)",
    )
    parser.add_argument(
        "--nightly-only",
        action="store_true",
        help="Only resolve latest and N-1 versions (depth=2)",
    )
    parser.add_argument(
        "--output-file",
        help="Path to write JSON matrix output (optional)",
    )
    parser.add_argument(
        "--set-github-output",
        action="store_true",
        help="Set output variable in $GITHUB_OUTPUT if running in GitHub Actions",
    )

    args = parser.parse_args()
    depth = 2 if args.nightly_only else args.depth
    tools = [t.strip() for t in args.tools.split(",") if t.strip()]

    matrix_include: List[Dict[str, Any]] = []
    for tool in tools:
        resolved = resolve_tool_versions(tool, depth=depth)
        matrix_include.extend(resolved)

    matrix_data = {"include": matrix_include}
    output_json = json.dumps(matrix_data, indent=2)

    print(output_json)

    if args.output_file:
        with open(args.output_file, "w") as f:
            f.write(output_json)

    if args.set_github_output and "GITHUB_OUTPUT" in os.environ:
        gh_output = os.environ["GITHUB_OUTPUT"]
        with open(gh_output, "a") as f:
            f.write(f"matrix={json.dumps(matrix_data)}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
