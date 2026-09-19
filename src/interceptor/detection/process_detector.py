"""Process detection and agent identification for zero-hook auto-detection."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProcessInfo:
    """Diagnostic information regarding a local client process."""

    pid: int
    name: str
    cmdline: str
    cwd: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "name": self.name,
            "cmdline": self.cmdline,
            "cwd": self.cwd,
        }


@dataclass(slots=True)
class AgentIdentity:
    """Canonical agent identity detected before or during interception."""

    name: str
    display_name: str
    is_known: bool
    pid: Optional[int] = None
    command: Optional[str] = None
    confidence: float = 1.0
    detection_source: str = "process"
    process_info: Optional[ProcessInfo] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "displayName": self.display_name,
            "isKnown": self.is_known,
            "pid": self.pid,
            "command": self.command,
            "confidence": self.confidence,
            "detectionSource": self.detection_source,
            "process": self.process_info.to_dict() if self.process_info else None,
        }


class ProcessDetector:
    """Identifies originating processes for network connections and matches known agent harnesses."""

    def __init__(self, cache_ttl_seconds: float = 5.0) -> None:
        self.cache_ttl = cache_ttl_seconds
        self._port_cache: Dict[Tuple[str, int], Tuple[float, Optional[ProcessInfo]]] = {}

    def lookup_by_port(self, port: int, ip: str = "127.0.0.1") -> Optional[ProcessInfo]:
        """Resolve originating PID and process info for a local TCP client port."""
        if port <= 0:
            return None

        key = (ip, port)
        now = time.monotonic()
        if key in self._port_cache:
            ts, cached_proc = self._port_cache[key]
            if now - ts < self.cache_ttl:
                return cached_proc

        proc = self._lookup_via_lsof(port)
        if proc is None and os.name == "posix" and os.path.exists("/proc/net/tcp"):
            proc = self._lookup_via_procfs(port)

        self._port_cache[key] = (now, proc)
        return proc

    def _lookup_via_lsof(self, port: int) -> Optional[ProcessInfo]:
        """Query lsof to find PID and command associated with a TCP port."""
        if not shutil.which("lsof"):
            return None

        try:
            # -nP: no host/port name resolution (fast)
            # -iTCP:<port>: filter by TCP port
            # -F pc: output machine-readable pid and command lines
            out = subprocess.check_output(
                ["lsof", "-nP", f"-iTCP:{port}", "-F", "pc"],
                stderr=subprocess.DEVNULL,
                timeout=0.8,
            ).decode("utf-8", errors="replace")
        except (subprocess.SubprocessError, OSError):
            return None

        pid: Optional[int] = None
        comm: Optional[str] = None

        for line in out.splitlines():
            if line.startswith("p") and len(line) > 1:
                try:
                    pid = int(line[1:])
                except ValueError:
                    pass
            elif line.startswith("c") and len(line) > 1:
                comm = line[1:].strip()

        if pid is None:
            return None

        cmdline = self._get_cmdline_for_pid(pid) or comm or f"pid_{pid}"
        cwd = self._get_cwd_for_pid(pid)

        return ProcessInfo(
            pid=pid,
            name=comm or "unknown",
            cmdline=cmdline,
            cwd=cwd,
        )

    def _get_cmdline_for_pid(self, pid: int) -> Optional[str]:
        """Fetch full command line for a PID using ps or /proc."""
        # Check /proc first on Linux
        proc_cmdline = f"/proc/{pid}/cmdline"
        if os.path.exists(proc_cmdline):
            try:
                with open(proc_cmdline, "rb") as f:
                    content = f.read().replace(b"\x00", b" ").strip()
                    if content:
                        return content.decode("utf-8", errors="replace")
            except OSError:
                pass

        if shutil.which("ps"):
            try:
                out = subprocess.check_output(
                    ["ps", "-p", str(pid), "-o", "args="],
                    stderr=subprocess.DEVNULL,
                    timeout=0.5,
                ).decode("utf-8", errors="replace").strip()
                if out:
                    return out
            except (subprocess.SubprocessError, OSError):
                pass

        return None

    def _get_cwd_for_pid(self, pid: int) -> Optional[str]:
        """Fetch current working directory for a PID."""
        proc_cwd = f"/proc/{pid}/cwd"
        if os.path.islink(proc_cwd):
            try:
                return os.readlink(proc_cwd)
            except OSError:
                pass

        if shutil.which("lsof"):
            try:
                out = subprocess.check_output(
                    ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-F", "n"],
                    stderr=subprocess.DEVNULL,
                    timeout=0.5,
                ).decode("utf-8", errors="replace")
                for line in out.splitlines():
                    if line.startswith("n") and len(line) > 1:
                        return line[1:].strip()
            except (subprocess.SubprocessError, OSError):
                pass

        return None

    def _lookup_via_procfs(self, port: int) -> Optional[ProcessInfo]:
        """Fallback Linux /proc/net/tcp parser to resolve port to PID."""
        target_hex = f"{port:04X}"
        target_inode: Optional[str] = None

        try:
            with open("/proc/net/tcp", "r") as f:
                lines = f.readlines()
            for line in lines[1:]:
                parts = line.strip().split()
                if len(parts) >= 10:
                    local_addr = parts[1]
                    if local_addr.endswith(f":{target_hex}"):
                        target_inode = parts[9]
                        break
        except OSError:
            return None

        if not target_inode:
            return None

        # Search /proc/<pid>/fd for socket:[<target_inode>]
        for entry in os.scandir("/proc"):
            if entry.is_dir() and entry.name.isdigit():
                pid = int(entry.name)
                fd_dir = os.path.join(entry.path, "fd")
                if os.path.isdir(fd_dir):
                    try:
                        for fd in os.scandir(fd_dir):
                            try:
                                if os.path.islink(fd.path):
                                    link = os.readlink(fd.path)
                                    if f"socket:[{target_inode}]" in link:
                                        cmdline = self._get_cmdline_for_pid(pid)
                                        return ProcessInfo(
                                            pid=pid,
                                            name=entry.name,
                                            cmdline=cmdline or f"pid_{pid}",
                                            cwd=self._get_cwd_for_pid(pid),
                                        )
                            except OSError:
                                continue
                    except OSError:
                        continue
        return None

    def is_process_alive(self, pid: int) -> bool:
        """Check if process with specified PID is still running."""
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def identify_from_process(self, proc: ProcessInfo) -> AgentIdentity:
        """Identify known agent harness from ProcessInfo."""
        cmdline = proc.cmdline.lower()
        comm = proc.name.lower()

        # 1. Antigravity CLI (agy)
        if (
            comm in ("agy", "antigravity", "antigravity-cli")
            or re.search(r"(^|[/\s])agy(\s|$)", cmdline)
            or "antigravity" in cmdline
        ):
            return AgentIdentity(
                name="agy",
                display_name="Antigravity (agy)",
                is_known=True,
                pid=proc.pid,
                command=proc.cmdline,
                confidence=1.0,
                detection_source="process",
                process_info=proc,
            )

        # 2. Claude Code (claude)
        if (
            comm == "claude"
            or "claude-code" in cmdline
            or "@anthropic-ai/claude-code" in cmdline
            or re.search(r"(^|[/\s])claude(\s|$)", cmdline)
        ):
            return AgentIdentity(
                name="claude-code",
                display_name="Claude Code",
                is_known=True,
                pid=proc.pid,
                command=proc.cmdline,
                confidence=1.0,
                detection_source="process",
                process_info=proc,
            )

        # 3. Aider (aider)
        if (
            comm == "aider"
            or re.search(r"(^|[/\s])aider(\s|$)", cmdline)
            or "python -m aider" in cmdline
            or "python3 -m aider" in cmdline
        ):
            return AgentIdentity(
                name="aider",
                display_name="Aider",
                is_known=True,
                pid=proc.pid,
                command=proc.cmdline,
                confidence=1.0,
                detection_source="process",
                process_info=proc,
            )

        # 4. OpenCode (opencode)
        if (
            comm == "opencode"
            or "opencode" in cmdline
        ):
            return AgentIdentity(
                name="opencode",
                display_name="OpenCode",
                is_known=True,
                pid=proc.pid,
                command=proc.cmdline,
                confidence=1.0,
                detection_source="process",
                process_info=proc,
            )

        # 5. Pi (pi)
        if comm == "pi" or re.search(r"(^|[/\s])pi(\s|$)", cmdline):
            return AgentIdentity(
                name="pi",
                display_name="Pi",
                is_known=True,
                pid=proc.pid,
                command=proc.cmdline,
                confidence=0.9,
                detection_source="process",
                process_info=proc,
            )

        # Process found, but not recognized as a pre-indexed agent
        return AgentIdentity(
            name="custom",
            display_name=f"Process: {proc.name}",
            is_known=False,
            pid=proc.pid,
            command=proc.cmdline,
            confidence=0.7,
            detection_source="process",
            process_info=proc,
        )

    def identify_from_headers(self, headers: Dict[str, str]) -> Optional[AgentIdentity]:
        """Secondary fallback: identify agent from HTTP request headers."""
        headers_lower = {k.lower(): v for k, v in headers.items()}
        ua = headers_lower.get("user-agent", "").lower()
        anthropic_client = headers_lower.get("anthropic-client", "").lower()

        # Claude Code
        if "claude-code" in ua or "claude-code" in anthropic_client:
            return AgentIdentity(
                name="claude-code",
                display_name="Claude Code",
                is_known=True,
                confidence=0.9,
                detection_source="header",
            )

        # Antigravity (agy)
        if "antigravity" in ua:
            return AgentIdentity(
                name="agy",
                display_name="Antigravity (agy)",
                is_known=True,
                confidence=0.9,
                detection_source="header",
            )

        # Aider
        if "aider" in ua:
            return AgentIdentity(
                name="aider",
                display_name="Aider",
                is_known=True,
                confidence=0.9,
                detection_source="header",
            )

        # OpenCode
        if "opencode" in ua:
            return AgentIdentity(
                name="opencode",
                display_name="OpenCode",
                is_known=True,
                confidence=0.9,
                detection_source="header",
            )

        # Pi
        if "pi-agent" in ua or "pi/" in ua:
            return AgentIdentity(
                name="pi",
                display_name="Pi",
                is_known=True,
                confidence=0.8,
                detection_source="header",
            )

        return None

    def identify_client(
        self,
        client_ip: Optional[str] = None,
        client_port: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> AgentIdentity:
        """Identify client process and agent identity before intercepting traffic."""
        proc: Optional[ProcessInfo] = None
        if client_port and client_port > 0:
            proc = self.lookup_by_port(client_port, client_ip or "127.0.0.1")

        if proc is not None:
            identity = self.identify_from_process(proc)
            if identity.is_known:
                return identity
            # If process is generic (e.g. node, python) and not recognized, check headers
            if headers:
                header_id = self.identify_from_headers(headers)
                if header_id and header_id.is_known:
                    header_id.pid = proc.pid
                    header_id.command = proc.cmdline
                    header_id.process_info = proc
                    header_id.confidence = 0.95
                    header_id.detection_source = "hybrid"
                    return header_id
            return identity

        # Fallback to headers if process lookup wasn't possible (e.g. non-local connection)
        if headers:
            header_id = self.identify_from_headers(headers)
            if header_id:
                return header_id

        return AgentIdentity(
            name="unknown",
            display_name="Unknown Agent",
            is_known=False,
            confidence=0.0,
            detection_source="none",
        )

    def scan_running_agents(self) -> List[AgentIdentity]:
        """Scan the local system for currently running known agent processes."""
        agents: List[AgentIdentity] = []
        my_pid = os.getpid()

        if shutil.which("ps"):
            try:
                out = subprocess.check_output(
                    ["ps", "-e", "-o", "pid=,comm=,args="],
                    stderr=subprocess.DEVNULL,
                    timeout=1.5,
                ).decode("utf-8", errors="replace")
                for line in out.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(None, 2)
                    if len(parts) < 2:
                        continue
                    try:
                        pid = int(parts[0])
                    except ValueError:
                        continue
                    if pid == my_pid:
                        continue
                    comm = parts[1]
                    args = parts[2] if len(parts) > 2 else comm

                    # Ignore greps, test runners, linters, ctxins itself
                    args_lower = args.lower()
                    if any(
                        skip in args_lower
                        for skip in ("grep", "pytest", "test_", "ruff", "mypy", "ctxins")
                    ):
                        continue

                    proc = ProcessInfo(pid=pid, name=comm, cmdline=args)
                    identity = self.identify_from_process(proc)
                    if identity.is_known:
                        agents.append(identity)
            except Exception as e:
                logger.debug("Failed to scan processes via ps: %s", e)

        return agents
