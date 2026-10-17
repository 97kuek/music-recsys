"""モデルアダプタから渡された実測値だけを集計する。価格推定やAPI呼出しは行わない。"""


def summarize_usage(responses):
    responses = tuple(responses)
    fields = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "api_cost": "api_cost_usd",
        "llm_calls": "llm_calls",
        "tool_calls": "tool_calls",
        "peak_memory": "peak_memory_bytes",
    }
    metrics, measured = {}, {}
    for metric, attribute in fields.items():
        values = [
            getattr(response.usage, attribute)
            for response in responses
            if response.usage is not None and getattr(response.usage, attribute) is not None
        ]
        measured[metric] = len(values)
        metrics[metric] = (
            (max(values) if metric == "peak_memory" else sum(values))
            if responses and len(values) == len(responses)
            else None
        )
    return {
        "metrics": metrics,
        "measured_requests": measured,
        "n_generation_requests": len(responses),
        "currency": "USD",
        "peak_memory_unit": "bytes",
        "policy": "total requires every generation request to be measured; peak is max",
    }
