"""Model-facing message/tool-call value types (transport-agnostic)."""
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class AssistantMessage:
    content: str = ""
    tool_calls: list = field(default_factory=list)
