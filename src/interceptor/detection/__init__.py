"""Agent and process auto-detection package."""

from src.interceptor.detection.process_detector import (
    AgentIdentity,
    ProcessDetector,
    ProcessInfo,
)

__all__ = ["AgentIdentity", "ProcessDetector", "ProcessInfo"]
