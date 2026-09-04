"""Rule CTX-002: Tool Schema Bloat / Overweight Detection."""

from __future__ import annotations

import json
from typing import List, Optional, Set

from src.core.analyzer.cost.pricing_table import get_pricing
from src.core.analyzer.heuristics.base import BaseHeuristic
from src.core.graph.turn_tree import ContextGraph
from src.schema.ast import CanonicalTurn, RuleViolation, ViolationSeverity


class SchemaBloatHeuristic(BaseHeuristic):
    """Detects when tool definitions consume excessive context with low usage."""

    rule_id = "CTX-002"
    name = "Tool Schema Overweight"
    description = (
        "Flags tool schemas consuming > 35% of input tokens when < 15% of registered "
        "tools have been invoked in the session."
    )

    def __init__(
        self,
        max_schema_ratio: float = 0.35,
        min_tool_count: int = 5,
        max_invocation_ratio: float = 0.15,
    ) -> None:
        self.max_schema_ratio = max_schema_ratio
        self.min_tool_count = min_tool_count
        self.max_invocation_ratio = max_invocation_ratio

    def analyze(
        self,
        turn: CanonicalTurn,
        graph: Optional[ContextGraph] = None,
        previous_turns: Optional[List[CanonicalTurn]] = None,
    ) -> List[RuleViolation]:
        violations: List[RuleViolation] = []
        if not turn.tool_defs:
            return violations

        total_tool_tokens = sum(td.token_count for td in turn.tool_defs)
        effective_input_tokens = turn.input_tokens or sum(b.token_count for b in turn.all_blocks)

        if effective_input_tokens <= 0:
            return violations

        schema_ratio = total_tool_tokens / effective_input_tokens
        if schema_ratio <= self.max_schema_ratio:
            return violations

        total_tools = len(turn.tool_defs)
        if total_tools <= self.min_tool_count:
            return violations

        # Collect tool names registered
        registered_tools: Set[str] = set()
        for td in turn.tool_defs:
            name = td.metadata.get("name")
            if not name:
                name = td.block_id.replace("tool_def_", "")
            registered_tools.add(name)

        # Track invoked tools across all turns in session
        resolved_prev = self.resolve_previous_turns(turn, graph, previous_turns)
        all_turns = resolved_prev + [turn]
        called_tools: Set[str] = set()

        for t in all_turns:
            for res in t.tool_results:
                tool_name = res.metadata.get("name") or res.metadata.get("tool_name")
                if tool_name and tool_name in registered_tools:
                    called_tools.add(tool_name)
                # Check JSON payload if metadata doesn't contain name
                if not tool_name and res.content:
                    try:
                        parsed = json.loads(res.content)
                        if isinstance(parsed, dict):
                            p_name = parsed.get("name") or parsed.get("tool_name")
                            if p_name and p_name in registered_tools:
                                called_tools.add(p_name)
                    except (json.JSONDecodeError, TypeError):
                        pass

            for blk in t.assistant_blocks:
                if not blk.content:
                    continue
                try:
                    parsed = json.loads(blk.content)
                    if isinstance(parsed, dict) and parsed.get("type") == "tool_use":
                        used_name = parsed.get("name")
                        if used_name and used_name in registered_tools:
                            called_tools.add(used_name)
                except (json.JSONDecodeError, TypeError):
                    pass

        invocation_ratio = len(called_tools) / total_tools
        if invocation_ratio < self.max_invocation_ratio:
            pricing = get_pricing(turn.model, turn.provider)
            waste_cost = round((total_tool_tokens / 1000.0) * pricing.input_cost_per_1k, 6)
            pct_input = round(schema_ratio * 100)
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    severity=ViolationSeverity.WARN,
                    title=self.name,
                    message=(
                        f"Tool schemas occupy {total_tool_tokens} tokens ({pct_input}% "
                        f"of input), but only {len(called_tools)}/{total_tools} tools "
                        f"have been used."
                    ),
                    estimated_waste_usd=waste_cost,
                    suggested_fix="Group tools into subagents or filter tool schemas dynamically.",
                    block_ids=[td.block_id for td in turn.tool_defs],
                )
            )

        return violations
