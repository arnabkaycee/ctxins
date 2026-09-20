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
            current_turn_idx = turns[-1].turn_index if turns else 0
            groups_map: Dict[Any, List[Any]] = {}
            for v in violations:
                rid = getattr(v, "rule_id", None) or getattr(v, "ruleId", "")
                fix = getattr(v, "suggested_fix", "") or getattr(v, "suggestedFix", "")
                title = getattr(v, "title", rid or "RULE")
                key = rid if rid else (fix or title)
                groups_map.setdefault(key, []).append(v)

            p_order = {"CRITICAL": 0, "WARN": 1, "INFO": 2}
            sorted_groups: List[Dict[str, Any]] = []
            for viols in groups_map.values():
                sorted_viols = sorted(
                    viols,
                    key=lambda x: getattr(x, "turn_index", getattr(x, "turnIndex", 0)) or 0,
                )
                first = sorted_viols[0]
                rid = getattr(first, "rule_id", None) or getattr(first, "ruleId", "RULE")
                title = getattr(first, "title", rid)
                severities: List[str] = []
                for x in sorted_viols:
                    sev_attr = getattr(x, "severity", "WARN")
                    s_val = sev_attr.value if hasattr(sev_attr, "value") else str(sev_attr)
                    severities.append(str(s_val).upper())

                if "CRITICAL" in severities:
                    sev_str = "CRITICAL"
                elif "WARN" in severities:
                    sev_str = "WARN"
                else:
                    sev_str = "INFO"

                earlier_viols = [
                    x
                    for x in sorted_viols
                    if (getattr(x, "turn_index", getattr(x, "turnIndex", None)) is not None)
                    and getattr(x, "turn_index", getattr(x, "turnIndex", 0)) < current_turn_idx
                ]
                current_viols = [
                    x
                    for x in sorted_viols
                    if getattr(x, "turn_index", getattr(x, "turnIndex", None)) == current_turn_idx
                ]
                cur_viol = current_viols[0] if current_viols else None
                fix = (
                    getattr(cur_viol, "suggested_fix", "") or getattr(cur_viol, "suggestedFix", "")
                    if cur_viol
                    else ""
                ) or next(
                    (
                        getattr(x, "suggested_fix", "") or getattr(x, "suggestedFix", "")
                        for x in reversed(sorted_viols)
                        if getattr(x, "suggested_fix", "") or getattr(x, "suggestedFix", "")
                    ),
                    "",
                )
                total_waste = sum(
                    float(
                        getattr(x, "estimated_waste_usd", 0.0)
                        or getattr(x, "estimatedWasteUSD", 0.0)
                        or 0.0
                    )
                    for x in sorted_viols
                )
                earlier_waste = sum(
                    float(
                        getattr(x, "estimated_waste_usd", 0.0)
                        or getattr(x, "estimatedWasteUSD", 0.0)
                        or 0.0
                    )
                    for x in earlier_viols
                )
                turn_indices = sorted(
                    list(
                        {
                            getattr(x, "turn_index", getattr(x, "turnIndex", 0))
                            for x in sorted_viols
                            if getattr(x, "turn_index", getattr(x, "turnIndex", None)) is not None
                        }
                    )
                )
                earlier_turn_indices = sorted(
                    list(
                        {
                            getattr(x, "turn_index", getattr(x, "turnIndex", 0))
                            for x in earlier_viols
                            if getattr(x, "turn_index", getattr(x, "turnIndex", None)) is not None
                        }
                    )
                )

                sorted_groups.append(
                    {
                        "rule_id": rid,
                        "title": title,
                        "fix": fix,
                        "sev_str": sev_str,
                        "count": len(sorted_viols),
                        "turn_indices": turn_indices,
                        "earlier_turn_indices": earlier_turn_indices,
                        "cur_viol": cur_viol,
                        "earlier_viols": earlier_viols,
                        "total_waste": total_waste,
                        "earlier_waste": earlier_waste,
                    }
                )

            sorted_groups.sort(
                key=lambda g: (
                    p_order.get(str(g["sev_str"]), 3),
                    -float(g["total_waste"]),
                )
            )

            for i, g in enumerate(sorted_groups, 1):
                rule_id = str(g["rule_id"])
                sev_str = str(g["sev_str"])
                title = str(g["title"])
                fix = str(g["fix"])
                count = int(g["count"])
                turn_indices = list(g["turn_indices"])
                cur_viol = g["cur_viol"]
                earlier_viols = list(g["earlier_viols"])
                total_waste = float(g["total_waste"])
                waste_str = f" (${total_waste:.4f} waste)" if total_waste else ""

                if count > 1:
                    turns_desc = (
                        f" ({count} occurrences across {len(turn_indices)} turns{waste_str})"
                    )
                    lines.append(f"### {i}. [{sev_str}] {title}{turns_desc}")
                else:
                    lines.append(f"### {i}. [{sev_str}] {title}{waste_str}")

                lines.append(f"- **Rule ID:** `{rule_id}`")
                lines.append(
                    f"- **Total Violations:** {count} across turns {', '.join(f'#{t}' for t in turn_indices) if turn_indices else f'#{current_turn_idx}'}"
                )

                if cur_viol:
                    cur_waste = getattr(cur_viol, "estimated_waste_usd", 0.0) or getattr(
                        cur_viol, "estimatedWasteUSD", 0.0
                    )
                    cur_msg = getattr(cur_viol, "message", "")
                    c_waste_str = f" (${cur_waste:.4f} waste)" if cur_waste else ""
                    lines.append(
                        f"- **Current Turn (#{current_turn_idx}):** Active{c_waste_str} — {cur_msg}"
                    )
                else:
                    lines.append(f"- **Current Turn (#{current_turn_idx}):** Clean / Not triggered")

                if earlier_viols:
                    e_turns = list(g["earlier_turn_indices"])
                    e_turns_str = ", ".join(f"#{t}" for t in e_turns)
                    e_waste = float(g["earlier_waste"])
                    e_waste_str = f" (${e_waste:.4f} waste)" if e_waste else ""
                    lines.append(
                        f"- **Earlier Turns ({e_turns_str}):** {len(earlier_viols)} violation(s){e_waste_str}"
                    )
                else:
                    lines.append("- **Earlier Turns:** None (first occurrence)")

                lines.append(f"- **Remediation:** {fix}")
                lines.append("")

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
