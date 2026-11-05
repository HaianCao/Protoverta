from concurrent.futures import ThreadPoolExecutor

from protoverta.core import convert_response
from protoverta.response_context import restore_request_context


def test_snapshot_context_does_not_infer_usage_defaults_or_mutate_input():
    result = {"output": [], "parallel_tool_calls": False}
    request = {
        "tools": [{"type": "function", "name": "lookup"}],
        "parallel_tool_calls": True,
        "tool_choice": {"type": "function", "name": "lookup"},
        "max_output_tokens": 64,
    }
    output = restore_request_context(result, request)
    assert output["parallel_tool_calls"] is True
    assert output["tools"] == [{**request["tools"][0], "description": None, "parameters": None, "strict": None}]
    assert output["tool_choice"] == request["tool_choice"]
    assert output["store"] is False and output["background"] is False
    assert output["previous_response_id"] is None
    assert not {"usage", "temperature", "service_tier", "reasoning"} & output.keys()
    output["tools"].clear()
    assert request["tools"] and result == {"output": [], "parallel_tool_calls": False}


def test_context_is_per_request_across_concurrent_stream_snapshots():
    event = {"type": "response.created", "response": {"output": []}}

    def run(index):
        instructions = f"request-{index}"
        result = restore_request_context(event, {"instructions": instructions, "temperature": index / 100})
        assert result["response"]["instructions"] == instructions
        assert result["response"]["temperature"] == index / 100

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(run, range(64)))
    assert event == {"type": "response.created", "response": {"output": []}}


def test_non_snapshot_event_remains_unchanged():
    event = {"type": "response.output_text.delta", "delta": "hello"}
    assert restore_request_context(event, {"temperature": 0.3}) == event


def test_generated_json_envelope_reports_own_lifecycle_without_sampling_guesses():
    body = {"model": "m", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "hello"}}]}
    result = convert_response(body, "chat", "responses")
    assert type(result["completed_at"]) is int
    assert result["store"] is False and result["background"] is False
    assert result["previous_response_id"] is None
    assert result["instructions"] is None
    assert not {"usage", "temperature", "top_p", "service_tier"} & result.keys()
    body["choices"][0]["finish_reason"] = "length"
    assert convert_response(body, "chat", "responses")["completed_at"] is None
