class AgentError(Exception):
    code = "AGENT_ERROR"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code
        self.message = message

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


class ElementNotFound(AgentError):
    code = "ELEMENT_NOT_FOUND"


class ElementStale(AgentError):
    code = "ELEMENT_STALE"


class ActionRejected(AgentError):
    code = "ACTION_REJECTED"


class WindowNotFound(AgentError):
    code = "WINDOW_NOT_FOUND"


class PermissionDenied(AgentError):
    code = "PERMISSION_DENIED"


class TimeoutError_(AgentError):
    code = "TIMEOUT"


class AdapterUnavailable(AgentError):
    code = "ADAPTER_UNAVAILABLE"


class AmbiguousTarget(AgentError):
    code = "AMBIGUOUS_TARGET"
