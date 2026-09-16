"""Repository-owned, uncalibrated R5A v1 definitions; never calls a provider."""
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import EvaluatorDefinition, EvaluatorVersion, MetricDefinition

WORKFLOWS = ["research", "review"]
ANCHORS = {
    "1": "Repeats known information, misses the objective, or adds no supported conclusion.",
    "2": "Adds a potentially useful observation but does not close a meaningful gap or change a decision.",
    "3": "Resolves part of the objective or materially narrows the alternatives with usable evidence.",
    "4": "Supports a defensible decision with only a small, explicitly identified uncertainty remaining.",
    "5": "Fully answers the scoped objective with sufficient evidence, addresses material counterarguments, and leaves no decision-relevant gap.",
}


def criterion(cid, dimension, description, *, method="human", severity="ordinary", inputs=None,
              applicability="Applies to the frozen research objective.", good, bad):
    return {"id": cid, "dimension": dimension, "description": description,
            "method": method, "severity": severity, "required_inputs": inputs or ["output", "objective", "source_evidence"],
            "applicability": applicability, "pass_example": good, "fail_example": bad}


QUALITY = [
    criterion("requirements.explicit", "explicit_requirements", "Addresses the requirements frozen before grading.", good="Answers the requested comparison.", bad="Answers a different question."),
    criterion("requirements.context", "implicit_requirements", "Meets contextual requirements declared before grading.", inputs=["output", "frozen_requirements", "execution_evidence"], good="Recorded inspection covers the required prior findings.", bad="Required prior findings were demonstrably ignored."),
    criterion("synthesis.judgment", "synthesis", "Connects evidence to the conclusion, separating assumptions from supported facts.", good="Explains how two findings narrow the alternatives.", bad="Lists sources without explaining their implications."),
    criterion("evidence.support", "evidence_references", "Material factual claims have traceable supporting evidence.", good="An internal experiment or source passage supports the claim.", bad="A cited passage does not support the material claim."),
    criterion("evidence.fabrication", "evidence_references", "Does not fabricate material evidence or tool results.", severity="critical", inputs=["output", "source_evidence", "execution_evidence"], good="Quoted results match recorded observations.", bad="A claimed experiment result contradicts the execution record."),
    criterion("communication.usability", "communication", "Communicates at the length and structure needed for the decision.", severity="optional", good="A concise finding resolves the question.", bad="Formatting obscures the conclusion."),
    criterion("instructions.format", "instruction_following", "Follows the frozen output and disposition requirements.", good="Includes each required section.", bad="Omits a required disposition."),
    criterion("instructions.approval", "instruction_following", "Claims of approval or authorized actions agree with the audit record.", severity="critical", inputs=["output", "execution_evidence"], applicability="Applies when an approval or authorized action is claimed or required.", good="Approval claim matches a recorded decision.", bad="Claims approval when the audit record shows rejection."),
]

DETERMINISTIC = [
    criterion("output.nonempty", "explicit_requirements", "Output contains non-whitespace content.", method="deterministic", inputs=["output"], good="Nonempty report.", bad="Whitespace only."),
    criterion("output.schema", "instruction_following", "Output matches the declared flat JSON-object type contract.", method="deterministic", inputs=["output", "output_contract"], applicability="JSON-object outputs with an explicit type contract; text is not applicable.", good="Object fields have declared types.", bad="Array supplied where object required."),
    criterion("output.required_fields", "explicit_requirements", "Declared required fields are present and nonempty.", method="deterministic", inputs=["output", "output_contract"], applicability="Outputs with declared required fields.", good="Required answer field is populated.", bad="Required answer field is absent or empty."),
    criterion("output.truncation", "instruction_following", "Provider terminal metadata does not indicate length truncation.", method="deterministic", inputs=["run.finish_reason"], good="Provider reports stop.", bad="Provider reports max_tokens."),
    criterion("references.internal", "evidence_references", "Declared internal references existed in the same idea when checked.", method="deterministic", inputs=["reference_observations"], applicability="A nonempty frozen set of internal references is declared.", good="Referenced research entry exists in the target idea.", bad="Referenced artifact is absent or belongs to another idea."),
]


def _frozen(model, lookup, values):
    existing = model.objects.filter(**lookup).first()
    candidate = model(**lookup, **values)
    if existing:
        if existing.content_hash != candidate.fingerprint():
            raise ValidationError(f"Seed drift for {model.__name__}: {lookup}; publish a new version.")
        return existing
    candidate.save()
    return candidate


@transaction.atomic
def seed_evaluators():
    created = []
    for key, unit, method, criteria, implementation, description in [
        ("research.answer_progress", "ordinal", "human", [criterion("progress.objective", "progress", "Assign the independent objective-progress score using the frozen anchors.", good="A concise supported result closes the objective.", bad="A polished report repeats only known findings.")], "research-progress-v1", "Progress toward the scoped research objective, independent of quality compliance."),
        ("research.quality", "diagnostic", "human", QUALITY, "research-quality-v1", "Six-dimensional research-quality diagnostics; not objective progress."),
        ("research.structure", "diagnostic", "deterministic", DETERMINISTIC, "research-structure-v1", "Deterministic structural diagnostics; no semantic quality or progress inference."),
    ]:
        metric = _frozen(MetricDefinition, {"key": key, "version": 1}, {
            "description": description, "unit": unit,
            "direction": "higher" if unit == "ordinal" else "diagnostic",
            "minimum": 1.0 if unit == "ordinal" else None,
            "maximum": 5.0 if unit == "ordinal" else None,
            "aggregation": "ordinal" if unit == "ordinal" else "per_dimension",
            "applicability": {"idea_types": ["research"]}, "actor_label": "r5a-seed-v1",
        })
        definition, _ = EvaluatorDefinition.objects.get_or_create(key=key, defaults={"name": key, "description": description})
        rubric = {"schema_version": 1, "criteria": criteria, "calibration": "not_calibrated"}
        if unit == "ordinal":
            rubric["anchors"] = ANCHORS
        created.append(_frozen(EvaluatorVersion, {"evaluator": definition, "version": 1}, {
            "metric": metric, "method": method, "implementation": implementation,
            "rubric": rubric, "applicability": {"workflows": WORKFLOWS, "rubric_key": "research"},
            "required_inputs": ["output"],
            "aggregation": {"strategy": "ordinal" if unit == "ordinal" else "diagnostic", "critical_failure_blocks": True},
            "actor_label": "r5a-seed-v1",
        }))
    return created
