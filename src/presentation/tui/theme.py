"""Theme definitions, color palettes, and visual helpers for the ctxins TUI."""

from __future__ import annotations

from typing import Dict

# Terminal background and structural colors
COLOR_BG: str = "#0d1117"
COLOR_PANEL_BG: str = "#161b22"
COLOR_BORDER: str = "#30363d"
COLOR_BORDER_FOCUS: str = "#58a6ff"
COLOR_FG: str = "#c9d1d9"
COLOR_MUTED: str = "#8b949e"
COLOR_ACCENT: str = "#58a6ff"

# Severity badge colors
COLOR_CRITICAL: str = "#f85149"  # Red
COLOR_WARN: str = "#d29922"  # Amber / Yellow
COLOR_INFO: str = "#58a6ff"  # Blue
COLOR_SUCCESS: str = "#3fb950"  # Green

# Context breakdown category colors
CATEGORY_COLORS: Dict[str, str] = {
    "system": "#388bfd",  # Blue
    "tools": "#a371f7",  # Purple
    "tool_def": "#a371f7",  # Purple (AST block type)
    "history": "#2ea043",  # Green
    "user_msg": "#2ea043",  # Green (AST block type)
    "toolResults": "#f0883e",  # Orange
    "tool_result": "#f0883e",  # Orange (AST block type)
    "assistant": "#58a6ff",  # Light Blue
    "assistant_msg": "#58a6ff",  # Light Blue (AST block type)
    "cache": "#7ee787",  # Bright Green
    "skills": "#2ea043",  # Emerald / Green
    "skill": "#2ea043",  # Emerald / Green (AST block type)
    "thought": "#d2a8ff",  # Magenta / Purple
    "thoughts": "#d2a8ff",  # Magenta / Purple
    "injected_state": "#39c5cf",  # Cyan
    "injected_context": "#39c5cf",  # Cyan (AST block type)
}

# Diff ledger status colors and styles
COLOR_DIFF_ADDED: str = "#3fb950"  # Green
COLOR_DIFF_PERSISTED: str = "#58a6ff"  # Blue / Dim
COLOR_DIFF_MUTATED: str = "#d29922"  # Yellow / Orange
COLOR_DIFF_EVICTED: str = "#f85149"  # Red

DIFF_COLORS: Dict[str, str] = {
    "added": COLOR_DIFF_ADDED,
    "persisted": COLOR_DIFF_PERSISTED,
    "mutated": COLOR_DIFF_MUTATED,
    "evicted": COLOR_DIFF_EVICTED,
    "diff_added": COLOR_DIFF_ADDED,
    "diff_persisted": COLOR_DIFF_PERSISTED,
    "diff_mutated": COLOR_DIFF_MUTATED,
    "diff_evicted": COLOR_DIFF_EVICTED,
    "removed": COLOR_DIFF_EVICTED,
}

DIFF_STYLES: Dict[str, str] = {
    "added": f"bold {COLOR_DIFF_ADDED}",
    "persisted": f"dim {COLOR_DIFF_PERSISTED}",
    "mutated": f"bold {COLOR_DIFF_MUTATED}",
    "evicted": f"bold strike {COLOR_DIFF_EVICTED}",
    "diff_added": f"bold {COLOR_DIFF_ADDED}",
    "diff_persisted": f"dim {COLOR_DIFF_PERSISTED}",
    "diff_mutated": f"bold {COLOR_DIFF_MUTATED}",
    "diff_evicted": f"bold strike {COLOR_DIFF_EVICTED}",
}


def get_pollution_color(score: float) -> str:
    """Return theme color for context pollution score (0-100)."""
    if score < 20.0:
        return COLOR_SUCCESS
    elif score <= 50.0:
        return COLOR_WARN
    else:
        return COLOR_CRITICAL


def get_pollution_label(score: float) -> str:
    """Return descriptive status text for pollution score."""
    if score < 20.0:
        return "Clean"
    elif score <= 50.0:
        return "Moderate"
    else:
        return "High Pollution"


def render_pollution_bar(score: float, width: int = 15) -> str:
    """Render horizontal visual ASCII meter for pollution score."""
    clamped_score = max(0.0, min(100.0, score))
    filled_chars = int(round((clamped_score / 100.0) * width))
    empty_chars = width - filled_chars
    bar = "■" * filled_chars + "□" * empty_chars
    label = get_pollution_label(clamped_score)
    return f"[{bar}] {clamped_score:.1f}/100 ({label})"


# Core application layout CSS
TUI_THEME_CSS: str = """
Screen {
    layout: vertical;
    background: #0d1117;
    color: #c9d1d9;
}

#main-container {
    height: 1fr;
    layout: horizontal;
}

#sessions-pane {
    width: 24%;
    min-width: 22;
    border-right: solid #30363d;
    height: 100%;
}

#timeline-pane {
    width: 22%;
    min-width: 20;
    border-right: solid #30363d;
    height: 100%;
}

#breakdown-pane {
    width: 32%;
    min-width: 28;
    border-right: solid #30363d;
    height: 100%;
}

#recommendations-pane {
    width: 22%;
    min-width: 20;
    height: 100%;
}

.pane-title {
    background: #161b22;
    color: #58a6ff;
    text-style: bold;
    padding: 0 1;
    border-bottom: solid #30363d;
    height: 1;
}
"""
