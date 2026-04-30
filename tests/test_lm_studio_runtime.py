"""Tests for livery.runtimes.lm_studio — native-format agent loop."""

from __future__ import annotations

import io
import json
from unittest.mock import patch, MagicMock

from livery.runtimes import lm_studio


def _mock_completion(content: str, tool_calls: list | None = None) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def _native_tool_call(name: str, arguments: dict, id_: str = "call_1") -> dict:
    return {
        "id": id_,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _mock_urlopen_sequence(responses: list[dict]) -> MagicMock:
    iterator = iter(responses)

    def _factory(*args, **kwargs):
        nxt = next(iterator)
        ctx = MagicMock()
        ctx.__enter__.return_value.read.return_value = json.dumps(nxt).encode("utf-8")
        ctx.__exit__.return_value = False
        return ctx

    return MagicMock(side_effect=_factory)


# ---------------------------------------------------------------------------
# extract_tool_calls
# ---------------------------------------------------------------------------


def test_extract_tool_calls_single():
    text = 'Here I go.\n<tool_call>\n{"name": "web_fetch", "arguments": {"url": "https://x.com"}}\n</tool_call>\nthinking...'
    calls = lm_studio.extract_tool_calls(text)
    assert calls == [{"name": "web_fetch", "arguments": {"url": "https://x.com"}}]


def test_extract_tool_calls_multiple_in_one_response():
    text = (
        '<tool_call>{"name":"a","arguments":{"x":1}}</tool_call>'
        'between\n'
        '<tool_call>\n{"name":"b","arguments":{"y":2}}\n</tool_call>'
    )
    calls = lm_studio.extract_tool_calls(text)
    assert calls == [
        {"name": "a", "arguments": {"x": 1}},
        {"name": "b", "arguments": {"y": 2}},
    ]


def test_extract_tool_calls_ignores_malformed_json():
    text = '<tool_call>{not_valid_json}</tool_call><tool_call>{"name":"ok","arguments":{}}</tool_call>'
    calls = lm_studio.extract_tool_calls(text)
    assert calls == [{"name": "ok", "arguments": {}}]


def test_extract_tool_calls_ignores_missing_name():
    text = '<tool_call>{"arguments":{"x":1}}</tool_call>'
    assert lm_studio.extract_tool_calls(text) == []


def test_extract_tool_calls_empty_when_no_blocks():
    assert lm_studio.extract_tool_calls("just a plain answer") == []


# ---------------------------------------------------------------------------
# build_tool_system_prompt
# ---------------------------------------------------------------------------


def test_build_tool_system_prompt_includes_names_and_instructions():
    schemas = [
        {
            "function": {
                "name": "web_fetch",
                "description": "Fetch a URL",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "Target URL"}},
                    "required": ["url"],
                },
            }
        }
    ]
    prompt = lm_studio.build_tool_system_prompt(schemas)
    assert "web_fetch" in prompt
    assert "Fetch a URL" in prompt
    assert "Target URL" in prompt
    assert "<tool_call>" in prompt
    assert "WHEN TO STOP" in prompt


# ---------------------------------------------------------------------------
# Single-shot (no tools)
# ---------------------------------------------------------------------------


def test_call_chat_completions_posts_and_returns_content():
    with patch("urllib.request.urlopen", _mock_urlopen_sequence([_mock_completion("hello")])) as uo:
        out = lm_studio.call_chat_completions(model="gemma", prompt="hi")
    assert out == "hello"
    body = json.loads(uo.call_args.args[0].data.decode("utf-8"))
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert "tools" not in body  # never sent in single-shot mode


def test_main_no_tools_reads_stdin_writes_stdout(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("say hi"))
    with patch("urllib.request.urlopen", _mock_urlopen_sequence([_mock_completion("hi!")])):
        rc = lm_studio.main(["--model", "gemma", "--no-tools"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "hi!" in captured.out


def test_main_empty_stdin_errors(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    rc = lm_studio.main(["--model", "gemma"])
    assert rc == 2
    assert "no prompt" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Agent loop (native-format tool calls)
# ---------------------------------------------------------------------------


def test_agent_loop_returns_immediately_with_no_tool_calls():
    responses = [_mock_completion("final answer with no tool_call blocks")]
    with patch("urllib.request.urlopen", _mock_urlopen_sequence(responses)):
        out = lm_studio.run_agent_loop(model="gemma", prompt="hi")
    assert out == "final answer with no tool_call blocks"


def test_agent_loop_executes_tool_call_and_feeds_result_back():
    call_text = '<tool_call>{"name":"web_fetch","arguments":{"url":"https://example.com"}}</tool_call>'
    responses = [
        _mock_completion(call_text),
        _mock_completion("after fetching, final summary here"),
    ]
    with patch("urllib.request.urlopen", _mock_urlopen_sequence(responses)) as uo:
        with patch("livery.runtimes.lm_studio.execute_tool_call", return_value="<fetched text>") as mock_exec:
            out = lm_studio.run_agent_loop(model="gemma", prompt="summarize example.com")

    assert out == "after fetching, final summary here"
    mock_exec.assert_called_once_with("web_fetch", {"url": "https://example.com"})

    # Second POST should carry the tool_response block as a user message.
    second_body = json.loads(uo.call_args_list[1].args[0].data.decode("utf-8"))
    last_msg = second_body["messages"][-1]
    assert last_msg["role"] == "user"
    assert "<tool_response" in last_msg["content"]
    assert "<fetched text>" in last_msg["content"]


def test_agent_loop_passes_tools_and_system_instructions():
    """Both: native `tools` param for well-behaved models, plus system prompt fallback for Gemma-style."""
    with patch("urllib.request.urlopen", _mock_urlopen_sequence([_mock_completion("done")])) as uo:
        lm_studio.run_agent_loop(model="any", prompt="hi")
    body = json.loads(uo.call_args_list[0].args[0].data.decode("utf-8"))
    # Native tool list present (for gpt-oss / Qwen / etc.).
    assert "tools" in body
    tool_names = {t["function"]["name"] for t in body["tools"]}
    assert "web_fetch" in tool_names and "web_search" in tool_names
    # System prompt fallback present (for models that ignore `tools`).
    messages = body["messages"]
    assert messages[0]["role"] == "system"
    assert "<tool_call>" in messages[0]["content"]


def test_agent_loop_handles_native_tool_calls():
    """OpenAI-structured tool_calls path (gpt-oss style): uses role=tool messages with tool_call_id."""
    call = _native_tool_call("web_fetch", {"url": "https://x.com"}, id_="call_xyz")
    responses = [
        _mock_completion("", tool_calls=[call]),
        _mock_completion("summary"),
    ]
    with patch("urllib.request.urlopen", _mock_urlopen_sequence(responses)) as uo:
        with patch("livery.runtimes.lm_studio.execute_tool_call", return_value="<fetched>") as mock_exec:
            out = lm_studio.run_agent_loop(model="gpt-oss:20b", prompt="fetch x.com")

    assert out == "summary"
    mock_exec.assert_called_once_with("web_fetch", {"url": "https://x.com"})
    # Second POST must include a role=tool message with the call's id.
    second_body = json.loads(uo.call_args_list[1].args[0].data.decode("utf-8"))
    tool_messages = [m for m in second_body["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_xyz"
    assert tool_messages[0]["content"] == "<fetched>"


def test_agent_loop_respects_max_iterations():
    call_text = '<tool_call>{"name":"web_fetch","arguments":{"url":"https://x.com"}}</tool_call>'
    responses = [_mock_completion(call_text) for _ in range(5)]
    with patch("urllib.request.urlopen", _mock_urlopen_sequence(responses)):
        with patch("livery.runtimes.lm_studio.execute_tool_call", return_value="stub"):
            out = lm_studio.run_agent_loop(model="gemma", prompt="loop", max_iterations=3)
    assert "max_iterations" in out


def test_agent_loop_handles_multiple_tool_calls_per_round():
    two_calls = (
        '<tool_call>{"name":"web_fetch","arguments":{"url":"https://a.com"}}</tool_call>'
        '<tool_call>{"name":"web_fetch","arguments":{"url":"https://b.com"}}</tool_call>'
    )
    responses = [_mock_completion(two_calls), _mock_completion("synthesis")]
    with patch("urllib.request.urlopen", _mock_urlopen_sequence(responses)) as uo:
        with patch("livery.runtimes.lm_studio.execute_tool_call", return_value="stub") as mock_exec:
            out = lm_studio.run_agent_loop(model="gemma", prompt="fetch both")
    assert out == "synthesis"
    assert mock_exec.call_count == 2
    # Both tool_response blocks should appear in the follow-up user message.
    second_body = json.loads(uo.call_args_list[1].args[0].data.decode("utf-8"))
    user_msg = second_body["messages"][-1]["content"]
    assert user_msg.count("<tool_response") == 2
