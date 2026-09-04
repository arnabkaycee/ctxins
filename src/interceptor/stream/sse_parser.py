"""Server-Sent Events (SSE) streaming parser."""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from typing import List, Optional, Union


@dataclass(slots=True)
class SSEEvent:
    """Represents a single parsed Server-Sent Event."""

    event: str = "message"
    data: str = ""
    id: Optional[str] = None
    retry: Optional[int] = None


class SSEParser:
    """Incremental parser for Server-Sent Events (SSE) supporting chunk fragmentation.

    Handles split chunks across arbitrary byte boundaries, multi-byte UTF-8 sequences,
    various line terminators (CRLF, LF, CR), SSE comments, multi-line data fields,
    and event dispatches on blank lines.
    """

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._buffer: str = ""
        self._current_event: Optional[str] = None
        self._data_lines: List[str] = []
        self._current_id: Optional[str] = None
        self._current_retry: Optional[int] = None

    def feed(self, chunk: Union[bytes, str]) -> List[SSEEvent]:
        """Ingest a chunk and yield any complete parsed SSEEvents."""
        if not chunk:
            return self.close()

        if isinstance(chunk, bytes):
            text = self._decoder.decode(chunk, final=False)
        else:
            text = chunk

        self._buffer += text
        events: List[SSEEvent] = []

        while True:
            # Find next line break: \r\n, \r, or \n
            cr_pos = self._buffer.find("\r")
            lf_pos = self._buffer.find("\n")

            if cr_pos != -1 and (lf_pos == -1 or cr_pos < lf_pos):
                # Carriage return appears first
                if cr_pos == len(self._buffer) - 1:
                    # \r is at the very end of buffer; wait for next chunk in case of \r\n
                    break
                if self._buffer[cr_pos + 1] == "\n":
                    nl_pos = cr_pos
                    skip = 2
                else:
                    nl_pos = cr_pos
                    skip = 1
            elif lf_pos != -1:
                nl_pos = lf_pos
                skip = 1
            else:
                break

            line = self._buffer[:nl_pos]
            self._buffer = self._buffer[nl_pos + skip:]
            event = self._process_line(line)
            if event is not None:
                events.append(event)

        return events

    def _process_line(self, line: str) -> Optional[SSEEvent]:
        """Process a single line from the SSE stream."""
        if not line:
            # Empty line dispatches the pending event
            return self._dispatch()

        if line.startswith(":"):
            # Comment line, ignore
            return None

        field, has_colon, value = line.partition(":")
        if has_colon and value.startswith(" "):
            value = value[1:]

        if field == "event":
            self._current_event = value
        elif field == "data":
            self._data_lines.append(value)
        elif field == "id":
            if "\0" not in value:
                self._current_id = value
        elif field == "retry":
            if value.isdigit():
                self._current_retry = int(value)

        return None

    def _dispatch(self) -> Optional[SSEEvent]:
        """Dispatch accumulated event fields if any data or event was received."""
        if not self._data_lines and self._current_event is None:
            return None

        event = SSEEvent(
            event=self._current_event or "message",
            data="\n".join(self._data_lines),
            id=self._current_id,
            retry=self._current_retry,
        )
        self._current_event = None
        self._data_lines = []
        self._current_id = None
        self._current_retry = None
        return event

    def close(self) -> List[SSEEvent]:
        """Flush any remaining buffered text and dispatch any pending event."""
        events: List[SSEEvent] = []
        remaining = self._decoder.decode(b"", final=True)
        self._buffer += remaining

        if self._buffer:
            lines = self._buffer.splitlines()
            self._buffer = ""
            for line in lines:
                ev = self._process_line(line)
                if ev is not None:
                    events.append(ev)

        ev = self._dispatch()
        if ev is not None:
            events.append(ev)

        return events
