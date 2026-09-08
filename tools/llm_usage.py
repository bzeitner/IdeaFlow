#!/usr/bin/env python3
"""Normalize Claude/Codex CLI output into assistant text and ledger measurements."""

import argparse
import json
import os
from pathlib import Path


def _integer(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _usage(values):
    values = values or {}
    input_tokens = _integer(values.get("input_tokens"))
    output_tokens = _integer(values.get("output_tokens"))
    cached_tokens = _integer(
        values.get("cached_tokens", values.get("cached_input_tokens", values.get("cache_read_input_tokens")))
    )
    reasoning = values.get("reasoning_tokens")
    if reasoning is None and isinstance(values.get("output_tokens_details"), dict):
        reasoning = values["output_tokens_details"].get("thinking_tokens")
    reasoning_tokens = _integer(reasoning)
    total_tokens = _integer(values.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        # Cached input is a billed subset/category of input, not additional context.
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
    }


def parse_claude(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_usage = data.get("usage") or {}
    usage = _usage(raw_usage)
    cache_creation = _integer(raw_usage.get("cache_creation_input_tokens")) or 0
    cache_read = _integer(raw_usage.get("cache_read_input_tokens")) or 0
    usage["cached_tokens"] = cache_creation + cache_read
    if usage["input_tokens"] is not None and usage["output_tokens"] is not None:
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"] + usage["cached_tokens"]
    cost = data.get("total_cost_usd")
    cost_micros = round(float(cost) * 1_000_000) if cost is not None else None
    structured_output = data.get("structured_output")
    output = (
        json.dumps(structured_output, ensure_ascii=False)
        if structured_output is not None
        else str(data.get("result") or "")
    )
    return output, {
        **usage,
        "cost_micros": cost_micros,
        "cost_source": "provider_reported" if cost_micros is not None else "",
        "provider_request_id": str(data.get("session_id") or ""),
    }


def parse_codex(path, allocated_cost_micros=None):
    usage = {}
    messages = []
    thread_id = ""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("type") == "thread.started":
            thread_id = str(event.get("thread_id") or "")
        if event.get("type") == "turn.completed":
            usage = event.get("usage") or usage
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            messages.append(str(item.get("text") or ""))
    normalized = _usage(usage)
    if allocated_cost_micros is None:
        raw_cost = os.environ.get("IDEAFLOW_CODEX_COST_MICROS_PER_RUN", "")
        if not raw_cost.isdigit() or int(raw_cost) <= 0:
            raise ValueError(
                "IDEAFLOW_CODEX_COST_MICROS_PER_RUN must be a positive integer "
                "for subscription cost allocation."
            )
        allocated_cost_micros = int(raw_cost)
    return (messages[-1] if messages else ""), {
        **normalized,
        "cost_micros": allocated_cost_micros,
        "cost_source": "subscription_allocated",
        "provider_request_id": thread_id,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=("claude", "codex"))
    parser.add_argument("raw_file")
    parser.add_argument("output_file")
    parser.add_argument("measurement_file")
    args = parser.parse_args()
    text, measurement = (
        parse_claude(args.raw_file) if args.provider == "claude" else parse_codex(args.raw_file)
    )
    Path(args.output_file).write_text(text, encoding="utf-8")
    Path(args.measurement_file).write_text(json.dumps(measurement), encoding="utf-8")


if __name__ == "__main__":
    main()
