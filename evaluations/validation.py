"""Pure rubric/result validation and transparent diagnostic aggregation."""
from collections import Counter
import re
from pathlib import PurePosixPath

from django.core.exceptions import ValidationError
from executions.services import canonical_hash
from .security import validate_excerpt, validate_metadata

STATES = {"pass", "fail", "not_applicable", "insufficient_evidence"}
INPUT_KINDS = {
    "output", "objective", "source_evidence", "execution_evidence",
    "frozen_requirements", "output_contract", "run.finish_reason",
    "reference_observations",
}


def validate_input_names(names):
    if not isinstance(names, list) or not all(isinstance(x, str) and x in INPUT_KINDS for x in names):
        raise ValidationError("Required inputs must name supported evidence kinds.")
    if len(names) != len(set(names)):
        raise ValidationError("Required input kinds must be unique.")


def validate_evidence(manifest, actor_label):
    """Validate frozen evidence descriptors, not the truth of their claims.

    An empty observed source/event list is evidence of absence. An absent
    descriptor is unavailable evidence and cannot support a completed grade.
    Output bytes remain in protected storage; other inputs are redacted
    snapshots whose values and hashes must agree.
    """
    validate_metadata(manifest)
    allowed_manifest = {
        "schema_version", "evaluated_run", "output_hash", "evaluator_hash",
        "request_hash", "output_contract", "finish_reason", "reference_observations",
        "observed_at", "rubric_key", "workflow_version", "evidence",
    }
    if set(manifest) - allowed_manifest:
        raise ValidationError("Unknown evaluation manifest fields are not allowed.")
    evidence = manifest.get("evidence", {})
    if not isinstance(evidence, dict):
        raise ValidationError("Manifest evidence must be a reference map.")
    for ref, item in evidence.items():
        if not isinstance(ref, str) or not ref or not isinstance(item, dict):
            raise ValidationError("Evidence needs a named, typed descriptor.")
        kind = item.get("kind")
        digest = item.get("hash")
        if not isinstance(kind, str) or kind not in INPUT_KINDS:
            raise ValidationError("Evidence kind is missing or unsupported.")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValidationError("Evidence requires a SHA-256 content hash.")
        if kind == "output":
            if set(item) != {"kind", "hash", "source", "reference"}:
                raise ValidationError("Output evidence must contain metadata only, never inline content.")
            if digest != manifest.get("output_hash"):
                raise ValidationError("Output evidence does not match the evaluated output.")
            reference = item["reference"]
            if item["source"] == "operator_file":
                if reference != "":
                    raise ValidationError("Operator-file evidence retains only its hash, not its local path.")
            elif item["source"] == "execution_payload":
                if not isinstance(reference, str) or not reference.startswith("execution://") or len(reference) > 500:
                    raise ValidationError("Invalid protected output reference.")
                name = reference[len("execution://"):]
                if not name or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                    raise ValidationError("Invalid protected output reference.")
            else:
                raise ValidationError("Unknown output evidence source.")
            continue
        excerpt = kind in {"objective", "frozen_requirements", "source_evidence", "execution_evidence"}
        fields = {"kind", "hash", "value", "approval"} if excerpt else {"kind", "hash", "value"}
        if set(item) != fields:
            raise ValidationError("Evidence descriptor fields do not match its kind.")
        if "value" not in item or canonical_hash(item["value"]) != digest:
            raise ValidationError("Evidence snapshot is absent or does not match its hash.")
        if excerpt:
            validate_excerpt(item, actor_label)
        value = item["value"]
        if kind == "objective" and (not isinstance(value, str) or not value.strip()):
            raise ValidationError("Objective evidence must contain the frozen objective.")
        if kind == "frozen_requirements" and (not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value)):
            raise ValidationError("Frozen requirements must be a list of statements.")
        if kind in {"source_evidence", "execution_evidence", "reference_observations"} and (not isinstance(value, list) or not all(isinstance(x, dict) for x in value)):
            raise ValidationError("Source, execution, and reference evidence require structured observations.")
        if kind == "output_contract":
            from .deterministic import validate_contract
            validate_contract(value)
            if value != manifest.get("output_contract"):
                raise ValidationError("Contract evidence differs from the frozen contract.")
        if kind == "run.finish_reason" and (not isinstance(value, str) or value != manifest.get("finish_reason")):
            raise ValidationError("Completion evidence differs from the frozen run metadata.")
        if kind == "reference_observations" and value != manifest.get("reference_observations"):
            raise ValidationError("Reference evidence differs from the frozen observations.")
    return evidence


def validate_rubric(rubric):
    if not isinstance(rubric, dict) or rubric.get("schema_version") != 1:
        raise ValidationError("Unsupported rubric schema.")
    criteria = rubric.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ValidationError("A rubric requires criteria.")
    ids = []
    for item in criteria:
        if not isinstance(item, dict):
            raise ValidationError("Criterion must be an object.")
        for key in ("id", "dimension", "description", "applicability", "method", "severity", "pass_example", "fail_example"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValidationError(f"Criterion requires {key}.")
        if item["severity"] not in {"ordinary", "critical", "optional"}:
            raise ValidationError("Unknown criterion severity.")
        if item["method"] not in {"deterministic", "human", "model"}:
            raise ValidationError("Unknown criterion method.")
        validate_input_names(item.get("required_inputs"))
        ids.append(item["id"])
    if len(ids) != len(set(ids)):
        raise ValidationError("Criterion IDs must be unique.")


def validate_results(rubric, results, manifest, required_inputs=None, *, actor_label=""):
    validate_rubric(rubric)
    required_inputs = [] if required_inputs is None else required_inputs
    validate_input_names(required_inputs)
    criteria = {c["id"]: c for c in rubric["criteria"]}
    expected = set(criteria)
    if not isinstance(results, list) or len(results) != len(expected):
        raise ValidationError("Every criterion must have exactly one result.")
    seen = set()
    allowed = validate_evidence(manifest, actor_label)
    for row in results:
        if not isinstance(row, dict):
            raise ValidationError("Criterion result must be an object.")
        if set(row) != {"id", "status", "reason", "evidence_refs"}:
            raise ValidationError("Unknown criterion result fields are not allowed.")
        cid = row.get("id")
        if not isinstance(cid, str) or cid not in expected or cid in seen:
            raise ValidationError("Unknown or duplicate criterion ID.")
        seen.add(cid)
        if not isinstance(row.get("status"), str) or row["status"] not in STATES or not isinstance(row.get("reason"), str) or not row["reason"].strip():
            raise ValidationError("Result needs a valid state and concise reason.")
        refs = row.get("evidence_refs")
        if not isinstance(refs, list) or not all(isinstance(ref, str) and ref in allowed for ref in refs):
            raise ValidationError("Unknown evidence reference.")
        if row["status"] in {"pass", "fail"} and not refs:
            raise ValidationError("Completed judgments require evidence references.")
        if row["status"] in {"pass", "fail"}:
            required = set(required_inputs) | set(criteria[cid]["required_inputs"])
            supplied = {allowed[ref]["kind"] for ref in refs}
            if required - supplied:
                raise ValidationError(
                    f"Criterion {cid} lacks required evidence: {', '.join(sorted(required - supplied))}. "
                    "Use insufficient_evidence when inputs are unavailable."
                )


def summarize(rubric, results):
    by_id = {row["id"]: row for row in results}
    dimensions = {}
    for criterion in rubric["criteria"]:
        dimensions.setdefault(criterion["dimension"], []).append(by_id[criterion["id"]])

    def counts(rows):
        totals = Counter(row["status"] for row in rows)
        completed = totals["pass"] + totals["fail"]
        applicable = len(rows) - totals["not_applicable"]
        return {
            "expected": len(rows), "applicable_or_unknown": applicable,
            **{key: totals[key] for key in sorted(STATES)},
            "pass_rate": totals["pass"] / completed if completed else None,
            "judgment_coverage": completed / applicable if applicable else None,
        }

    return {
        "counts": counts([by_id[c["id"]] for c in rubric["criteria"] if c["severity"] != "optional"]),
        "dimensions": {key: counts(rows) for key, rows in sorted(dimensions.items())},
        "critical_failures": [c["id"] for c in rubric["criteria"] if c["severity"] == "critical" and by_id[c["id"]]["status"] == "fail"],
        # A1/A2 is diagnostic. Calibration and decision-use approval are later.
        "decision_grade": False,
    }
