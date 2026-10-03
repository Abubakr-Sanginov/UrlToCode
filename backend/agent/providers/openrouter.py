"""OpenRouter as a provider the agent can edit with.

The clone path could always reach OpenRouter, but the editor could not: the
agent spoke the OpenAI Responses API, and OpenRouter serves only
`/chat/completions`. A project configured for OpenRouter could therefore
clone a site and then not edit it - the edit silently went to a different
provider, usually one whose key was dead.

This speaks chat completions, keeps the conversation in the same
`messages` array every OpenAI-compatible server expects, and turns the
streamed deltas back into the agent's events.
"""

import json
from typing import Any, Dict, List, Optional, cast

import httpx
from openai.types.chat import ChatCompletionMessageParam

from agent.providers.base import (
    EventSink,
    ExecutedToolCall,
    ProviderTurn,
    StreamEvent,
)
from agent.tools import ToolCall
from costs.token_usage import TokenUsage
from fs_logging.agent_runs import AgentRunRecorder
from fs_logging.prompt_reports import PromptReportLogger
from llm import OPENROUTER_BASE_URL, Llm, get_openrouter_api_name

# Enough for a whole page of code plus the thinking that produced it. The
# clone path uses the same ceiling.
MAX_OUTPUT_TOKENS = 50000
REQUEST_TIMEOUT_SECONDS = 600


def _serialize_tools(canonical_tools: List[Any]) -> List[Dict[str, Any]]:
    """Agent tools to the chat-completions tool shape.

    The canonical form is already the flat OpenAI function-tool shape, so
    this only has to copy it into the envelope the wire wants. Accepts
    plain dicts too, because that is what a caller holding raw tools has.
    """
    serialized: List[Dict[str, Any]] = []
    for tool in canonical_tools:
        parameters: Dict[str, Any]
        if isinstance(tool, dict):
            name: str = str(tool.get("name") or "")
            description: str = str(tool.get("description") or "")
            parameters = dict(tool.get("parameters") or {})
        else:
            name = str(tool.name)
            description = str(tool.description)
            parameters = tool.parameters or {}
        serialized.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": parameters.get("properties", {}),
                        "required": parameters.get("required", []),
                    },
                },
            }
        )
    return serialized


def _system_and_messages(
    prompt_messages: List[ChatCompletionMessageParam],
) -> tuple[str, List[Dict[str, Any]]]:
    """Split the leading system prompt out, as chat completions wants it."""
    system_parts: List[str] = []
    messages: List[Dict[str, Any]] = []
    for index, message in enumerate(prompt_messages):
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        if index == 0 and message.get("role") == "system":
            system_parts.append(text)
            continue
        messages.append({"role": message.get("role", "user"), "content": text})
    return "\n\n".join(system_parts), messages


class OpenRouterProviderSession:
    """One editing session against an OpenRouter model."""

    def __init__(
        self,
        api_key: str,
        model: Llm,
        prompt_messages: List[ChatCompletionMessageParam],
        tools: List[Any],
        reasoning_effort: Optional[str] = None,
        recorder: Optional[AgentRunRecorder] = None,
    ):
        self._api_key = api_key
        self._model = model
        self._api_model_name = get_openrouter_api_name(model)
        self._tools = _serialize_tools(tools)
        self._reasoning_effort = reasoning_effort
        self._recorder = recorder
        self._total_usage = TokenUsage()
        self._prompt_report_logger = PromptReportLogger(
            provider="openrouter",
            model=model,
            api_model_name=self._api_model_name,
        )
        self._system_prompt, self._messages = _system_and_messages(prompt_messages)

    def _body(self, stream: bool) -> Dict[str, Any]:
        messages: List[Dict[str, Any]] = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.extend(self._messages)
        body: Dict[str, Any] = {
            "model": self._api_model_name,
            "messages": messages,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "stream": stream,
        }
        if self._tools:
            body["tools"] = self._tools
            body["tool_choice"] = "auto"
        if self._reasoning_effort:
            # OpenRouter takes the same reasoning block the OpenAI API
            # does. Without it this model spends its whole output budget
            # thinking and returns no code at all.
            body["reasoning"] = {"effort": self._reasoning_effort}
        return body

    async def stream_turn(self, on_event: EventSink) -> ProviderTurn:
        body = self._body(stream=True)
        self._prompt_report_logger.record_request(body)
        if self._recorder is not None:
            self._recorder.record_llm_request(
                "openrouter", self._api_model_name, body
            )

        assistant_text = ""
        tool_calls: List[ToolCall] = []
        # Streamed tool arguments arrive as JSON fragments keyed by index.
        pending: Dict[int, Dict[str, str]] = {}
        usage: Optional[TokenUsage] = None

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://urltocode.app",
        }
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            async with client.stream(
                "POST",
                f"{OPENROUTER_BASE_URL}/chat/completions",
                json=body,
                headers=headers,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk: Dict[str, Any] = json.loads(data)
                    if chunk.get("error"):
                        raise RuntimeError(
                            f"OpenRouter returned an error: {chunk['error']}"
                        )
                    turn_usage = _usage_of(chunk)
                    if turn_usage is not None:
                        usage = turn_usage
                    choices: List[Dict[str, Any]] = chunk.get("choices") or []
                    for choice in choices:
                        delta: Dict[str, Any] = choice.get("delta") or {}
                        text: str = delta.get("content") or ""
                        if text:
                            assistant_text += text
                            await on_event(
                                StreamEvent(type="assistant_delta", text=text)
                            )
                        calls: List[Dict[str, Any]] = delta.get("tool_calls") or []
                        for call in calls:
                            index = int(call.get("index", 0))
                            slot = pending.setdefault(index, {"id": "", "name": ""})
                            if call.get("id"):
                                slot["id"] = str(call["id"])
                            function: Dict[str, Any] = call.get("function") or {}
                            if function.get("name"):
                                slot["name"] = str(function["name"])
                            arguments: str = str(function.get("arguments") or "")
                            if arguments:
                                slot["arguments"] = (
                                    slot.get("arguments", "") + arguments
                                )
                                await on_event(
                                    StreamEvent(
                                        type="tool_call_delta",
                                        tool_call_id=slot["id"],
                                        tool_name=slot["name"],
                                        tool_arguments=arguments,
                                    )
                                )

        for index in sorted(pending):
            slot = pending[index]
            if not slot["name"]:
                continue
            raw_arguments = slot.get("arguments", "")
            parsed: Dict[str, Any] = {}
            try:
                if raw_arguments:
                    loaded = json.loads(raw_arguments)
                    if isinstance(loaded, dict):
                        parsed = loaded
            except json.JSONDecodeError:
                # A model that streamed half an argument has still asked
                # for a tool; reporting it with no arguments lets the agent
                # answer with a real error instead of crashing here.
                parsed = {}
            tool_calls.append(
                ToolCall(id=slot["id"], name=slot["name"], arguments=parsed)
            )

        if usage is not None:
            self._prompt_report_logger.record_usage(usage)
            self._total_usage.accumulate(usage)
        if self._recorder is not None:
            self._recorder.record_llm_response(assistant_text, tool_calls, usage)

        return ProviderTurn(
            assistant_text=assistant_text,
            tool_calls=tool_calls,
            assistant_turn=None,
        )

    async def append_tool_results(
        self,
        turn: ProviderTurn,
        executed_tool_calls: list[ExecutedToolCall],
    ) -> None:
        # The assistant turn has to be replayed before its results, or the
        # model sees a tool result for a call it never made.
        self._messages.append(
            {
                "role": "assistant",
                "content": turn.assistant_text,
                **(
                    {
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": json.dumps(call.arguments),
                                },
                            }
                            for call in turn.tool_calls
                        ]
                    }
                    if turn.tool_calls
                    else {}
                ),
            }
        )
        for executed in executed_tool_calls:
            # The structured result is what the model reasons over; the
            # summary is prose for a human and adds nothing here.
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": executed.tool_call.id,
                    "content": json.dumps(
                        executed.result.result, ensure_ascii=False
                    ),
                }
            )

    def total_cost_usd(self) -> Optional[float]:
        # OpenRouter model names are not in the pricing table, and guessing
        # a price is worse than reporting none.
        return None

    async def close(self) -> None:
        return None


def _usage_of(chunk: Dict[str, Any]) -> Optional[TokenUsage]:
    raw: Dict[str, Any] = chunk.get("usage") or {}
    if not raw:
        return None
    prompt_tokens = int(raw.get("prompt_tokens") or 0)
    completion_tokens = int(raw.get("completion_tokens") or 0)
    details: Dict[str, Any] = raw.get("prompt_tokens_details") or {}
    return TokenUsage(
        input=prompt_tokens,
        output=completion_tokens,
        cache_read=int(details.get("cached_tokens") or 0),
        total=int(raw.get("total_tokens") or (prompt_tokens + completion_tokens)),
    )


__all__ = ["OpenRouterProviderSession"]
