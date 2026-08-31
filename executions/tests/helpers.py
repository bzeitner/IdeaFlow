from django.utils import timezone

from executions.models import (
    ApprovalStatus, ModelConfiguration, WorkflowDefinition, WorkflowVersion,
)
from executions.services import canonical_hash


def make_configuration():
    return ModelConfiguration.objects.create(
        provider="test",
        model_identifier="test-model",
        settings={"temperature": 0},
        content_hash=canonical_hash({"provider": "test", "model": "test-model"}),
    )


def make_workflow_version(key="research"):
    workflow, _ = WorkflowDefinition.objects.get_or_create(
        key=key, defaults={"name": key.title()}
    )
    configuration = {"output_schema": "v1"}
    return WorkflowVersion.objects.create(
        workflow=workflow,
        version=99,
        status=ApprovalStatus.APPROVED,
        configuration=configuration,
        content_hash=canonical_hash(configuration),
    )
