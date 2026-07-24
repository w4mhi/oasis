"""Action gate: classify each tool call as execute / confirm / blocked.

Read tools always execute. Action tools (config.action_tools) execute only if
allowlisted in config.auto_actions; otherwise they require confirmation — unless
actions_enabled is false (read-only mode), in which case they are blocked.
"""


class ActionGate:
    def __init__(self, cfg, registry):
        self._actions = set(cfg.action_tools)
        self._auto = set(cfg.auto_actions)
        self._enabled = cfg.actions_enabled
        self._registry = registry

    def classify(self, name, args):
        if name not in self._actions:
            return "execute"
        if not self._enabled:
            return "blocked"
        if name in self._auto:
            return "execute"
        return "confirm"

    def pending(self, name, args):
        return self._registry.add(name, args)
