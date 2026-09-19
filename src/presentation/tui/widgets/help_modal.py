"""Help and keyboard shortcuts modal dialog for the ctxins TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class HelpModalScreen(ModalScreen[None]):
    """Modal screen displaying keyboard shortcuts and navigation guide."""

    DEFAULT_CSS = """
    HelpModalScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }

    #help-container {
        width: 82;
        height: auto;
        border: solid #58a6ff;
        background: #161b22;
        padding: 1 2;
    }

    .help-title {
        color: #58a6ff;
        text-style: bold;
        margin-bottom: 1;
    }

    .help-box {
        background: #0d1117;
        border: solid #30363d;
        padding: 1;
        margin-bottom: 1;
    }

    .button-bar {
        margin-top: 1;
        height: 3;
        align: right middle;
    }
    """

    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("enter", "dismiss", "Close"),
        ("?", "dismiss", "Close"),
        ("q", "dismiss", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-container"):
            yield Label("📖 ctxins Keyboard Shortcuts & Help Guide", classes="help-title")

            shortcuts = (
                "[bold #58a6ff]Navigation & Panes[/bold #58a6ff]\n"
                "  [bold yellow]Tab[/bold yellow] / [bold yellow]Shift+Tab[/bold yellow] : Switch focus between Sessions, Timeline, Breakdown, Recommendations\n"
                "  [bold yellow]↑ / ↓[/bold yellow] or [bold yellow]j / k[/bold yellow]   : Navigate list items (sessions in [0] or turns in [1])\n"
                "  [bold yellow]Enter[/bold yellow]             : Select highlighted session or turn\n"
                "  [bold yellow]n / p[/bold yellow]             : Select next / previous AST content block in breakdown\n"
                "\n"
                "[bold #58a6ff]Sessions & Multi-Agent Cockpit[/bold #58a6ff]\n"
                "  [bold yellow]s[/bold yellow]                 : Cycle active session across running / detected agents\n"
                "  [bold yellow]a[/bold yellow]                 : Open interactive Agents selector modal\n"
                "  [bold yellow]h[/bold yellow]                 : Open Hook Guide (connection recipes for any agent/port)\n"
                "  [bold yellow]c[/bold yellow]                 : Copy proxy environment variables to clipboard\n"
                "  [bold yellow]w[/bold yellow]                 : Open real-time Web Dashboard in default browser\n"
                "  [bold yellow]r[/bold yellow]                 : Toggle rule filter (selected turn vs all session turns)\n"
                "  [bold yellow]e[/bold yellow]                 : Export full session report adhering to .jsonc schema\n"
                "  [bold red]q[/bold red]                 : Cleanly shut down proxy, UDS server, and TUI\n"
                "\n"
                "[bold #58a6ff]Inspector Panes Overview[/bold #58a6ff]\n"
                "  [bold cyan][0] Sessions & Agents[/bold cyan]   : Auto-detected processes (AGY, Claude, OpenCode, Aider, etc.)\n"
                "  [bold cyan][1] Turns & Timeline[/bold cyan]    : Live LLM turns, TTFT, token velocity, and duration\n"
                "  [bold cyan][2] Context Composition[/bold cyan]: Token usage by role (system, tools, history, results)\n"
                "  [bold cyan][3] Recommendations[/bold cyan]    : Actionable alerts for CTX-001 (stale tools), CTX-002\n"
                "                               (schema bloat), and CACHE-001 (prefix breaks)"
            )
            yield Static(shortcuts, classes="help-box")

            with Container(classes="button-bar"):
                yield Button("Close [Esc / Enter]", id="btn-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-close":
            self.dismiss()
