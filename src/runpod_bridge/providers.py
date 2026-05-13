from __future__ import annotations

from typing import Any


RUNPOD_DOCS = {
    "api_overview": "https://docs.runpod.io/api-reference/overview",
    "agent_skills": "https://docs.runpod.io/get-started/agent-skills",
    "mcp_servers": "https://docs.runpod.io/get-started/mcp-servers",
    "runpodctl": "https://docs.runpod.io/runpodctl/overview",
    "runpodctl_pod": "https://docs.runpod.io/runpodctl/reference/runpodctl-pod",
    "runpodctl_ssh": "https://docs.runpod.io/runpodctl/reference/runpodctl-ssh",
    "runpodctl_billing": "https://docs.runpod.io/runpodctl/reference/runpodctl-billing",
    "runpodctl_registry": "https://docs.runpod.io/runpodctl/reference/runpodctl-registry",
    "create_pod": "https://docs.runpod.io/api-reference/pods/POST/pods",
    "list_pods": "https://docs.runpod.io/api-reference/pods/GET/pods",
    "serverless_cli": "https://docs.runpod.io/runpodctl/reference/runpodctl-serverless",
    "flash": "https://docs.runpod.io/flash/overview",
    "flash_create_endpoints": "https://docs.runpod.io/flash/create-endpoints",
    "flash_deploy": "https://docs.runpod.io/flash/apps/deploy-apps",
    "flash_cli": "https://docs.runpod.io/flash/cli/overview",
    "flash_parameters": "https://docs.runpod.io/flash/configuration/parameters",
    "ports": "https://docs.runpod.io/pods/configuration/expose-ports",
    "ssh": "https://docs.runpod.io/pods/configuration/use-ssh",
    "network_volumes": "https://docs.runpod.io/storage/network-volumes",
    "flash_storage": "https://docs.runpod.io/flash/configuration/storage",
    "network_volume_api": "https://docs.runpod.io/api-reference/network-volumes/POST/networkvolumes",
    "templates": "https://docs.runpod.io/api-reference/templates/POST/templates",
    "billing_pods": "https://docs.runpod.io/api-reference/billing/GET/billing/pods",
    "billing_endpoints": "https://docs.runpod.io/api-reference/billing/GET/billing/endpoints",
    "billing_network_volumes": "https://docs.runpod.io/api-reference/billing/GET/billing/networkvolumes",
    "billing_overview": "https://docs.runpod.io/accounts-billing/billing",
    "cost_centers": "https://docs.runpod.io/accounts-billing/cost-centers",
    "instant_clusters": "https://docs.runpod.io/instant-clusters",
    "s3_api": "https://docs.runpod.io/storage/s3-api",
    "api_keys": "https://docs.runpod.io/get-started/api-keys",
    "graphql_pods": "https://docs.runpod.io/sdks/graphql/manage-pods",
    "graphql_overview": "https://docs.runpod.io/sdks/graphql/configurations",
}


AWS_DOCS = {
    "s3_presigned_upload": "https://docs.aws.amazon.com/AmazonS3/latest/userguide/PresignedUrlUploadObject.html",
    "sts_assume_role": "https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html",
    "secrets_manager_cli": "https://docs.aws.amazon.com/secretsmanager/latest/userguide/retrieving-secrets_cli.html",
    "ecr_auth": "https://docs.aws.amazon.com/cli/latest/reference/ecr/get-login-password.html",
    "sqs_visibility_timeout": "https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html",
    "sqs_send_message": "https://docs.aws.amazon.com/cli/latest/reference/sqs/send-message.html",
    "eventbridge_scheduler": "https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-schedule.html",
    "eventbridge_create_schedule": "https://docs.aws.amazon.com/cli/latest/reference/scheduler/create-schedule.html",
    "dynamodb_condition_expressions": "https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Expressions.OperatorsAndFunctions.html",
    "dynamodb_put_item": "https://docs.aws.amazon.com/cli/latest/reference/dynamodb/put-item.html",
    "dynamodb_ttl": "https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/time-to-live-ttl-before-you-start.html",
}


def provider_capabilities(provider: str = "runpod") -> dict[str, Any]:
    if provider != "runpod":
        return {
            "provider": provider,
            "implemented": False,
            "adapter": f"{provider}_future_adapter",
            "portable_contract": [
                "validate-manifest",
                "prepare",
                "write-handoff",
                "validate-handoff",
                "egress-plan",
                "supervise",
                "dashboard",
            ],
        }

    return {
        "provider": "runpod",
        "implemented": True,
        "adapter": "runpod_pod_v1",
        "docs": RUNPOD_DOCS,
        "related_docs": {
            "aws": AWS_DOCS,
        },
        "resources": {
            "pods": {
                "lifecycle": ["create", "list", "get", "update", "start", "stop", "delete", "reset", "restart"],
                "bridge_commands": [
                    "run-remote",
                    "run-handoff",
                    "create-pod",
                    "list-pods",
                    "get-pod",
                    "runtime-metrics",
                    "cleanup-pod",
                    "productivity-plan",
                ],
                "optional_runpodctl_commands": ["render-runpodctl-create", "pod-ssh-info"],
                "runtime_backstop": "budget.terminate_after_minutes renders to runpodctl pod create --terminate-after",
            },
            "runtime_metrics": {
                "surface": "RunPod GraphQL pod.runtime",
                "bridge_commands": ["runtime-metrics", "productivity-plan"],
                "fields": [
                    "runtime.uptimeInSeconds",
                    "runtime.container.cpuPercent",
                    "runtime.container.memoryPercent",
                    "runtime.gpus[].gpuUtilPercent",
                    "runtime.gpus[].memoryUtilPercent",
                ],
                "notes": [
                    "use as a read-only crash-loop and idle-sample detector",
                    "a tiny uptime after long elapsed time or an uptime reset across samples indicates likely container restart/crash loop",
                    "single utilization samples do not prove useful workload progress or artifact success",
                ],
            },
            "serverless_endpoints": {
                "lifecycle": ["create", "list", "get", "update", "delete"],
                "bridge_commands": ["billing-endpoints"],
                "candidate_adapter": "runpod_serverless_v1",
                "notes": [
                    "managed by RunPod Serverless or Flash, not the current pod runner",
                    "queue endpoints support async jobs; load-balanced endpoints expose HTTP routes",
                    "endpoint success still requires job/output artifact validation and cleanup/undeploy proof",
                ],
            },
            "flash": {
                "status": "candidate_adapter",
                "candidate_adapter": "runpod_flash_v1",
                "local_tools": ["flash", "runpod_flash"],
                "patterns": [
                    "Python @Endpoint functions for remote GPU/CPU execution",
                    "Flash apps with isolated environments and multiple endpoint types",
                    "custom Docker images through Endpoint(image=...) when needed",
                    "network volumes mounted at /runpod-volume/ for persistent worker data",
                ],
                "python_versions": ["3.10", "3.11", "3.12", "3.13"],
                "bridge_fit": [
                    "best for bursty function, inference, and sharded Python workloads",
                    "less fit for arbitrary shell pipelines that require full pod lifecycle control",
                    "pin the Python minor version in the adapter contract before local validation and deploy",
                ],
            },
            "network_volumes": {
                "lifecycle": ["create", "list", "get", "update", "delete"],
                "notes": [
                    "Secure Cloud only for Pods",
                    "attach at pod deployment time",
                    "pod deletion preserves attached network volume data",
                    "multiple writers require application-level coordination",
                    "RunPod S3 API allows post-cleanup artifact pull from retained network volumes with separate rps_ S3 keys",
                ],
            },
            "templates": {
                "lifecycle": ["create", "list", "get", "update", "delete"],
                "notes": ["template fields overlap pod create fields"],
            },
            "aws_integrations": {
                "status": "optional_superpowers",
                "bridge_commands": ["aws-orchestrator-plan", "egress-plan", "render-startup", "prepare", "run-handoff"],
                "patterns": [
                    "aws_s3_presigned_upload for pod-to-S3 archive egress without AWS credentials inside the pod",
                    "object_store_upload with STS-scoped runtime-injected AWS credentials for multipart/sync-heavy workflows",
                    "RunPod network-volume S3 plus AWS CLI-compatible tooling for post-cleanup artifact pull",
                    "ECR registry auth refreshed by a trusted orchestrator before private image launches",
                    "SQS/DynamoDB/EventBridge as optional orchestrator-side queue, lock, and cleanup backstop surfaces",
                    "Secrets Manager refs resolved only by the trusted orchestrator",
                ],
            },
            "billing": {
                "endpoints": ["/billing/pods", "/billing/endpoints", "/billing/networkvolumes"],
                "bridge_commands": ["billing-pods", "billing-endpoints", "billing-network-volumes", "cost-report"],
                "optional_runpodctl_backend": [
                    "billing-pods --backend runpodctl",
                    "billing-endpoints --backend runpodctl",
                    "billing-network-volumes --backend runpodctl",
                ],
                "manifest_fields": ["billing.cost_center", "billing.project_code", "billing.resource_owner"],
                "notes": [
                    "cost centers are available in RunPod console for team/project spend tracking",
                    "bridge manifest billing fields preserve intended attribution for closeout even before provider-side assignment is automated",
                ],
            },
            "interruptible_pods": {
                "field": "runpod.interruptible",
                "policy": [
                    "require non-none workload.checkpoint_policy.mode before paid launch",
                    "require explicit stage-contract resume or rerun policy before paid launch",
                    "require durable artifact egress before paid launch",
                ],
            },
            "instant_clusters": {
                "status": "watchlist",
                "candidate_adapter": "runpod_cluster_v1",
                "notes": [
                    "managed multi-node clusters for distributed training or inference",
                    "intended for jobs that need 2-8 nodes or larger sales-assisted clusters",
                    "requires a separate stage contract for scheduler, rank, checkpoint, and teardown semantics",
                ],
            },
        },
        "ports": {
            "http_proxy": "https://<pod_id>-<internal_port>.proxy.runpod.net",
            "tcp": "pod publicIp plus portMappings",
            "symmetric_tcp": "pseudo ports above 70000 expose RUNPOD_TCP_PORT_<port> inside the pod",
                "warnings": [
                    "HTTP proxy is public and short-request oriented",
                    "artifact inspection ports are completion-only unless startup.progress also starts a live health endpoint",
                    "TCP external mappings can change after resets",
                    "UDP is not supported",
                ],
        },
        "ssh_scp": {
            "basic_proxy": "basic ssh.runpod.io access is not a full SCP/SFTP path",
            "full_scp_requires": ["supportPublicIp", "22/tcp", "sshd running in image", "SSH public key auth"],
        },
        "artifact_egress": {
            "smoke": ["workspace_archive", "http_proxy", "tcp"],
            "production": ["aws_s3_presigned_upload", "runpod_network_volume_s3", "network_volume_s3", "scp", "object_store_upload"],
        },
        "limitations": [
            "pod log streaming and in-pod exec are not part of the observed bridge surface",
            "Flash and generic Serverless endpoint lifecycle are documented opportunities, not implemented mutating adapters in this bridge yet",
            "Instant Clusters are a watchlist target and not implemented by the current adapter",
            "scoped RunPod API keys should be used when available, but programmatic per-run key provisioning is not implemented in this bridge",
            "REST pod create does not currently enforce budget.terminate_after_minutes; render-runpodctl-create exposes the runpodctl backstop path",
            "provider-side cost-center assignment is recorded as intended manifest metadata until a safe machine-readable assignment route is implemented",
            "actual GPU prices still come from live RunPod pod fields or billing records",
        ],
        "next_adapters": [
            {
                "name": "runpod_flash_v1",
                "priority": "high",
                "why": "maps small and bursty Python GPU/CPU functions to Serverless without pod startup scripting",
            },
            {
                "name": "runpod_serverless_v1",
                "priority": "high",
                "why": "adds endpoint create/job/status/delete around template or Hub-based Serverless workloads",
            },
            {
                "name": "runpod_cluster_v1",
                "priority": "later",
                "why": "covers huge distributed jobs that need multi-node networking and scheduler-aware monitoring",
            },
        ],
    }
