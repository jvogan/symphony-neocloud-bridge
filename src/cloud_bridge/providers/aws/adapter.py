"""AWS provider setup entry.

AWS compute is split by maturity. Batch/SageMaker are contract-only (provider
profiles, no code; SageMaker is not even profiled). EC2 and Batch are target
profiles that still require public smoke validation before execution. AWS is
also the bridge's orchestration glue: S3 egress and Secrets/STS refs are modeled
as reviewable plans, while ECR-auth refresh, SQS, DynamoDB, and EventBridge
backstops are rendered as command templates but not executed. See
cloud_bridge.aws_orchestration and docs/providers/aws.md.
"""
from __future__ import annotations

from typing import Any

from cloud_bridge.providers.base import PORTABLE_CONTRACT, ProviderAdapter
from cloud_bridge.providers.capabilities import AWS_DOCS


class AwsAdapter(ProviderAdapter):
    name = "aws"
    adapter_id = "aws_v1"
    automated_launch = False
    provenance = "setup guidance from public provider docs and bridge-safe patterns"
    summary = (
        "AWS is the bridge's artifact + secret glue. Modeled paths include S3 artifact egress "
        "(presigned PUT / aws s3 cp + SHA-256) and Secrets-Manager/STS refs. ECR-auth refresh, SQS, "
        "DynamoDB, and EventBridge backstops are RENDERED as command templates but never executed. "
        "AWS-as-compute (Batch/EC2/SageMaker) is contract-only and requires public smoke validation."
    )
    learnings_doc = "docs/providers/aws.md"
    roadmap = [
        "implement one public AWS-compute smoke first: Batch or EC2 with S3-checksum egress and CloudWatch polling",
        "generic AWS Batch job-definition + queue submit_job remains setup guidance until a public smoke validates it",
        "promote the existing aws-orchestrator-plan glue (rendered command templates) into a "
        "first-class executed adapter surface",
    ]
    known_patterns = [
        "S3 presigned PUT lets the worker egress artifacts with NO AWS creds in the pod; the validator "
        "HARD-ERRORS on a literal upload URL (presigned URLs are bearer creds) - only *_url_ref is allowed, fail-closed",
        "object_store_upload (multipart / sync-heavy egress) requires destination_uri[_ref] AND a "
        "credentials_ref - best is aws-sts: short-lived creds scoped to the run prefix",
        "EC2/Batch in-instance auth = IAM instance profile + IMDSv2 (no creds in the workload), which "
        "eliminates RunPod's stale-injected-key footgun; scope self-terminate with ec2:ResourceTag/project=<x> (proven)",
        "ECR-token -> RunPod registry-auth refresh is RENDERED by the bridge (aws ecr get-login-password | "
        "runpodctl registry create); tokens expire ~12h so refresh immediately pre-launch. AWS-compute private-ECR pulls are contract-only, never run",
        "AWS Budgets can lag immediately after a run; use CloudWatch and instance-type x runtime estimates "
        "during closeout, then reconcile billing after provider-side delay",
        "cost: Budgets lag (estimate from instance-type x runtime); for a real cap wire a Budgets "
        "APPLY_IAM_POLICY action that attaches an emergency Deny (ec2:RunInstances / batch:SubmitJob / cloudformation:CreateStack) - explicit Deny beats AdministratorAccess",
        "S3 STATUS/SUCCESS/FAILURE sidecar (aws s3 cp every ~30s) is the AWS status plane - no ?cb=$RANDOM "
        "needed (S3 strong read-after-write, GETs uncached); there is NO managed public proxy URL like *.proxy.runpod.net",
        "Secrets Manager refs resolved only by the trusted orchestrator, never inside the pod",
    ]

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "adapter": self.adapter_id,
            "automated_launch": False,
            "summary": self.summary,
            "learnings_doc": self.learnings_doc,
            "portable_contract": list(PORTABLE_CONTRACT),
            "known_patterns": self.known_patterns,
            "roadmap": self.roadmap,
            "compute_status": "contract_only",
            "orchestration_glue": {
                "executed_in_real_runs": ["s3_artifact_egress", "secrets_manager_sts_refs"],
                "rendered_command_templates_only": [
                    "ecr_auth_refresh",
                    "sqs_handoff",
                    "dynamodb_launch_lock",
                    "eventbridge_cleanup_backstop",
                ],
                "cli_command": "aws-orchestrator-plan",
                "docs": AWS_DOCS,
            },
        }
