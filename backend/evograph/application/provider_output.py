"""Fixed diagnostics for the unified planner's provider-output integrity gate."""


class ProviderOutputError(RuntimeError):
    MESSAGES = {
        "provider_output_limited": "模型输出达到服务端生成上限，候选未自动应用",
        "provider_output_incomplete": "模型输出缺少有效结束标记或使用不支持的响应，候选未自动应用",
    }

    def __init__(self, code):
        if code not in self.MESSAGES:
            raise ValueError("未知的模型输出诊断")
        self.code = code
        super().__init__(self.MESSAGES[code])


def require_complete_provider_output(call):
    """Check observed protocol termination, never infer JSON or semantic validity."""
    metadata = call.get("request_metadata")
    if metadata is None:
        return  # Existing synthetic/legacy settings streams have no adapter metadata.
    finishes = call.get("response_finish", [])
    reasons = [item.get("finish_reason", item.get("stop_reason")) for item in finishes]
    if any(isinstance(reason, str) and reason in {"length", "max_tokens"} for reason in reasons):
        raise ProviderOutputError("provider_output_limited")
    provider = metadata.get("provider")
    allowed = {"openai_compatible": {"stop", "tool_calls"},
               "anthropic": {"end_turn", "tool_use", "stop_sequence"}}.get(provider, set())
    expected = {"openai_compatible": "[DONE]", "anthropic": "message_stop"}.get(provider)
    if (not reasons or any(not isinstance(reason, str) or reason not in allowed for reason in reasons)
            or not call.get("received_finish_marker")
            or not call.get("received_terminal_marker")
            or call.get("terminal_marker") != expected):
        raise ProviderOutputError("provider_output_incomplete")
