from executions.services import canonical_hash


def approved_excerpt(kind, value, actor="operator"):
    digest = canonical_hash(value)
    return {"kind": kind, "value": value, "hash": digest,
            "approval": {"policy": "evaluation-excerpt-v1", "approved_by": actor,
                         "value_hash": digest}}
