#!/usr/bin/env python3
"""Backfill open questions through a deployed IdeaFlow's HTTP API."""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
import subprocess
import tempfile
from pathlib import Path

try:
    from tools.llm_pricing import estimate_openai_cost_micros
except ModuleNotFoundError:
    from llm_pricing import estimate_openai_cost_micros

DEFAULT_BASE = "https://ideaflow.bitesoftheweek.com"
MAX_REPORT_CHARS = 4000
CLIENT = Path(__file__).resolve().parents[1] / "tools" / "ideaflow"


def load_dotenv(path):
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"").strip("'")
        if key:
            os.environ.setdefault(key, value)


def request_json(url, *, token, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("User-Agent", "IdeaFlow-Open-Question-Backfill/1.0")
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:1000]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc


def client_json(*args):
    return json.loads(subprocess.check_output([str(CLIENT), *args], text=True))


def markdown_questions(context):
    questions = []
    in_section = False
    for line in (context or "").splitlines():
        if re.match(r"^\s{0,3}#{1,6}\s+open questions?\s*:?\s*$", line, re.I):
            in_section = True
            continue
        if in_section and re.match(r"^\s{0,3}#{1,6}\s+", line):
            break
        if not in_section:
            continue
        match = re.match(r"^\s*(?:[-*+] |\d+[.)] )(.*\S)\s*$", line)
        if match:
            question = match.group(1).strip()
            if question.lower().rstrip(".") not in {"none", "n/a"}:
                questions.append(question)
    return questions


def ai_questions(entries, *, api_key, api_base, model, prompt_template):
    # Validate policy before incurring provider cost.
    estimate_openai_cost_micros(model, {})
    reports = "\n\n".join(
        f"ENTRY {entry['id']}\nTopic: {entry['topic']}\n{entry['context'][:MAX_REPORT_CHARS]}"
        for entry in entries
    )
    prompt = prompt_template.format(reports=reports)
    body = {
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "You conservatively extract questions requiring human input."},
                {"role": "user", "content": prompt},
            ],
        }
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as prompt_file:
        json.dump(body, prompt_file)
        prompt_file.flush()
        trace = client_json(
            "trace-start", "--workflow", "open_question_extraction",
            "--trigger", "management_command",
        )
        run = client_json(
            "run-start", "--trace-id", trace["id"], "--provider", "openai-compatible",
            "--model", model, "--purpose", "extraction",
            "--prompt-key", "open-question-batch", "--input-file", prompt_file.name,
        )
    try:
        data = request_json(
            f"{api_base.rstrip('/')}/chat/completions", token=api_key,
            method="POST", body=body,
        )
    except Exception as exc:
        client_json(
            "run-fail", "--run-id", run["id"], "--error-class", type(exc).__name__,
            "--error-detail", str(exc), "--measurement-unavailable-reason", "provider_request_failed",
        )
        client_json("trace-fail", "--trace-id", trace["id"], "--reason", str(exc))
        raise
    usage_data = data.get("usage") or {}
    usage = {
        "input_tokens": usage_data.get("prompt_tokens"),
        "output_tokens": usage_data.get("completion_tokens"),
    }
    parse_error = None
    try:
        content = json.loads(data["choices"][0]["message"]["content"])
    except Exception as exc:
        content = None
        parse_error = exc
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as output_file:
        json.dump(data, output_file)
        output_file.flush()
        complete_args = [
            "run-complete", "--run-id", run["id"], "--output-file", output_file.name,
            "--measurement-status", "complete", "--cost-micros", str(estimate_openai_cost_micros(model, usage)),
            "--cost-source", "price_table",
        ]
        for key, flag in (("prompt_tokens", "--input-tokens"), ("completion_tokens", "--output-tokens"), ("total_tokens", "--total-tokens")):
            if usage_data.get(key) is not None:
                complete_args.extend([flag, str(usage_data[key])])
        if data.get("id"):
            complete_args.extend(["--provider-request-id", str(data["id"])])
        client_json(*complete_args)
    if parse_error is not None:
        client_json("trace-fail", "--trace-id", trace["id"], "--reason", str(parse_error))
        raise parse_error
    client_json("trace-complete", "--trace-id", trace["id"])
    result = {}
    for item in content.get("entries", []):
        questions = []
        for candidate in item.get("questions", []):
            try:
                confidence = float(candidate.get("confidence", 0))
            except (TypeError, ValueError):
                continue
            question = str(candidate.get("question", "")).strip()
            if confidence >= 0.8 and question and question not in questions:
                questions.append(question)
        result[int(item["entry_id"])] = questions
    return result


def chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idea", type=int, help="Only inspect one idea ID.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum research entries.")
    parser.add_argument("--all", action="store_true", help="Also inspect entries already containing questions.")
    parser.add_argument("--use-ai", action="store_true", help="Analyze prose when Markdown extraction finds nothing.")
    parser.add_argument("--batch-size", type=int, default=5, help="Reports per semantic API request (1-10).")
    parser.add_argument("--apply", action="store_true", help="Write findings; without this flag the run is a preview.")
    args = parser.parse_args()
    if args.limit < 1 or not 1 <= args.batch_size <= 10:
        parser.error("--limit must be positive and --batch-size must be between 1 and 10")

    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")
    base = os.environ.get("IDEAFLOW_API_BASE", DEFAULT_BASE).rstrip("/")
    token = os.environ.get("IDEAFLOW_API_TOKEN", "").strip()
    if not token:
        sys.exit("error: IDEAFLOW_API_TOKEN is not set (environment or repository .env).")

    summaries = request_json(f"{base}/api/ideas/", token=token)["ideas"]
    summaries = [idea for idea in summaries if idea.get("status") != "archived"]
    if args.idea:
        summaries = [idea for idea in summaries if idea["id"] == args.idea]
        if not summaries:
            sys.exit(f"error: idea {args.idea} was not returned by the server.")

    candidates = []
    for summary in summaries:
        detail = request_json(f"{base}/api/ideas/{summary['id']}/", token=token)
        for entry in detail.get("research_entries", []):
            if not entry.get("context") or (entry.get("open_questions") and not args.all):
                continue
            candidates.append({**entry, "idea_id": summary["id"]})
            if len(candidates) >= args.limit:
                break
        if len(candidates) >= args.limit:
            break

    findings = {entry["id"]: markdown_questions(entry["context"]) for entry in candidates}
    ambiguous = [entry for entry in candidates if not findings[entry["id"]]]
    if args.use_ai and ambiguous:
        api_key = os.environ.get("IDEAFLOW_SEMANTIC_API_KEY", "").strip()
        if not api_key:
            sys.exit("error: IDEAFLOW_SEMANTIC_API_KEY is required with --use-ai.")
        if not os.environ.get("IDEAFLOW_EXECUTION_API_TOKEN", "").strip():
            sys.exit("error: IDEAFLOW_EXECUTION_API_TOKEN is required with --use-ai.")
        api_base = os.environ.get("IDEAFLOW_SEMANTIC_API_BASE", "https://api.openai.com/v1")
        model = os.environ.get("IDEAFLOW_SEMANTIC_CLASSIFIER_MODEL", "gpt-4.1-mini")
        prompt_template = request_json(
            f"{base}/api/prompts/open-question-batch/", token=token
        )["content"]
        for batch in chunks(ambiguous, args.batch_size):
            findings.update(
                ai_questions(
                    batch,
                    api_key=api_key,
                    api_base=api_base,
                    model=model,
                    prompt_template=prompt_template,
                )
            )

    found = updated = 0
    by_id = {entry["id"]: entry for entry in candidates}
    for entry_id, questions in findings.items():
        if not questions:
            continue
        entry = by_id[entry_id]
        found += len(questions)
        print(f"Entry {entry_id} (idea {entry['idea_id']}): " + " | ".join(questions))
        if args.apply:
            request_json(
                f"{base}/api/ideas/{entry['idea_id']}/research/{entry_id}/open-questions/",
                token=token,
                method="POST",
                body={"questions": questions},
            )
            updated += 1
    mode = "Applied" if args.apply else "Preview"
    print(f"{mode}: inspected {len(candidates)}; found {found} question(s); updated {updated} entries.")


if __name__ == "__main__":
    main()
