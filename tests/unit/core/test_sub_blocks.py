"""Unit tests for SubBlockDecomposer and normalizer integration."""

from src.core.ast.normalizers import (
    AnthropicASTNormalizer,
    GeminiASTNormalizer,
    OpenAIASTNormalizer,
)
from src.core.ast.sub_blocks import SubBlockDecomposer
from src.core.graph.hasher import compute_block_hash
from src.schema.ast import BlockType

# ---------------------------------------------------------------------------
# SubBlockDecomposer Unit Tests
# ---------------------------------------------------------------------------


def test_fallback_plain_text():
    decomposer = SubBlockDecomposer()
    text = "You are an expert Python engineer."
    blocks = decomposer.decompose(text, default_block_type=BlockType.SYSTEM, base_id="sys")

    assert len(blocks) == 1
    blk = blocks[0]
    assert blk.block_id == "sys_0"
    assert blk.block_type == BlockType.SYSTEM
    assert blk.content == text
    assert blk.content_hash == compute_block_hash(text)
    assert blk.token_count == max(1, len(text) // 4)
    assert blk.identity_key == ""


def test_empty_or_whitespace_text():
    decomposer = SubBlockDecomposer()
    assert decomposer.decompose("") == []
    assert decomposer.decompose("   \n\t  ") == []


def test_custom_token_counter():
    def counter(s: str) -> int:
        return len(s.split())

    decomposer = SubBlockDecomposer(token_counter=counter)
    blocks = decomposer.decompose("one two three")
    assert blocks[0].token_count == 3


def test_xml_tags_basic():
    decomposer = SubBlockDecomposer()
    text = """
<skills>
- git commit
- bash command
</skills>

<user_rules>
Always write tests before completing work.
</user_rules>

<tools>
tool: search
tool: calculator
</tools>

<thought>
Let me plan the next steps.
</thought>

<context>
Workspace: /Users/arnab/project
</context>

<environment>
OS: macOS
Python: 3.12
</environment>
"""
    blocks = decomposer.decompose(text, default_block_type=BlockType.SYSTEM, base_id="test")

    assert len(blocks) == 6

    # 1. Skills
    assert blocks[0].block_type == BlockType.SKILL
    assert blocks[0].identity_key == "skills"
    assert "- git commit" in blocks[0].content
    assert blocks[0].block_id == "test_0"

    # 2. User Rules
    assert blocks[1].block_type == BlockType.SYSTEM
    assert blocks[1].identity_key == "rules:user_rules"
    assert "Always write tests" in blocks[1].content
    assert blocks[1].block_id == "test_1"

    # 3. Tools
    assert blocks[2].block_type == BlockType.TOOL_DEF
    assert blocks[2].identity_key == "tools"
    assert "tool: search" in blocks[2].content
    assert blocks[2].block_id == "test_2"

    # 4. Thought
    assert blocks[3].block_type == BlockType.THOUGHT
    assert blocks[3].identity_key == "thought:3"
    assert "Let me plan" in blocks[3].content
    assert blocks[3].block_id == "test_3"

    # 5. Context
    assert blocks[4].block_type == BlockType.INJECTED_STATE
    assert blocks[4].identity_key == "context"
    assert "Workspace: /Users/arnab/project" in blocks[4].content
    assert blocks[4].block_id == "test_4"

    # 6. Environment
    assert blocks[5].block_type == BlockType.INJECTED_STATE
    assert blocks[5].identity_key == "environment"
    assert "OS: macOS" in blocks[5].content
    assert blocks[5].block_id == "test_5"


def test_xml_tags_with_attributes_and_brackets():
    decomposer = SubBlockDecomposer()
    text = """
<skill name="web_search">
Execute web searches via SerpAPI.
</skill>

<user_rules name="code_style">
Follow PEP 8 formatting.
</user_rules>

<RULE[/Users/arnab/code/ctxins/AGENTS.md]>
Non-interactive commands required.
</RULE>
"""
    blocks = decomposer.decompose(text, base_id="sys")

    assert len(blocks) == 3
    assert blocks[0].block_type == BlockType.SKILL
    assert blocks[0].identity_key == "skill:web_search"
    assert "SerpAPI" in blocks[0].content

    assert blocks[1].block_type == BlockType.SYSTEM
    assert blocks[1].identity_key == "rules:code_style"
    assert "PEP 8" in blocks[1].content

    assert blocks[2].block_type == BlockType.SYSTEM
    assert blocks[2].identity_key == "rules:AGENTS.md"
    assert "Non-interactive" in blocks[2].content


def test_nested_xml_tags():
    decomposer = SubBlockDecomposer()
    text = """
<skills>
  <skill name="browser">
    Interact with web pages.
  </skill>
  <skill name="terminal">
    Execute terminal commands.
  </skill>
</skills>
"""
    blocks = decomposer.decompose(text, base_id="sys")

    assert len(blocks) == 2
    assert blocks[0].block_type == BlockType.SKILL
    assert blocks[0].identity_key == "skill:browser"
    assert "Interact with web pages." in blocks[0].content

    assert blocks[1].block_type == BlockType.SKILL
    assert blocks[1].identity_key == "skill:terminal"
    assert "Execute terminal commands." in blocks[1].content


def test_xml_tags_with_preamble_and_intervening_text():
    decomposer = SubBlockDecomposer()
    text = """You are an intelligent coding agent.

<skills>
Coding in Python.
</skills>

Please obey the following constraints:

<user_rules>
Do not modify files outside workspace.
</user_rules>

End of instructions."""

    blocks = decomposer.decompose(text, default_block_type=BlockType.SYSTEM, base_id="sys")

    assert len(blocks) == 5
    assert blocks[0].block_type == BlockType.SYSTEM
    assert blocks[0].content == "You are an intelligent coding agent."
    assert blocks[0].identity_key == ""

    assert blocks[1].block_type == BlockType.SKILL
    assert blocks[1].content == "Coding in Python."
    assert blocks[1].identity_key == "skills"

    assert blocks[2].block_type == BlockType.SYSTEM
    assert blocks[2].content == "Please obey the following constraints:"

    assert blocks[3].block_type == BlockType.SYSTEM
    assert blocks[3].identity_key == "rules:user_rules"
    assert "Do not modify" in blocks[3].content

    assert blocks[4].block_type == BlockType.SYSTEM
    assert blocks[4].content == "End of instructions."


def test_markdown_sections():
    decomposer = SubBlockDecomposer()
    text = """You are Antigravity assistant.

# Skills
- Code Analysis
- Test Generation

## Tools
- bash
- view_file

### User Rules
1. Never drop tables.
2. Always commit before push.

### Instructions
Execute given tasks thoroughly.
"""
    blocks = decomposer.decompose(text, default_block_type=BlockType.SYSTEM, base_id="sys")

    assert len(blocks) == 5

    # Preamble
    assert blocks[0].block_type == BlockType.SYSTEM
    assert blocks[0].content == "You are Antigravity assistant."

    # Skills
    assert blocks[1].block_type == BlockType.SKILL
    assert blocks[1].identity_key == "skills"
    assert "Code Analysis" in blocks[1].content

    # Tools
    assert blocks[2].block_type == BlockType.TOOL_DEF
    assert blocks[2].identity_key == "tools"
    assert "bash" in blocks[2].content

    # User Rules
    assert blocks[3].block_type == BlockType.SYSTEM
    assert blocks[3].identity_key == "rules:user_rules"
    assert "Never drop tables" in blocks[3].content

    # Instructions
    assert blocks[4].block_type == BlockType.SYSTEM
    assert blocks[4].identity_key == "instructions"
    assert "Execute given tasks thoroughly." in blocks[4].content


def test_markdown_sections_with_subnames():
    decomposer = SubBlockDecomposer()
    text = """
## Skill: Python
Write typed code.

## Tool: Git
Version control tool.

### User Rules - Safety
Do not execute dangerous commands.
"""
    blocks = decomposer.decompose(text, default_block_type=BlockType.SYSTEM, base_id="sys")

    assert len(blocks) == 3
    assert blocks[0].block_type == BlockType.SKILL
    assert blocks[0].identity_key == "skill:Python"
    assert "Write typed code." in blocks[0].content

    assert blocks[1].block_type == BlockType.TOOL_DEF
    assert blocks[1].identity_key == "tool_def:Git"
    assert "Version control tool." in blocks[1].content

    assert blocks[2].block_type == BlockType.SYSTEM
    assert blocks[2].identity_key == "rules:Safety"
    assert "dangerous commands" in blocks[2].content


# ---------------------------------------------------------------------------
# Anthropic Normalizer Integration Tests
# ---------------------------------------------------------------------------


def test_anthropic_decomposes_structured_system_prompt():
    normalizer = AnthropicASTNormalizer()
    payload = {
        "correlation_id": "ant-decomp-1",
        "provider": "anthropic",
        "request_payload": {
            "model": "claude-3-5-sonnet",
            "system": """You are an agent.

<skills>
<skill name="search">Search web</skill>
<skill name="edit">Edit files</skill>
</skills>

<user_rules>
Always use non-interactive commands.
</user_rules>""",
            "messages": [{"role": "user", "content": "Hello"}],
        },
        "response_payload": {
            "content": [{"type": "text", "text": "Hi there!"}],
        },
    }

    turn = normalizer.normalize(payload)
    assert len(turn.system_blocks) == 4

    assert turn.system_blocks[0].content == "You are an agent."
    assert turn.system_blocks[0].block_type == BlockType.SYSTEM

    assert turn.system_blocks[1].block_type == BlockType.SKILL
    assert turn.system_blocks[1].identity_key == "skill:search"
    assert "Search web" in turn.system_blocks[1].content

    assert turn.system_blocks[2].block_type == BlockType.SKILL
    assert turn.system_blocks[2].identity_key == "skill:edit"
    assert "Edit files" in turn.system_blocks[2].content

    assert turn.system_blocks[3].block_type == BlockType.SYSTEM
    assert turn.system_blocks[3].identity_key == "rules:user_rules"
    assert "non-interactive" in turn.system_blocks[3].content


def test_anthropic_thinking_content_blocks():
    normalizer = AnthropicASTNormalizer()
    payload = {
        "correlation_id": "ant-thought-1",
        "provider": "anthropic",
        "request_payload": {
            "model": "claude-3-7-sonnet",
            "messages": [
                {"role": "user", "content": "What is 2 + 2?"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "The user is asking a basic arithmetic question.",
                            "signature": "sig123",
                        },
                        {"type": "text", "text": "2 + 2 is 4."},
                    ],
                },
                {"role": "user", "content": "And 3 + 3?"},
            ],
        },
        "response_payload": {
            "content": [
                {
                    "type": "thinking",
                    "thinking": "Now user asks 3 + 3, which equals 6.",
                    "signature": "sig456",
                },
                {"type": "text", "text": "3 + 3 is 6."},
            ],
        },
    }

    turn = normalizer.normalize(payload)

    # History should contain the thought block
    history_thoughts = [b for b in turn.conversation_history if b.block_type == BlockType.THOUGHT]
    assert len(history_thoughts) == 1
    assert "basic arithmetic" in history_thoughts[0].content
    assert history_thoughts[0].metadata.get("signature") == "sig123"

    # Assistant response blocks should contain the response thought block
    resp_thoughts = [b for b in turn.assistant_blocks if b.block_type == BlockType.THOUGHT]
    assert len(resp_thoughts) == 1
    assert "which equals 6" in resp_thoughts[0].content
    assert resp_thoughts[0].metadata.get("signature") == "sig456"


# ---------------------------------------------------------------------------
# OpenAI Normalizer Integration Tests
# ---------------------------------------------------------------------------


def test_openai_decomposes_developer_and_system_prompts():
    normalizer = OpenAIASTNormalizer()
    payload = {
        "correlation_id": "oai-decomp-1",
        "provider": "openai",
        "request_payload": {
            "model": "o1-mini",
            "messages": [
                {
                    "role": "developer",
                    "content": """# Skills
- Code generation

### User Rules
Never hallucinate imports.
""",
                },
                {"role": "user", "content": "Write a hello world script"},
            ],
        },
        "response_payload": {
            "choices": [
                {"message": {"role": "assistant", "content": "print('hello world')"}},
            ],
        },
    }

    turn = normalizer.normalize(payload)
    assert len(turn.system_blocks) == 2

    assert turn.system_blocks[0].block_type == BlockType.SKILL
    assert turn.system_blocks[0].identity_key == "skills"
    assert "Code generation" in turn.system_blocks[0].content

    assert turn.system_blocks[1].block_type == BlockType.SYSTEM
    assert turn.system_blocks[1].identity_key == "rules:user_rules"
    assert "hallucinate" in turn.system_blocks[1].content


def test_openai_handles_reasoning_and_thinking():
    normalizer = OpenAIASTNormalizer()
    payload = {
        "correlation_id": "oai-reason-1",
        "provider": "openai",
        "request_payload": {
            "model": "o1",
            "messages": [
                {"role": "user", "content": "Solve puzzle"},
                {
                    "role": "assistant",
                    "reasoning_content": "Pondering the puzzle rules in history.",
                    "content": "Step 1 done.",
                },
                {"role": "user", "content": "Continue"},
            ],
        },
        "response_payload": {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "reasoning_content": "Pondering the final move.",
                        "content": "Solved.",
                    },
                }
            ],
        },
    }

    turn = normalizer.normalize(payload)

    hist_thoughts = [b for b in turn.conversation_history if b.block_type == BlockType.THOUGHT]
    assert len(hist_thoughts) == 1
    assert "Pondering the puzzle" in hist_thoughts[0].content

    resp_thoughts = [b for b in turn.assistant_blocks if b.block_type == BlockType.THOUGHT]
    assert len(resp_thoughts) == 1
    assert "Pondering the final move" in resp_thoughts[0].content


def test_openai_decomposes_user_injected_instructions():
    normalizer = OpenAIASTNormalizer()
    payload = {
        "correlation_id": "oai-injected-1",
        "provider": "openai",
        "request_payload": {
            "model": "gpt-4o",
            "messages": [
                {
                    "role": "user",
                    "content": "<context>File: main.py\nline 1: import os</context>Please analyze this file.",
                }
            ],
        },
        "response_payload": {
            "choices": [{"message": {"role": "assistant", "content": "Looks good."}}],
        },
    }

    turn = normalizer.normalize(payload)
    assert len(turn.conversation_history) == 2

    # Context block
    assert turn.conversation_history[0].block_type == BlockType.INJECTED_STATE
    assert turn.conversation_history[0].identity_key == "context"
    assert "File: main.py" in turn.conversation_history[0].content

    # User message
    assert turn.conversation_history[1].block_type == BlockType.USER_MSG
    assert turn.conversation_history[1].content == "Please analyze this file."


# ---------------------------------------------------------------------------
# Gemini Normalizer Integration Tests
# ---------------------------------------------------------------------------


def test_gemini_decomposes_system_instruction():
    normalizer = GeminiASTNormalizer()
    payload = {
        "correlation_id": "gem-decomp-1",
        "provider": "gemini",
        "request_payload": {
            "model": "gemini-2.0-flash",
            "systemInstruction": {
                "parts": [
                    {
                        "text": """<skills>
<skill name="search">Gemini grounding search</skill>
</skills>
<user_rules>
Always cite sources.
</user_rules>"""
                    }
                ]
            },
            "contents": [{"role": "user", "parts": [{"text": "Summarize news"}]}],
        },
        "response_payload": {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Here is the summary."}],
                    }
                }
            ]
        },
    }

    turn = normalizer.normalize(payload)
    assert len(turn.system_blocks) == 2

    assert turn.system_blocks[0].block_type == BlockType.SKILL
    assert turn.system_blocks[0].identity_key == "skill:search"
    assert "Gemini grounding search" in turn.system_blocks[0].content

    assert turn.system_blocks[1].block_type == BlockType.SYSTEM
    assert turn.system_blocks[1].identity_key == "rules:user_rules"
    assert "cite sources" in turn.system_blocks[1].content


def test_gemini_parses_thought_blocks():
    normalizer = GeminiASTNormalizer()
    payload = {
        "correlation_id": "gem-thought-1",
        "provider": "gemini",
        "request_payload": {
            "model": "gemini-2.0-flash-thinking",
            "contents": [
                {"role": "user", "parts": [{"text": "Plan refactor"}]},
                {
                    "role": "model",
                    "parts": [
                        {
                            "thought": True,
                            "text": "Refactor strategy: decouple normalizers first.",
                        },
                        {"text": "Strategy formulated."},
                    ],
                },
                {"role": "user", "parts": [{"text": "Execute step 1"}]},
            ],
        },
        "response_payload": {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": "<thought>Decoupling normalizers and adding tests.</thought>Step 1 complete."
                            }
                        ]
                    }
                }
            ]
        },
    }

    turn = normalizer.normalize(payload)

    # History thought from part with thought=True
    hist_thoughts = [b for b in turn.conversation_history if b.block_type == BlockType.THOUGHT]
    assert len(hist_thoughts) == 1
    assert "decouple normalizers" in hist_thoughts[0].content

    # Response thought parsed from <thought> tags
    resp_thoughts = [b for b in turn.assistant_blocks if b.block_type == BlockType.THOUGHT]
    assert len(resp_thoughts) == 1
    assert "Decoupling normalizers and adding tests." in resp_thoughts[0].content

    # Non-thought response text
    resp_texts = [b for b in turn.assistant_blocks if b.block_type == BlockType.ASSISTANT_MSG]
    assert len(resp_texts) == 1
    assert "Step 1 complete." in resp_texts[0].content
