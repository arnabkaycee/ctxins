"""Unit tests for ProcessDetector and AgentIdentity matching."""

from unittest.mock import patch

from src.interceptor.detection.process_detector import (
    ProcessDetector,
    ProcessInfo,
)


def test_identify_from_process_known_agents():
    detector = ProcessDetector()

    # 1. Antigravity (agy)
    p_agy = ProcessInfo(pid=1001, name="agy", cmdline="/usr/local/bin/agy")
    id_agy = detector.identify_from_process(p_agy)
    assert id_agy.name == "agy"
    assert id_agy.is_known is True
    assert id_agy.display_name == "Antigravity (agy)"
    assert id_agy.pid == 1001

    # 2. Claude Code
    p_claude = ProcessInfo(pid=1002, name="node", cmdline="node /path/to/@anthropic-ai/claude-code")
    id_claude = detector.identify_from_process(p_claude)
    assert id_claude.name == "claude-code"
    assert id_claude.is_known is True
    assert id_claude.display_name == "Claude Code"

    # Claude binary
    p_claude_bin = ProcessInfo(pid=1003, name="claude", cmdline="claude")
    assert detector.identify_from_process(p_claude_bin).name == "claude-code"

    # 3. Aider
    p_aider = ProcessInfo(
        pid=1004, name="python3", cmdline="python3 -m aider --model claude-3-5-sonnet"
    )
    id_aider = detector.identify_from_process(p_aider)
    assert id_aider.name == "aider"
    assert id_aider.is_known is True

    # 4. OpenCode
    p_opencode = ProcessInfo(pid=1005, name="opencode", cmdline="opencode")
    id_opencode = detector.identify_from_process(p_opencode)
    assert id_opencode.name == "opencode"
    assert id_opencode.is_known is True

    # 5. Pi
    p_pi = ProcessInfo(pid=1006, name="pi", cmdline="pi")
    id_pi = detector.identify_from_process(p_pi)
    assert id_pi.name == "pi"
    assert id_pi.is_known is True


def test_identify_from_process_custom():
    detector = ProcessDetector()
    p_custom = ProcessInfo(pid=2001, name="my_agent", cmdline="python run_custom_agent.py")
    identity = detector.identify_from_process(p_custom)
    assert identity.name == "custom"
    assert identity.is_known is False
    assert identity.pid == 2001


def test_identify_from_headers():
    detector = ProcessDetector()

    # Claude Code
    id_c = detector.identify_from_headers({"user-agent": "claude-code/0.2.29"})
    assert id_c is not None and id_c.name == "claude-code"

    # Antigravity
    id_a = detector.identify_from_headers({"user-agent": "antigravity/1.0.0"})
    assert id_a is not None and id_a.name == "agy"

    # Aider
    id_ai = detector.identify_from_headers({"user-agent": "aider/0.50.0"})
    assert id_ai is not None and id_ai.name == "aider"

    # OpenCode
    id_oc = detector.identify_from_headers({"user-agent": "opencode/1.2.0"})
    assert id_oc is not None and id_oc.name == "opencode"

    # Pi
    id_pi = detector.identify_from_headers({"user-agent": "pi-agent/0.1"})
    assert id_pi is not None and id_pi.name == "pi"

    # Unknown
    assert detector.identify_from_headers({"user-agent": "curl/7.88.1"}) is None


def test_identify_client_hybrid_fallback():
    detector = ProcessDetector()
    # Process is generic node without claude in cmdline
    generic_node = ProcessInfo(pid=3001, name="node", cmdline="node server.js")
    with patch.object(detector, "lookup_by_port", return_value=generic_node):
        identity = detector.identify_client(
            client_ip="127.0.0.1",
            client_port=54321,
            headers={"user-agent": "claude-code/0.2.29"},
        )
        assert identity.name == "claude-code"
        assert identity.pid == 3001
        assert identity.detection_source == "hybrid"


def test_lookup_by_port_mocked_lsof():
    detector = ProcessDetector(cache_ttl_seconds=10.0)

    lsof_output = b"p55555\ncagy\nf3\n"
    with (
        patch("subprocess.check_output") as mock_exec,
        patch("shutil.which", return_value="/usr/sbin/lsof"),
    ):

        def side_effect(cmd, **kwargs):
            if cmd[0] == "lsof":
                return lsof_output
            elif cmd[0] == "ps":
                return b"/usr/local/bin/agy\n"
            return b""

        mock_exec.side_effect = side_effect

        proc = detector.lookup_by_port(port=43210)
        assert proc is not None
        assert proc.pid == 55555
        assert proc.name == "agy"
        assert proc.cmdline == "/usr/local/bin/agy"

        # Cache test: subsequent call within TTL should return cached object without executing subprocess
        mock_exec.reset_mock()
        cached_proc = detector.lookup_by_port(port=43210)
        assert cached_proc == proc
        mock_exec.assert_not_called()
