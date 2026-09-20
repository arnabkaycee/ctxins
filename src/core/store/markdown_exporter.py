"""Actionable Markdown audit and remediation report generator for sessions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.core.analyzer.scorer import PollutionScorer
from src.core.store.session_store import SessionStore
from src.schema.ast import CanonicalTurn


class MarkdownExporter:
    """Exports session audits and optimization plans into actionable Markdown."""

    @classmethod
    def export_from_store(cls, store: SessionStore, session_id: str) -> str:
        """Construct actionable Markdown report for a session in store."""
        turns = store.get_session(session_id) or []
        metadata = store.get_session_metadata(session_id) or {}
        violations = store.get_violations(session_id)
        return cls.generate_report(session_id, turns, violations, metadata)

    @classmethod
    def generate_report(
        cls,
        session_id: str,
        turns: List[CanonicalTurn],
        violations: list,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Format turns, violations, and summary metrics into canonical markdown audit."""
        meta = metadata or {}
        summary = PollutionScorer.calculate_summary(turns)
        first_turn = turns[0] if turns else None
        harness = (
            meta.get("agentHarness")
            or meta.get("harness")
            or (
                first_turn.metadata.get("harness")
                if first_turn
                and hasattr(first_turn, "metadata")
                and isinstance(first_turn.metadata, dict)
                else "Unknown Agent"
            )
        )
        model = first_turn.model if first_turn else meta.get("model", "auto-detect")
        provider = first_turn.provider if first_turn else meta.get("provider", "unknown")

        total_input = summary.get("totalInputTokens", 0)
        total_output = summary.get("totalOutputTokens", 0)
        total_tokens = total_input + total_output
        cache_hit_pct = summary.get("cacheHitRatio", 0.0) * 100.0
        spend = summary.get("estimatedCostUSD", 0.0)
        savings = summary.get("potentialSavingsUSD", 0.0)
        pollution = summary.get("pollutionScore", 0.0)

        pollution_status = "Pristine Clean"
        if pollution >= 50:
            pollution_status = "Critical Pollution"
        elif pollution >= 20:
            pollution_status = "Moderate Bloat"

        lines = [
            f"# 🔍 ctxins Context Optimization Report: `{session_id}`",
            "",
            f"- **Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"- **Agent Harness:** {harness}",
            f"- **Model & Provider:** {model} ({provider})",
            f"- **Total Turns:** {len(turns)}",
            "",
            "---",
            "",
            "## 📊 Executive Summary & Financial Audit",
            "",
            "| Metric | Value | Assessment |",
            "| :--- | :--- | :--- |",
            f"| **Total Tokens** | {total_tokens:,} ({total_input:,} in / {total_output:,} out) | Combined cumulative context |",
            f"| **Prompt Cache Hit %** | {cache_hit_pct:.1f}% | Ratio of cached read to input tokens |",
            f"| **Estimated Total Spend** | ${spend:.4f} USD | Total model API cost |",
            f"| **Avoidable Waste** | **${savings:.4f} USD** | Recoverable financial waste |",
            f"| **Context Pollution Score** | **{pollution:.1f} / 100** | {pollution_status} |",
            "",
            "---",
            "",
            "## 🚨 Triggered Context Health Violations",
            "",
        ]

        if not violations:
            lines.extend(
                [
                    "> ✨ **Zero Context Violations Detected**",
                    "> Context cache boundaries, prompt sizing, and tool lifecycles are operating efficiently.",
                    "",
                ]
            )
        else:
            for i, v in enumerate(violations, 1):
                rule_id = getattr(v, "rule_id", None) or getattr(v, "ruleId", "RULE")
                sev = getattr(v, "severity", "WARN")
                sev_str = sev.value if hasattr(sev, "value") else str(sev)
                title = getattr(v, "title", rule_id)
                msg = getattr(v, "message", "")
                fix = getattr(v, "suggested_fix", "") or getattr(v, "suggestedFix", "")
                waste = getattr(v, "estimated_waste_usd", 0.0) or getattr(v, "estimatedWasteUSD", 0.0)
                waste_str = f" (${waste:.4f} waste)" if waste else ""

                lines.extend(
                    [
                        f"### {i}. [{sev_str}] {title}{waste_str}",
                        f"- **Rule ID:** `{rule_id}`",
                        f"- **Diagnostic:** {msg}",
                        f"- **Remediation:** {fix}",
                        "",
                    ]
                )

        lines.extend(
            [
                "---",
                "",
                "## 🛠️ Recommended Directives for AGENTS.md / .cursorrules",
                "",
                "Copy and append the following directives to your project's `AGENTS.md` or `.cursorrules` to instruct agent harnesses to avoid detected context drag:",
                "",
                "```markdown",
                "# Context Optimization Directives (ctxins)",
            ]
        )

        seen_directives = set()
        for v in violations:
            raw_id = getattr(v, "rule_id", None) or getattr(v, "ruleId", "") or ""
            rid = str(raw_id).upper()
            if "CTX001" in rid and "CTX001" not in seen_directives:
                seen_directives.add("CTX001")
                lines.append(
                    "- [Stale Tool Output]: Summarize execution results older than 3 turns into key outcomes; omit raw stdout/stderr dumps."
                )
            elif "CTX002" in rid and "CTX002" not in seen_directives:
                seen_directives.add("CTX002")
                lines.append(
                    "- [Tool Schema Bloat]: Defer tool declarations until needed; prune uninvoked tool definitions from the system prompt."
                )
            elif "CACHE001" in rid and "CACHE001" not in seen_directives:
                seen_directives.add("CACHE001")
                lines.append(
                    "- [Cache Invalidation]: Ensure system instructions, skills, and tools are strictly deterministic and static at the context prefix."
                )

        if not seen_directives:
            lines.append(
                "- Maintain static system prompt prefixes and compact multi-turn tool outputs to maximize prompt cache hits."
            )

        lines.extend(
            [
                "```",
                "",
                "---",
                "*Report generated by [ctxins](https://github.com/arnabkaycee/ctxins)*",
                "",
            ]
        )

        return "\n".join(lines)
