"""Pure checks against frozen bytes and timestamped observations; no network calls.

The output contract is a deliberately bounded flat object contract, not a
partial implementation presented as full JSON Schema.
"""
import json
import math

from django.core.exceptions import ValidationError

TYPES = {"string", "number", "integer", "boolean", "object", "array", "null"}


def validate_contract(contract):
    if not isinstance(contract, dict) or set(contract) - {"format", "fields", "required"}:
        raise ValidationError("Unknown output contract fields.")
    if contract.get("format") not in {"text", "json_object"}:
        raise ValidationError("Output format must be text or json_object.")
    fields = contract.get("fields", {})
    required = contract.get("required", [])
    if not isinstance(fields, dict) or not all(isinstance(k, str) and isinstance(v, str) and v in TYPES for k, v in fields.items()):
        raise ValidationError("Fields must map names to supported flat JSON types.")
    if not isinstance(required, list) or not all(isinstance(k, str) and k in fields for k in required) or len(required) != len(set(required)):
        raise ValidationError("Required fields must be unique declared field names.")
    if contract["format"] == "text" and (fields or required):
        raise ValidationError("Text cannot declare JSON fields.")


def _matches(value, kind):
    if kind == "number":
        return type(value) in (int, float) and (not isinstance(value, float) or math.isfinite(value))
    return {"string": str, "integer": int, "boolean": bool, "object": dict, "array": list, "null": type(None)}[kind] is type(value)


def evaluate(output, manifest):
    contract = manifest["output_contract"]
    validate_contract(contract)
    rows = []

    def add(cid, status, reason, *refs):
        if status in {"pass", "fail"} and "output" not in refs:
            refs = (*refs, "output")
        rows.append({"id": cid, "status": status, "reason": reason, "evidence_refs": list(refs)})

    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("Research output must be UTF-8 text.") from exc
    add("output.nonempty", "pass" if text.strip() else "fail", "Checked output for non-whitespace content.", "output")
    if contract["format"] == "text":
        add("output.schema", "not_applicable", "The frozen contract declares free text.", "contract")
        add("output.required_fields", "not_applicable", "No structured fields apply to free text.", "contract")
    else:
        try:
            def reject_constant(value):
                raise ValueError(value)
            def unique_pairs(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate JSON key")
                    result[key] = value
                return result
            parsed = json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_pairs)
            valid = isinstance(parsed, dict) and all(_matches(parsed[k], kind) for k, kind in contract.get("fields", {}).items() if k in parsed)
        except (ValueError, RecursionError):
            parsed, valid = None, False
        add("output.schema", "pass" if valid else "fail", "Checked JSON object shape and declared field types.", "output", "contract")
        required = contract.get("required", [])
        if not required:
            add("output.required_fields", "not_applicable", "No required fields were declared.", "contract")
        else:
            def populated(value):
                return value is not None and (bool(value.strip()) if isinstance(value, str) else value != [] and value != {})
            complete = isinstance(parsed, dict) and all(k in parsed and populated(parsed[k]) for k in required)
            add("output.required_fields", "pass" if complete else "fail", "Checked required fields for presence and nonempty content.", "output", "contract")
    finish = manifest["finish_reason"]
    if finish in {"length", "max_tokens", "max_output_tokens", "MAX_TOKENS"}:
        status, reason = "fail", "Provider reports output-length truncation."
    elif finish in {"stop", "end_turn", "completed", "STOP"}:
        status, reason = "pass", "Provider reports ordinary completion; semantic completeness is not inferred."
    else:
        status, reason = "insufficient_evidence", "No recognized provider completion reason; punctuation is not proof of completeness."
    add("output.truncation", status, reason, "run")
    observations = manifest["reference_observations"]
    if observations:
        add("references.internal", "pass" if all(x["valid"] for x in observations) else "fail", "Checked declared internal references against the target idea at the recorded time.", "references")
    else:
        add("references.internal", "not_applicable", "No internal references declared; external citation support was not evaluated.", "references")
    return rows
