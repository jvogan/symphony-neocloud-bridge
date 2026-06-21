from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from .aws_orchestration import build_aws_orchestrator_plan
from .closeout import write_closeout_files
from .contract import contract_self_check
from .cost import cost_report_from_record
from .dashboard import scan_dashboard_records, write_dashboard
from .doctor import run_doctor
from .egress import build_egress_plan
from .handoff import (
    load_provider_handoff,
    run_handoff_flow,
    validate_provider_handoff,
    write_provider_handoff,
)
from .learnings import (
    CATEGORIES,
    SEVERITIES,
    STATUSES,
    build_research_brief,
    corrupt_lines as learning_corrupt_lines,
    ledger_safety_warning,
    mark_promoted,
    promotion_bullet,
    promotion_candidates,
    read_entries,
    record_learning,
    search as search_learnings,
    provider_entry_file,
    stats as learning_stats,
)
from .linear_issue import validate_issue_file
from .linear_api import LinearApiError, LinearClient, write_issue_markdown
from .local_run import run_local
from . import __version__
from .manifest import ManifestError, build_plan, dumps_pretty, load_manifest, validate_manifest
from .manifest_audit import audit_manifest_tree
from .monitor import inspect_execution
from .orchestrator import issue_intake, run_orchestrator_once, scan_handoffs
from .packet import prepare_packet
from .preflight import analyze_preflight
from .productivity import build_productivity_plan
from .progress_report import build_progress_report
from .profiles import get_profile, list_profiles, recommend_profile
from .providers import ProviderLaunchUnsupported, available_adapters, get_adapter, provider_capabilities
from .public_readiness import run_public_audit
from .proxy import fetch_proxy_file, fetch_tcp_file, tcp_endpoint_from_pod, verify_proxy_packet, verify_tcp_packet
from .registry_auth import build_registry_auth_plan
from .recovery import analyze_recovery, recover_run
from .remote_run import run_remote_flow
from .providers.huggingface.jobs import run_job_flow
from .providers.huggingface.rest import HfJobsError
from .remote_outcome import write_remote_outcome
from .providers.runpod.rest import (
    RunpodRestClient,
    RunpodRestError,
    cleanup_pod_flow,
    create_pod_flow,
    summarize_pod,
)
from .util import redact
from .providers.runpod.catalog import build_gpu_catalog_report, build_gpu_catalog_report_from_manifest
from .providers.runpod.runtime import RunpodGraphqlError, build_runtime_metrics_report, load_previous_report
from .providers.runpod.s3_verify import verify_network_volume_s3
from .providers.runpod.ops_audit import audit_runpod_ops_tree
from .providers.runpod.ctl import (
    RunpodCtlError,
    billing_network_volume as runpodctl_billing_network_volume,
    billing_pods as runpodctl_billing_pods,
    billing_serverless as runpodctl_billing_serverless,
    build_pod_create_command,
    shell_join,
    ssh_info as runpodctl_ssh_info,
)
from .source_check import check_source_reachability
from .source_ingress import build_source_ingress_plan
from .startup import render_startup_script
from .supervisor import supervise_execution


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ProviderLaunchUnsupported as exc:
        print(f"error: provider launch unsupported: {exc}", file=sys.stderr)
        return 2


def _run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cloud-bridge")
    parser.add_argument("--version", action="version", version=f"cloud-bridge {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate-manifest", help="Validate a launch manifest without touching RunPod")
    validate_parser.add_argument("manifest")
    validate_parser.add_argument("--json", action="store_true", dest="as_json")

    plan_parser = subparsers.add_parser("plan", help="Print a dry-run execution plan")
    plan_parser.add_argument("manifest")
    plan_parser.add_argument("--json", action="store_true", dest="as_json")

    render_parser = subparsers.add_parser("render-startup", help="Render a startup script from a manifest")
    render_parser.add_argument("manifest")
    render_parser.add_argument("--out", help="Output path. Defaults to stdout.")

    prepare_parser = subparsers.add_parser("prepare", help="Write a launch packet without touching RunPod")
    prepare_parser.add_argument("manifest")
    prepare_parser.add_argument("--out-dir", default=".runtime/runpod-packet", help="Directory for launch_manifest, startup, and local_preflight")
    prepare_parser.add_argument("--source-dir", help="Directory to package when repo.source is local_snapshot or prepared_snapshot")
    prepare_parser.add_argument("--source-archive-pod-path", default="", help="Pod path for the prepared source archive when staging through an attached RunPod network volume")
    prepare_parser.add_argument("--json", action="store_true", dest="as_json")

    handoff_parser = subparsers.add_parser("write-handoff", help="Write a provider handoff for orchestrator-side RunPod execution")
    handoff_parser.add_argument("manifest")
    handoff_parser.add_argument("--out", default=".runtime/provider_handoff.json")
    handoff_parser.add_argument("--reason", default="worker_network_unreachable")
    handoff_parser.add_argument("--worker-id", default="")
    handoff_parser.add_argument("--local-preflight")
    handoff_parser.add_argument("--startup")
    handoff_parser.add_argument("--source-archive")
    handoff_parser.add_argument("--source-archive-manifest")
    handoff_parser.add_argument("--verification-mode", choices=("auto", "tcp", "proxy", "none"), default="auto")
    handoff_parser.add_argument("--port", type=int, default=8000)
    handoff_parser.add_argument("--timeout-seconds", type=int, default=180)
    handoff_parser.add_argument("--interval-seconds", type=int, default=5)
    handoff_parser.add_argument("--cleanup-action", choices=("stop", "delete"), default="delete")
    handoff_parser.add_argument("--json", action="store_true", dest="as_json")

    validate_handoff_parser = subparsers.add_parser("validate-handoff", help="Validate a provider handoff and referenced manifest")
    validate_handoff_parser.add_argument("handoff")
    validate_handoff_parser.add_argument("--json", action="store_true", dest="as_json")

    preflight_parser = subparsers.add_parser("preflight", help="Run higher-level launch, profile, provider, and egress checks")
    preflight_parser.add_argument("manifest")
    preflight_parser.add_argument("--json", action="store_true", dest="as_json")

    contract_parser = subparsers.add_parser("contract-self-check", help="Check stage contract, artifact proof, monitoring truth, and claim boundaries")
    contract_parser.add_argument("manifest")
    contract_parser.add_argument("--json", action="store_true", dest="as_json")

    egress_parser = subparsers.add_parser("egress-plan", help="Render durable artifact egress requirements and commands")
    egress_parser.add_argument("manifest")
    egress_parser.add_argument("--json", action="store_true", dest="as_json")

    productivity_parser = subparsers.add_parser("productivity-plan", help="Render live progress and peek-channel checks for a pod workload")
    productivity_parser.add_argument("manifest")
    productivity_parser.add_argument("--pod-id", default="POD_ID")
    productivity_parser.add_argument("--public-ip", default="POD_PUBLIC_IP")
    productivity_parser.add_argument("--external-port", type=int)
    productivity_parser.add_argument("--json", action="store_true", dest="as_json")

    source_parser = subparsers.add_parser("source-check", help="Check git source/ref reachability before paid launch")
    source_parser.add_argument("manifest")
    source_parser.add_argument("--execute", action="store_true", help="Run git network checks. Without this, print the command plan only.")
    source_parser.add_argument("--timeout-seconds", type=int, default=90)
    source_parser.add_argument("--json", action="store_true", dest="as_json")

    source_ingress_parser = subparsers.add_parser("source-ingress-plan", help="Render private source ingress options for git, archive URL, or RunPod network-volume S3 snapshots")
    source_ingress_parser.add_argument("manifest")
    source_ingress_parser.add_argument("--source-archive", help="Local source archive path to stage when repo.snapshot.archive_pod_path is used")
    source_ingress_parser.add_argument("--json", action="store_true", dest="as_json")

    registry_auth_parser = subparsers.add_parser("registry-auth-plan", help="Render provider-side private image registry auth and image-pull canary checks")
    registry_auth_parser.add_argument("manifest")
    registry_auth_parser.add_argument("--json", action="store_true", dest="as_json")

    aws_parser = subparsers.add_parser("aws-orchestrator-plan", help="Render optional AWS companion commands for RunPod orchestration")
    aws_parser.add_argument("manifest")
    aws_parser.add_argument("--handoff", default="provider_handoff.json", help="Provider handoff path used in SQS send-message commands")
    aws_parser.add_argument("--out-dir", help="Write helper JSON files for STS, SQS, DynamoDB, and EventBridge command templates")
    aws_parser.add_argument("--json", action="store_true", dest="as_json")

    profiles_parser = subparsers.add_parser("profiles", help="List or inspect built-in compute profiles")
    profiles_parser.add_argument("--name")
    profiles_parser.add_argument("--recommend-for")
    profiles_parser.add_argument("--json", action="store_true", dest="as_json")

    provider_parser = subparsers.add_parser("provider-capabilities", help="Describe provider adapter capabilities")
    provider_parser.add_argument("provider", nargs="?", default="runpod")
    provider_parser.add_argument("--json", action="store_true", dest="as_json")

    providers_parser = subparsers.add_parser("providers", help="List provider adapters and implementation status")
    providers_parser.add_argument("--json", action="store_true", dest="as_json")

    learnings_parser = subparsers.add_parser(
        "learnings",
        help="Self-learning ledger: record/search provider issues, render a research brief, promote fixes",
    )
    learnings_sub = learnings_parser.add_subparsers(dest="learnings_action", required=True)

    lrn_record = learnings_sub.add_parser("record", help="Append a learning to the ledger")
    lrn_record.add_argument("--provider", required=True, help="Provider/tool the issue is about")
    lrn_record.add_argument("--symptom", required=True, help="What went wrong (one line)")
    lrn_record.add_argument("--category", default="other", help=f"One of: {', '.join(CATEGORIES)} (free-form allowed)")
    lrn_record.add_argument("--severity", default="warn", choices=SEVERITIES)
    lrn_record.add_argument("--status", default="open", choices=STATUSES)
    lrn_record.add_argument("--context", default="", help="Where/when it happened")
    lrn_record.add_argument("--resolution", default="", help="The fix, if known (sets status implication)")
    lrn_record.add_argument("--evidence", default="", help="Log/command/citation pointer")
    lrn_record.add_argument("--tag", action="append", dest="tags", default=[], help="Repeatable tag")
    lrn_record.add_argument("--json", action="store_true", dest="as_json")

    lrn_list = learnings_sub.add_parser("list", help="List recent learnings")
    lrn_search = learnings_sub.add_parser("search", help="Search learnings before escalating")
    for sub in (lrn_list, lrn_search):
        sub.add_argument("--provider")
        sub.add_argument("--category")
        sub.add_argument("--severity", choices=SEVERITIES)
        sub.add_argument("--status", choices=STATUSES)
        sub.add_argument("--tag")
        sub.add_argument("--limit", type=int, default=20)
        sub.add_argument("--json", action="store_true", dest="as_json")
    lrn_search.add_argument("--query", help="Substring across symptom/context/resolution/evidence")

    lrn_brief = learnings_sub.add_parser("brief", help="Render a research-agent brief for a stuck provider issue")
    lrn_brief.add_argument("--provider", required=True)
    lrn_brief.add_argument("--symptom", required=True)
    lrn_brief.add_argument("--failing-invocation", default="", dest="failing_invocation", help="The exact command/output that failed")
    lrn_brief.add_argument("--no-record", action="store_true", dest="no_record", help="Do not auto-record an open learning for this symptom")
    lrn_brief.add_argument("--json", action="store_true", dest="as_json")

    lrn_promote = learnings_sub.add_parser("promote", help="Show scrub-clean resolved learnings ready for a provider entry")
    lrn_promote.add_argument("--provider", help="Limit to one provider")
    lrn_promote.add_argument("--mark", help="Mark a learning id as promoted (append-only marker)")
    lrn_promote.add_argument("--json", action="store_true", dest="as_json")

    lrn_stats = learnings_sub.add_parser("stats", help="Counts by provider/category/severity/status")
    lrn_stats.add_argument("--json", action="store_true", dest="as_json")

    closeout_parser = subparsers.add_parser("closeout", help="Hash artifacts and write local closeout files")
    closeout_parser.add_argument("manifest")
    closeout_parser.add_argument("--base-dir", default=".", help="Base directory for relative manifest paths")
    closeout_parser.add_argument("--json", action="store_true", dest="as_json")

    monitor_parser = subparsers.add_parser("monitor", help="Inspect local workload heartbeat/status/log files")
    monitor_parser.add_argument("manifest")
    monitor_parser.add_argument("--base-dir", default=".", help="Base directory for relative manifest paths")
    monitor_parser.add_argument("--previous", help="Previous monitor JSON report for advancement detection")
    monitor_parser.add_argument("--out", help="Write the JSON monitor report to this path")
    monitor_parser.add_argument("--json", action="store_true", dest="as_json")

    supervise_parser = subparsers.add_parser("supervise", help="Inspect workload state and recommend next supervisor action")
    supervise_parser.add_argument("manifest")
    supervise_parser.add_argument("--base-dir", default=".")
    supervise_parser.add_argument("--json", action="store_true", dest="as_json")

    local_parser = subparsers.add_parser("run-local", help="Execute the startup contract locally without RunPod")
    local_parser.add_argument("manifest")
    local_parser.add_argument("--repo-dir", default=".", help="Local directory to use as RUNPOD_REPO_DIR")
    local_parser.add_argument("--runtime-dir", default=".runtime/run-local", help="Directory for generated startup script and result JSON")
    local_parser.add_argument("--json", action="store_true", dest="as_json")

    doctor_parser = subparsers.add_parser("doctor", help="Check local and Symphony bridge discoverability")
    doctor_parser.add_argument("--json", action="store_true", dest="as_json")

    public_audit_parser = subparsers.add_parser("public-audit", help="Check whether the repo is ready for public release")
    public_audit_parser.add_argument("--json", action="store_true", dest="as_json")

    manifest_audit_parser = subparsers.add_parser("audit-manifests", help="Scan a repo or directory for RunPod launch manifests and stale templates")
    manifest_audit_parser.add_argument("root")
    manifest_audit_parser.add_argument("--migration-hints", action="store_true", help="Include common stale-to-current manifest migration hints")
    manifest_audit_parser.add_argument("--summary-only", action="store_true", help="Print repeated issue and migration-hint counts without per-manifest detail")
    manifest_audit_parser.add_argument("--only-failures", action="store_true", help="In plain output, omit manifests that only have warnings")
    manifest_audit_parser.add_argument("--json", action="store_true", dest="as_json")

    ops_audit_parser = subparsers.add_parser("audit-runpod-ops", help="Scan repo text for RunPod operational patterns that bypass bridge closeout")
    ops_audit_parser.add_argument("root")
    ops_audit_parser.add_argument("--include-logs", action="store_true", help="Include logs directories; default skips historical logs to reduce noise")
    ops_audit_parser.add_argument("--summary-only", action="store_true", help="Print repeated finding counts without per-line detail")
    ops_audit_parser.add_argument("--json", action="store_true", dest="as_json")

    issue_parser = subparsers.add_parser("validate-linear-issue", help="Validate a Symphony-ready Linear issue body")
    issue_parser.add_argument("issue_markdown")
    issue_parser.add_argument("--json", action="store_true", dest="as_json")

    issue_intake_parser = subparsers.add_parser("issue-intake", help="Validate a Linear issue body plus manifest and prepare a handoff packet")
    issue_intake_parser.add_argument("issue_markdown")
    issue_intake_parser.add_argument("--manifest", required=True)
    issue_intake_parser.add_argument("--out-dir", default=".runtime/issue-intake")
    issue_intake_parser.add_argument("--json", action="store_true", dest="as_json")

    linear_fetch_parser = subparsers.add_parser("linear-issue", help="Fetch a Linear issue body through the GraphQL API")
    linear_fetch_parser.add_argument("issue")
    linear_fetch_parser.add_argument("--out")
    linear_fetch_parser.add_argument("--json", action="store_true", dest="as_json")

    linear_comment_parser = subparsers.add_parser("linear-comment", help="Post a Linear issue comment from a file")
    linear_comment_parser.add_argument("issue")
    linear_comment_parser.add_argument("--body-file", required=True)
    linear_comment_parser.add_argument("--execute", action="store_true")
    linear_comment_parser.add_argument("--yes-comment-linear", action="store_true")
    linear_comment_parser.add_argument("--json", action="store_true", dest="as_json")

    create_parser = subparsers.add_parser("create-pod", help="Build or execute an audited RunPod pod creation request")
    create_parser.add_argument("manifest")
    create_parser.add_argument("--out-dir", default=".runtime/runpod-remote", help="Directory for request and resource records")
    create_parser.add_argument("--execute", action="store_true", help="Actually call RunPod REST API after all launch gates pass")
    create_parser.add_argument("--yes-create-paid-runpod", action="store_true", help="Required with --execute; confirms paid resources may be created")
    create_parser.add_argument("--max-spend-usd", type=float, help="Block creation when manifest budget exceeds this ceiling")
    create_parser.add_argument("--allow-duplicate", action="store_true", help="Allow creation even if an active pod with the same resource prefix exists")
    create_parser.add_argument("--json", action="store_true", dest="as_json")

    runpodctl_create_parser = subparsers.add_parser("render-runpodctl-create", help="Render a runpodctl pod create command without executing it")
    runpodctl_create_parser.add_argument("manifest")
    runpodctl_create_parser.add_argument("--json", action="store_true", dest="as_json")

    remote_run_parser = subparsers.add_parser("run-remote", help="Create, verify, and clean up one guarded RunPod run")
    remote_run_parser.add_argument("manifest")
    remote_run_parser.add_argument("--out-dir", default=".runtime/runpod-remote-run", help="Directory for remote run audit records")
    remote_run_parser.add_argument("--execute", action="store_true", help="Actually call RunPod REST API after all launch gates pass")
    remote_run_parser.add_argument("--yes-create-paid-runpod", action="store_true", help="Required with --execute; confirms paid resources may be created")
    remote_run_parser.add_argument("--yes-cleanup-runpod", action="store_true", help="Required with --execute; confirms created pods should be cleaned up")
    remote_run_parser.add_argument("--max-spend-usd", type=float, help="Block creation when manifest budget exceeds this ceiling")
    remote_run_parser.add_argument("--allow-duplicate", action="store_true", help="Allow creation even if an active pod with the same resource prefix exists")
    remote_run_parser.add_argument("--verification-mode", choices=("auto", "tcp", "proxy", "none"), default="auto", help="Artifact verification mode. Auto tries TCP first, then HTTP proxy.")
    remote_run_parser.add_argument("--port", type=int, default=8000, help="Internal pod artifact server port")
    remote_run_parser.add_argument("--timeout-seconds", type=int, default=180)
    remote_run_parser.add_argument("--interval-seconds", type=int, default=5)
    remote_run_parser.add_argument("--cleanup-action", choices=("stop", "delete"), default="delete")
    remote_run_parser.add_argument("--no-wait-cleanup", action="store_true", help="Submit cleanup without polling for deleted/stopped state")
    remote_run_parser.add_argument("--cleanup-timeout-seconds", type=int, default=120)
    remote_run_parser.add_argument("--lock-dir", help="Directory for atomic local launch locks. Defaults to RUNPOD_BRIDGE_LOCK_DIR or ~/.cache/runpod-bridge/locks.")
    remote_run_parser.add_argument("--json", action="store_true", dest="as_json")

    run_job_parser = subparsers.add_parser("run-job", help="Submit, poll, and verify one guarded Hugging Face Job (the batch-job provider surface)")
    run_job_parser.add_argument("manifest")
    run_job_parser.add_argument("--out-dir", default=".runtime/hf-job-run", help="Directory for job audit records and downloaded artifact evidence")
    run_job_parser.add_argument("--execute", action="store_true", help="Actually submit the job to the Hugging Face Jobs API after all gates pass")
    run_job_parser.add_argument("--yes-run-paid-hf-job", action="store_true", help="Required with --execute; confirms a paid HF Job may be submitted")
    run_job_parser.add_argument("--max-spend-usd", type=float, help="Block submission when the worst-case (flavor x timeout) estimate exceeds this ceiling")
    run_job_parser.add_argument("--poll-timeout-seconds", type=int, default=1800, help="Local budget for polling the job to a terminal stage; on timeout the job is canceled")
    run_job_parser.add_argument("--poll-interval-seconds", type=int, default=10)
    run_job_parser.add_argument("--log-tail", type=int, default=200, help="Number of trailing log lines to capture as evidence")
    run_job_parser.add_argument("--json", action="store_true", dest="as_json")

    run_handoff_parser = subparsers.add_parser("run-handoff", help="Execute an orchestrator-side provider handoff")
    run_handoff_parser.add_argument("handoff")
    run_handoff_parser.add_argument("--out-dir", default=".runtime/runpod-handoff-run", help="Directory for handoff and remote run audit records")
    run_handoff_parser.add_argument("--execute", action="store_true", help="Actually call RunPod REST API after all launch gates pass")
    run_handoff_parser.add_argument("--yes-create-paid-runpod", action="store_true", help="Required with --execute; confirms paid resources may be created")
    run_handoff_parser.add_argument("--yes-cleanup-runpod", action="store_true", help="Required with --execute; confirms created pods should be cleaned up")
    run_handoff_parser.add_argument("--max-spend-usd", type=float, help="Block creation when manifest budget exceeds this ceiling")
    run_handoff_parser.add_argument("--allow-duplicate", action="store_true", help="Allow creation even if an active pod with the same resource prefix exists")
    run_handoff_parser.add_argument("--verification-mode", choices=("auto", "tcp", "proxy", "none"))
    run_handoff_parser.add_argument("--port", type=int)
    run_handoff_parser.add_argument("--timeout-seconds", type=int)
    run_handoff_parser.add_argument("--interval-seconds", type=int)
    run_handoff_parser.add_argument("--cleanup-action", choices=("stop", "delete"))
    run_handoff_parser.add_argument("--no-wait-cleanup", action="store_true", help="Submit cleanup without polling for deleted/stopped state")
    run_handoff_parser.add_argument("--cleanup-timeout-seconds", type=int)
    run_handoff_parser.add_argument("--lock-dir", help="Directory for atomic local launch locks. Defaults to RUNPOD_BRIDGE_LOCK_DIR or ~/.cache/runpod-bridge/locks.")
    run_handoff_parser.add_argument("--json", action="store_true", dest="as_json")

    orchestrator_scan_parser = subparsers.add_parser("orchestrator-scan", help="Scan a directory tree for provider handoffs")
    orchestrator_scan_parser.add_argument("root")
    orchestrator_scan_parser.add_argument("--json", action="store_true", dest="as_json")

    orchestrator_once_parser = subparsers.add_parser("orchestrator-once", help="Run ready provider handoffs once from an orchestrator directory")
    orchestrator_once_parser.add_argument("root")
    orchestrator_once_parser.add_argument("--out-root", default=".runtime/orchestrator")
    orchestrator_once_parser.add_argument("--execute", action="store_true")
    orchestrator_once_parser.add_argument("--yes-create-paid-runpod", action="store_true")
    orchestrator_once_parser.add_argument("--yes-cleanup-runpod", action="store_true")
    orchestrator_once_parser.add_argument("--max-spend-usd", type=float)
    orchestrator_once_parser.add_argument("--lock-dir")
    orchestrator_once_parser.add_argument("--json", action="store_true", dest="as_json")

    list_pods_parser = subparsers.add_parser("list-pods", help="List RunPod pods through the REST API")
    list_pods_parser.add_argument("--name-prefix", default="", help="Optional pod name prefix filter")
    list_pods_parser.add_argument("--json", action="store_true", dest="as_json")

    get_pod_parser = subparsers.add_parser("get-pod", help="Fetch one RunPod pod through the REST API")
    get_pod_parser.add_argument("pod_id")
    get_pod_parser.add_argument("--json", action="store_true", dest="as_json")

    gpu_catalog_parser = subparsers.add_parser("gpu-catalog", help="Probe RunPod GraphQL GPU catalog and availability before REST create retries")
    gpu_catalog_parser.add_argument("--manifest", help="Read gpuTypeIds, dataCenterIds, cloudType, gpuCount, and networkVolumeId from a launch manifest")
    gpu_catalog_parser.add_argument("--gpu-type-id", action="append", default=[], help="Requested RunPod gpuTypeId. Repeat for fallback lists.")
    gpu_catalog_parser.add_argument("--data-center-id", action="append", default=[], help="Requested RunPod data center ID. Repeat for fallback data centers.")
    gpu_catalog_parser.add_argument("--cloud-type", choices=("SECURE", "COMMUNITY"), default="SECURE")
    gpu_catalog_parser.add_argument("--gpu-count", type=int, default=1)
    gpu_catalog_parser.add_argument("--network-volume-id", default="")
    gpu_catalog_parser.add_argument("--out", help="Write the JSON report to this path")
    gpu_catalog_parser.add_argument("--json", action="store_true", dest="as_json")

    runtime_parser = subparsers.add_parser("runtime-metrics", help="Fetch RunPod GraphQL runtime metrics and detect crash-loop signals")
    runtime_parser.add_argument("pod_id")
    runtime_parser.add_argument("--expected-elapsed-seconds", type=float, help="Seconds since allocation/start as observed by the operator")
    runtime_parser.add_argument("--expected-elapsed-minutes", type=float, help="Minutes since allocation/start as observed by the operator")
    runtime_parser.add_argument("--previous", help="Previous runtime-metrics JSON report for reset detection")
    runtime_parser.add_argument("--out", help="Write the JSON report to this path")
    runtime_parser.add_argument("--crash-loop-uptime-threshold-seconds", type=float, default=120)
    runtime_parser.add_argument("--json", action="store_true", dest="as_json")

    progress_report_parser = subparsers.add_parser("progress-report", help="Classify live RunPod pod progress without conflating monitor liveness with workload progress")
    progress_report_parser.add_argument("manifest")
    progress_report_parser.add_argument("pod_id")
    progress_report_parser.add_argument("--previous", help="Previous progress-report JSON for advancement detection")
    progress_report_parser.add_argument("--out", help="Write JSON report to this path")
    progress_report_parser.add_argument("--mode", choices=("auto", "proxy", "tcp"), default="auto")
    progress_report_parser.add_argument("--public-ip", default="")
    progress_report_parser.add_argument("--external-port", type=int)
    progress_report_parser.add_argument("--progress-timeout-seconds", type=int, default=5)
    progress_report_parser.add_argument("--json", action="store_true", dest="as_json")

    ssh_info_parser = subparsers.add_parser("pod-ssh-info", help="Fetch a pod SSH command through runpodctl")
    ssh_info_parser.add_argument("pod_id")
    ssh_info_parser.add_argument("--verbose", action="store_true")
    ssh_info_parser.add_argument("--json", action="store_true", dest="as_json")

    cleanup_parser = subparsers.add_parser("cleanup-pod", help="Build or execute an audited RunPod stop/delete request")
    cleanup_parser.add_argument("pod_id")
    cleanup_parser.add_argument("--action", choices=("stop", "delete"), required=True)
    cleanup_parser.add_argument("--out-dir", default=".runtime/runpod-remote", help="Directory for cleanup records")
    cleanup_parser.add_argument("--execute", action="store_true", help="Actually call RunPod REST API")
    cleanup_parser.add_argument("--yes-cleanup-runpod", action="store_true", help="Required with --execute; confirms the pod should be stopped or deleted")
    cleanup_parser.add_argument("--wait", action="store_true", help="Poll until cleanup is verified")
    cleanup_parser.add_argument("--timeout-seconds", type=int, default=120)
    cleanup_parser.add_argument("--interval-seconds", type=int, default=5)
    cleanup_parser.add_argument("--json", action="store_true", dest="as_json")

    cost_parser = subparsers.add_parser("cost-report", help="Estimate cost from a remote run record and optionally query RunPod billing")
    cost_parser.add_argument("record")
    cost_parser.add_argument("--fetch-billing", action="store_true")
    cost_parser.add_argument("--json", action="store_true", dest="as_json")

    outcome_parser = subparsers.add_parser("remote-outcome", help="Render a Linear-ready symphony-outcome block from a remote run record")
    outcome_parser.add_argument("record")
    outcome_parser.add_argument("--out", default="runpod-execution/symphony_outcome.md")
    outcome_parser.add_argument("--fetch-billing", action="store_true")
    outcome_parser.add_argument("--json", action="store_true", dest="as_json")

    billing_pods_parser = subparsers.add_parser("billing-pods", help="Fetch RunPod pod billing records")
    billing_pods_parser.add_argument("--pod-id")
    billing_pods_parser.add_argument("--start-time")
    billing_pods_parser.add_argument("--end-time")
    billing_pods_parser.add_argument("--bucket-size", default="day")
    billing_pods_parser.add_argument("--grouping", default="podId")
    billing_pods_parser.add_argument("--gpu-id")
    billing_pods_parser.add_argument("--backend", choices=("rest", "runpodctl"), default="rest")
    billing_pods_parser.add_argument("--json", action="store_true", dest="as_json")

    billing_endpoints_parser = subparsers.add_parser("billing-endpoints", help="Fetch RunPod Serverless endpoint billing records")
    billing_endpoints_parser.add_argument("--endpoint-id")
    billing_endpoints_parser.add_argument("--pod-id")
    billing_endpoints_parser.add_argument("--start-time")
    billing_endpoints_parser.add_argument("--end-time")
    billing_endpoints_parser.add_argument("--bucket-size", default="day")
    billing_endpoints_parser.add_argument("--grouping", default="endpointId")
    billing_endpoints_parser.add_argument("--data-center-id", action="append", default=[])
    billing_endpoints_parser.add_argument("--gpu-type-id", action="append", default=[])
    billing_endpoints_parser.add_argument("--gpu-id")
    billing_endpoints_parser.add_argument("--image-name")
    billing_endpoints_parser.add_argument("--template-id")
    billing_endpoints_parser.add_argument("--backend", choices=("rest", "runpodctl"), default="rest")
    billing_endpoints_parser.add_argument("--json", action="store_true", dest="as_json")

    billing_volumes_parser = subparsers.add_parser("billing-network-volumes", help="Fetch RunPod network volume billing records")
    billing_volumes_parser.add_argument("--start-time")
    billing_volumes_parser.add_argument("--end-time")
    billing_volumes_parser.add_argument("--bucket-size", default="day")
    billing_volumes_parser.add_argument("--backend", choices=("rest", "runpodctl"), default="rest")
    billing_volumes_parser.add_argument("--json", action="store_true", dest="as_json")

    network_volume_verify_parser = subparsers.add_parser("verify-network-volume-s3", help="Download, extract, and close out a RunPod network-volume S3 artifact archive")
    network_volume_verify_parser.add_argument("manifest")
    network_volume_verify_parser.add_argument("--out-dir", default=".runtime/network-volume-s3-verify")
    network_volume_verify_parser.add_argument("--execute", action="store_true", help="Run AWS CLI commands. Without this, render the plan only.")
    network_volume_verify_parser.add_argument("--timeout-seconds", type=int, default=180)
    network_volume_verify_parser.add_argument("--interval-seconds", type=int, default=5)
    network_volume_verify_parser.add_argument("--json", action="store_true", dest="as_json")

    volumes_parser = subparsers.add_parser("list-network-volumes", help="List RunPod network volumes")
    volumes_parser.add_argument("--json", action="store_true", dest="as_json")

    get_volume_parser = subparsers.add_parser("get-network-volume", help="Fetch one RunPod network volume")
    get_volume_parser.add_argument("network_volume_id")
    get_volume_parser.add_argument("--json", action="store_true", dest="as_json")

    templates_parser = subparsers.add_parser("list-templates", help="List RunPod templates")
    templates_parser.add_argument("--json", action="store_true", dest="as_json")

    get_template_parser = subparsers.add_parser("get-template", help="Fetch one RunPod template")
    get_template_parser.add_argument("template_id")
    get_template_parser.add_argument("--json", action="store_true", dest="as_json")

    dashboard_parser = subparsers.add_parser("dashboard", help="Render a local HTML dashboard from run records")
    dashboard_parser.add_argument("--scan-dir", default=".runtime")
    dashboard_parser.add_argument("--out", default=".runtime/runpod-dashboard.html")
    dashboard_parser.add_argument("--json", action="store_true", dest="as_json")

    recover_parser = subparsers.add_parser("recover-run", help="Analyze or execute recovery for a run record")
    recover_parser.add_argument("record")
    recover_parser.add_argument("--action", choices=("stop", "delete"), default="delete")
    recover_parser.add_argument("--execute-cleanup", action="store_true")
    recover_parser.add_argument("--yes-cleanup-runpod", action="store_true")
    recover_parser.add_argument("--out-dir")
    recover_parser.add_argument("--json", action="store_true", dest="as_json")

    fetch_proxy_parser = subparsers.add_parser("fetch-proxy-file", help="Fetch a file from a pod's RunPod HTTP proxy")
    fetch_proxy_parser.add_argument("pod_id")
    fetch_proxy_parser.add_argument("remote_path")
    fetch_proxy_parser.add_argument("--port", type=int, default=8000)
    fetch_proxy_parser.add_argument("--out", required=True)
    fetch_proxy_parser.add_argument("--timeout-seconds", type=int, default=30)
    fetch_proxy_parser.add_argument("--json", action="store_true", dest="as_json")

    verify_proxy_parser = subparsers.add_parser("verify-proxy-packet", help="Download and close out a pod execution packet via HTTP proxy")
    verify_proxy_parser.add_argument("manifest")
    verify_proxy_parser.add_argument("pod_id")
    verify_proxy_parser.add_argument("--port", type=int, default=8000)
    verify_proxy_parser.add_argument("--out-dir", default=".runtime/proxy-packet")
    verify_proxy_parser.add_argument("--timeout-seconds", type=int, default=180)
    verify_proxy_parser.add_argument("--interval-seconds", type=int, default=5)
    verify_proxy_parser.add_argument("--json", action="store_true", dest="as_json")

    fetch_tcp_parser = subparsers.add_parser("fetch-tcp-file", help="Fetch a file through a pod's direct TCP HTTP service")
    fetch_tcp_parser.add_argument("host")
    fetch_tcp_parser.add_argument("external_port", type=int)
    fetch_tcp_parser.add_argument("remote_path")
    fetch_tcp_parser.add_argument("--out", required=True)
    fetch_tcp_parser.add_argument("--timeout-seconds", type=int, default=30)
    fetch_tcp_parser.add_argument("--json", action="store_true", dest="as_json")

    verify_tcp_parser = subparsers.add_parser("verify-tcp-packet", help="Download and close out a pod execution packet via direct TCP")
    verify_tcp_parser.add_argument("manifest")
    verify_tcp_parser.add_argument("pod_id")
    verify_tcp_parser.add_argument("--port", type=int, default=8000, help="Internal pod port to resolve from RunPod port mappings")
    verify_tcp_parser.add_argument("--out-dir", default=".runtime/tcp-packet")
    verify_tcp_parser.add_argument("--timeout-seconds", type=int, default=180)
    verify_tcp_parser.add_argument("--interval-seconds", type=int, default=5)
    verify_tcp_parser.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args(argv)

    if args.command == "validate-manifest":
        manifest = load_manifest(args.manifest)
        result = validate_manifest(manifest)
        if args.as_json:
            sys.stdout.write(dumps_pretty(result.as_dict()))
        else:
            print_validation(result)
        return 0 if result.ok else 1

    if args.command == "plan":
        manifest = load_manifest(args.manifest)
        result = validate_manifest(manifest)
        plan = build_plan(manifest, result)
        if args.as_json:
            sys.stdout.write(dumps_pretty(plan))
        else:
            print_plan(plan)
        return 0 if result.ok else 1

    if args.command == "render-startup":
        manifest = load_manifest(args.manifest)
        result = validate_manifest(manifest)
        if not result.ok:
            print_validation(result, stream=sys.stderr)
            return 1
        script = render_startup_script(manifest)
        if args.out:
            output = Path(args.out)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(script)
            output.chmod(0o755)
        else:
            sys.stdout.write(script)
        return 0

    if args.command == "prepare":
        manifest = load_manifest(args.manifest)
        preflight = prepare_packet(
            manifest,
            args.out_dir,
            source_dir=args.source_dir,
            source_archive_pod_path=args.source_archive_pod_path,
        )
        if args.as_json:
            sys.stdout.write(json.dumps(preflight, indent=2, sort_keys=True) + "\n")
        else:
            print(f"ok: {str(preflight['ok']).lower()}")
            print(f"launch_manifest: {preflight['files']['launch_manifest']}")
            print(f"local_preflight: {preflight['files']['local_preflight']}")
            if preflight["files"]["startup"]:
                print(f"startup: {preflight['files']['startup']}")
            if preflight["plan"]["blockers"]:
                print("blockers:")
                for blocker in preflight["plan"]["blockers"]:
                    print(f"  - {blocker}")
        return 0 if preflight["ok"] else 1

    if args.command == "write-handoff":
        manifest = load_manifest(args.manifest)
        handoff = write_provider_handoff(
            manifest,
            manifest_path=args.manifest,
            out_path=args.out,
            reason=args.reason,
            worker_id=args.worker_id,
            local_preflight_path=args.local_preflight,
            startup_path=args.startup,
            source_archive_path=args.source_archive,
            source_archive_manifest_path=args.source_archive_manifest,
            verification_mode=args.verification_mode,
            port=args.port,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
            cleanup_action=args.cleanup_action,
        )
        if args.as_json:
            sys.stdout.write(json.dumps(handoff, indent=2, sort_keys=True) + "\n")
        else:
            print(f"status: {handoff['status']}")
            print(f"provider_handoff: {Path(args.out).resolve()}")
        return 0 if handoff["status"] == "ready_for_orchestrator" else 2

    if args.command == "validate-handoff":
        handoff = load_provider_handoff(args.handoff)
        validation = validate_provider_handoff(handoff, handoff_path=args.handoff)
        if args.as_json:
            sys.stdout.write(json.dumps(validation, indent=2, sort_keys=True) + "\n")
        else:
            print(f"handoff valid: {str(validation['ok']).lower()}")
            for issue in validation["errors"]:
                print(f"ERROR {issue['path']}: {issue['message']}")
            for issue in validation["warnings"]:
                print(f"WARN  {issue['path']}: {issue['message']}")
        return 0 if validation["ok"] else 1

    if args.command == "preflight":
        manifest = load_manifest(args.manifest)
        report = analyze_preflight(manifest)
        if args.as_json:
            sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            print(f"ok: {str(report['ok']).lower()}")
            print(f"run_id: {report.get('run_id')}")
            print(f"profile: {report['recommended_profile']['name']}")
            if report.get("payload"):
                print(f"payload_post_body_bytes: {report['payload'].get('post_body_bytes')}")
                print(f"payload_docker_start_cmd_script_bytes: {report['payload'].get('docker_start_cmd_script_bytes')}")
            if report.get("bootstrap_requirements"):
                print(f"bootstrap_requires_git: {str(report['bootstrap_requirements'].get('requires_git')).lower()}")
                print(f"bootstrap_git_available_declared: {str(report['bootstrap_requirements'].get('git_available_declared')).lower()}")
                print(f"bootstrap_private_registry_likely: {str(report['bootstrap_requirements'].get('likely_private_registry_image')).lower()}")
                print(f"bootstrap_registry_auth_declared: {str(report['bootstrap_requirements'].get('registry_auth_declared')).lower()}")
            for issue in report["errors"]:
                print(f"ERROR {issue.get('path')}: {issue.get('message')}")
            for issue in report["warnings"]:
                print(f"WARN  {issue.get('path')}: {issue.get('message')}")
            for item in report["recommendations"]:
                print(f"RECOMMEND {item}")
        return 0 if report["ok"] else 1

    if args.command == "contract-self-check":
        manifest = load_manifest(args.manifest)
        report = contract_self_check(manifest)
        if args.as_json:
            sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            print(f"ok: {str(report['ok']).lower()}")
            print(f"run_id: {report.get('run_id')}")
            print(f"scale: {report.get('scale')}")
            print(f"claim_level: {report.get('claim_level')}")
            for issue in report["errors"]:
                print(f"ERROR {issue.get('path')}: {issue.get('message')}")
            for issue in report["warnings"]:
                print(f"WARN  {issue.get('path')}: {issue.get('message')}")
            for item in report["recommendations"]:
                print(f"RECOMMEND {item}")
        return 0 if report["ok"] else 1

    if args.command == "egress-plan":
        manifest = load_manifest(args.manifest)
        plan = build_egress_plan(manifest)
        if args.as_json:
            sys.stdout.write(json.dumps(plan, indent=2, sort_keys=True) + "\n")
        else:
            print(f"mode: {plan['mode']}")
            print(f"durable: {str(plan['durable']).lower()}")
            for blocker in plan["blockers"]:
                print(f"ERROR {blocker}")
            for warning in plan["warnings"]:
                print(f"WARN  {warning}")
            for command in plan["commands"]:
                print(command)
        return 0 if plan["ok"] else 1

    if args.command == "productivity-plan":
        manifest = load_manifest(args.manifest)
        plan = build_productivity_plan(
            manifest,
            pod_id=args.pod_id,
            public_ip=args.public_ip,
            external_port=args.external_port,
        )
        if args.as_json:
            sys.stdout.write(json.dumps(plan, indent=2, sort_keys=True) + "\n")
        else:
            print(f"ok: {str(plan['ok']).lower()}")
            print(f"run_id: {plan.get('run_id')}")
            print("productive_definition:")
            for item in plan["productive_definition"]:
                print(f"  - {item}")
            for signal in plan["signals"]:
                print(f"{signal['name']}: {signal['status']} - {signal.get('proves')}")
                if signal.get("does_not_prove"):
                    print(f"  does_not_prove: {signal['does_not_prove']}")
                if signal.get("connection_refused_means"):
                    print(f"  connection_refused_means: {signal['connection_refused_means']}")
            for warning in plan["warnings"]:
                print(f"WARN  {warning}")
            for blocker in plan["blockers"]:
                print(f"ERROR {blocker}")
            for command in plan["commands"]:
                print(command)
        return 0 if plan["ok"] else 1

    if args.command == "source-check":
        manifest = load_manifest(args.manifest)
        report = check_source_reachability(manifest, execute=args.execute, timeout_seconds=args.timeout_seconds)
        if args.as_json:
            sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            print(f"ok: {str(report['ok']).lower()}")
            print(f"status: {report['status']}")
            if report.get("run_id"):
                print(f"run_id: {report['run_id']}")
            if report.get("ref"):
                print(f"ref: {report['ref']}")
            for warning in report.get("warnings", []):
                print(f"WARN  {warning}")
            for error in report.get("errors", []):
                print(f"ERROR {error}")
            for command in report.get("commands", []):
                print(command)
        return 0 if report["ok"] else 1

    if args.command == "source-ingress-plan":
        manifest = load_manifest(args.manifest)
        plan = build_source_ingress_plan(manifest, source_archive_path=args.source_archive)
        if args.as_json:
            sys.stdout.write(json.dumps(plan, indent=2, sort_keys=True) + "\n")
        else:
            print(f"ok: {str(plan['ok']).lower()}")
            print(f"mode: {plan['mode']}")
            if plan["required_env"]:
                print("required_env:")
                for item in plan["required_env"]:
                    print(f"  - {item}")
            for blocker in plan["blockers"]:
                print(f"ERROR {blocker}")
            for warning in plan["warnings"]:
                print(f"WARN  {warning}")
            for command in plan["commands"]:
                print(command)
        return 0 if plan["ok"] else 1

    if args.command == "registry-auth-plan":
        manifest = load_manifest(args.manifest)
        plan = build_registry_auth_plan(manifest)
        if args.as_json:
            sys.stdout.write(json.dumps(plan, indent=2, sort_keys=True) + "\n")
        else:
            print(f"status: {plan['status']}")
            print(f"ok: {str(plan['ok']).lower()}")
            print(f"image: {plan['image']}")
            print(f"registry_host: {plan['registry_host']}")
            for blocker in plan["blockers"]:
                print(f"ERROR {blocker}")
            for warning in plan["warnings"]:
                print(f"WARN  {warning}")
            for command in plan["commands"]:
                print(command)
            print(f"canary_success_requirement: {plan['canary']['success_requirement']}")
        return 0 if plan["ok"] else 1

    if args.command == "aws-orchestrator-plan":
        manifest = load_manifest(args.manifest)
        plan = build_aws_orchestrator_plan(manifest, handoff_path=args.handoff)
        if args.out_dir:
            write_aws_helper_files(plan, Path(args.out_dir))
        if args.as_json:
            sys.stdout.write(json.dumps(plan, indent=2, sort_keys=True) + "\n")
        else:
            print(f"ok: {str(plan['ok']).lower()}")
            print(f"run_id: {plan.get('run_id')}")
            if plan["required_env"]:
                print("required_env:")
                for env_name in plan["required_env"]:
                    print(f"  - {env_name}")
            for name, feature in plan["features"].items():
                print(f"{name}: {feature['status']} - {feature['summary']}")
                for warning in feature.get("warnings", []):
                    print(f"  WARN {warning}")
                for blocker in feature.get("blockers", []):
                    print(f"  ERROR {blocker}")
                for command in feature.get("commands", []):
                    print(f"  {command}")
            if args.out_dir:
                print(f"helper_files: {Path(args.out_dir).resolve()}")
        return 0 if plan["ok"] else 1

    if args.command == "profiles":
        if args.recommend_for:
            payload = recommend_profile(load_manifest(args.recommend_for))
        elif args.name:
            try:
                payload = get_profile(args.name)
            except KeyError as exc:
                print(str(exc), file=sys.stderr)
                return 1
        else:
            payload = {"profiles": list_profiles()}
        if args.as_json:
            sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        else:
            profiles = payload.get("profiles") if isinstance(payload, dict) else None
            if isinstance(profiles, list):
                for profile in profiles:
                    print(f"{profile['name']}: {profile['description']}")
            else:
                print(f"{payload['name']}: {payload['description']}")
        return 0

    if args.command == "providers":
        rows = [adapter.status() for adapter in available_adapters()]
        if args.as_json:
            sys.stdout.write(json.dumps(rows, indent=2, sort_keys=True) + "\n")
        else:
            for row in rows:
                if row["automated_launch"]:
                    flag = "launch:auto"
                else:
                    flag = "setup:guide"
                print(f"{row['provider']:11} [{flag:14}] {row['adapter']} ({row.get('category','')}): {row['summary']}")
        return 0

    if args.command == "provider-capabilities":
        try:
            capabilities = get_adapter(args.provider).capabilities()
        except KeyError:
            capabilities = provider_capabilities(args.provider)
        if args.as_json:
            sys.stdout.write(json.dumps(capabilities, indent=2, sort_keys=True) + "\n")
        else:
            print(f"provider: {capabilities['provider']}")
            print(f"automated launch support: {str(capabilities.get('automated_launch')).lower()}")
            print(f"adapter: {capabilities.get('adapter')}")
            if capabilities.get("category"):
                print(f"category: {capabilities['category']}")
            if capabilities.get("provenance"):
                print(f"provenance: {capabilities['provenance']}")
            if capabilities.get("summary"):
                print(f"summary: {capabilities['summary']}")
        return 0 if capabilities.get("automated_launch") else 2

    if args.command == "learnings":
        return run_learnings(args)

    if args.command == "closeout":
        manifest = load_manifest(args.manifest)
        closeout = write_closeout_files(manifest, args.base_dir)
        if args.as_json:
            sys.stdout.write(json.dumps(closeout, indent=2, sort_keys=True) + "\n")
        else:
            print(f"status: {closeout['status']}")
            print(f"artifacts: {len(closeout['artifacts'])}")
            if closeout["missing_required_artifacts"]:
                print("missing required artifacts:")
                for path in closeout["missing_required_artifacts"]:
                    print(f"  - {path}")
        return 0 if closeout["status"] == "succeeded" else 1

    if args.command == "monitor":
        manifest = load_manifest(args.manifest)
        previous = read_json_file(args.previous) if args.previous else None
        report = inspect_execution(manifest, args.base_dir, previous_report=previous)
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        if args.as_json:
            sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            print(f"state: {report['state']}")
            print(f"workload_terminal_reported: {str(report['workload_terminal_reported']).lower()}")
            if report["workload_terminal_status"]:
                print(f"workload_terminal_status: {report['workload_terminal_status']}")
            print(f"final_success: {str(report['final_success']).lower()}")
            print(f"final_success_reason: {report['final_success_reason']}")
            print(f"status_present: {str(report['files']['status_present']).lower()}")
            print(f"heartbeat_present: {str(report['files']['heartbeat_present']).lower()}")
            print(f"log_present: {str(report['files']['log_present']).lower()}")
            if report["silence"]["minutes_since_heartbeat"] is not None:
                print(f"minutes_since_heartbeat: {report['silence']['minutes_since_heartbeat']}")
            print(f"advancement: {str(report['advancement']['advanced']).lower()} ({report['advancement']['reason']})")
            print(f"productivity: {report['productivity']['state']} ({report['productivity']['confidence']})")
        if report["state"] == "running":
            return 0
        if report["state"] == "terminal_reported":
            return 2
        return 1

    if args.command == "supervise":
        manifest = load_manifest(args.manifest)
        report = supervise_execution(manifest, args.base_dir)
        if args.as_json:
            sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            print(f"state: {report['state']}")
            print(f"action: {report['action']}")
        return 0 if report["state"] in ("running", "terminal_reported") else 1

    if args.command == "run-local":
        manifest = load_manifest(args.manifest)
        result = run_local(manifest, repo_dir=args.repo_dir, runtime_dir=args.runtime_dir)
        if args.as_json:
            sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        else:
            print(f"ok: {str(result['ok']).lower()}")
            print(f"returncode: {result.get('returncode')}")
            print(f"script_path: {result.get('script_path')}")
            closeout = result.get("closeout", {})
            if closeout:
                print(f"closeout_status: {closeout.get('status')}")
        return 0 if result["ok"] else 1

    if args.command == "doctor":
        report = run_doctor()
        if args.as_json:
            sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            print(f"overall: {report['overall']}")
            for check in report["checks"]:
                print(f"{check['status'].upper()} {check['name']}: {check['message']}")
        if report["overall"] == "fail":
            return 1
        if report["overall"] == "warn":
            return 2
        return 0

    if args.command == "public-audit":
        report = run_public_audit()
        if args.as_json:
            sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            print(f"overall: {report['overall']}")
            for check in report["checks"]:
                print(f"{check['status'].upper()} {check['name']}: {check['message']}")
        return 0 if report["overall"] == "pass" else 1

    if args.command == "audit-manifests":
        report = audit_manifest_tree(args.root, migration_hints=args.migration_hints)
        if args.as_json:
            sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            summary = report["summary"]
            print(f"ok: {str(report['ok']).lower()}")
            print(f"manifest_candidates: {summary['manifest_candidates']}")
            print(f"failures: {summary['failures']}")
            print(f"with_warnings: {summary['with_warnings']}")
            print_manifest_audit_summary(report)
            if args.summary_only:
                print_manifest_audit_failure_paths(report)
                return 0 if report["ok"] else 1
            for item in report["results"]:
                if args.only_failures and item["ok"]:
                    continue
                if item["ok"] and not item.get("warnings"):
                    continue
                print(f"{'OK' if item['ok'] else 'FAIL'} {item['path']}")
                for issue in item.get("errors", []):
                    print(f"  ERROR {issue.get('path')}: {issue.get('message')}")
                for issue in item.get("warnings", []):
                    print(f"  WARN  {issue.get('path')}: {issue.get('message')}")
                for hint in item.get("migration_hints", []):
                    print(f"  MIGRATE {hint}")
        return 0 if report["ok"] else 1

    if args.command == "audit-runpod-ops":
        report = audit_runpod_ops_tree(args.root, include_logs=args.include_logs)
        if args.as_json:
            sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            summary = report["summary"]
            print(f"ok: {str(report['ok']).lower()}")
            print(f"files_scanned: {summary['files_scanned']}")
            print(f"findings: {summary['findings']}")
            print(f"errors: {summary['errors']}")
            print(f"warnings: {summary['warnings']}")
            print_runpod_ops_summary(report)
            if not args.summary_only:
                for finding in report["findings"]:
                    location = finding["path"]
                    if finding.get("line") is not None:
                        location = f"{location}:{finding['line']}"
                    print(f"{finding['severity'].upper()} {finding['rule']} {location}")
                    print(f"  {finding['message']}")
                    print(f"  SUGGEST {finding['suggestion']}")
        return 0 if report["ok"] else 1

    if args.command == "validate-linear-issue":
        result = validate_issue_file(args.issue_markdown)
        if args.as_json:
            sys.stdout.write(json.dumps(result.as_dict(), indent=2, sort_keys=True) + "\n")
        else:
            print(f"issue valid: {str(result.ok).lower()}")
            for issue in result.errors:
                print(f"ERROR {issue.path}: {issue.message}")
            for issue in result.warnings:
                print(f"WARN  {issue.path}: {issue.message}")
        return 0 if result.ok else 1

    if args.command == "issue-intake":
        result = issue_intake(args.issue_markdown, args.manifest, args.out_dir)
        if args.as_json:
            sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        else:
            print(f"ready_for_remote: {str(result['ready_for_remote']).lower()}")
            print(f"handoff_path: {result['handoff_path']}")
        return 0 if result["issue_validation"]["ok"] and result["manifest_validation"]["ok"] else 1

    if args.command == "linear-issue":
        try:
            issue = LinearClient().get_issue(args.issue)
        except LinearApiError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        payload: dict[str, Any] = {"issue": issue}
        if args.out:
            payload["markdown"] = write_issue_markdown(issue, args.out)
        if args.as_json:
            sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        else:
            print(f"{issue.get('identifier')} {issue.get('title')}")
            if args.out:
                print(f"markdown: {payload['markdown']['path']}")
        return 0

    if args.command == "linear-comment":
        body = Path(args.body_file).read_text()
        if not args.execute:
            payload = {"action": "linear_comment", "execute": False, "issue": args.issue, "body_file": str(Path(args.body_file).resolve())}
        else:
            if not args.yes_comment_linear:
                print("--execute requires --yes-comment-linear", file=sys.stderr)
                return 2
            try:
                payload = LinearClient().create_comment(args.issue, body)
            except LinearApiError as exc:
                print(str(exc), file=sys.stderr)
                return 1
        if args.as_json:
            sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        else:
            print(f"execute: {str(args.execute).lower()}")
            if isinstance(payload, dict) and payload.get("comment"):
                print(f"comment: {payload['comment'].get('url') or payload['comment'].get('id')}")
        return 0

    if args.command == "create-pod":
        manifest = load_manifest(args.manifest)
        if args.execute and not args.yes_create_paid_runpod:
            print("--execute requires --yes-create-paid-runpod", file=sys.stderr)
            return 2
        try:
            record = create_pod_flow(
                manifest,
                out_dir=args.out_dir,
                execute=args.execute,
                max_spend_usd=args.max_spend_usd,
                allow_duplicate=args.allow_duplicate,
            )
        except RunpodRestError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        else:
            print(f"status: {record['status']}")
            print(f"resource_record: {Path(args.out_dir).resolve() / 'runpod_resource_record.json'}")
            if record.get("blockers"):
                print("blockers:")
                for blocker in record["blockers"]:
                    print(f"  - {blocker}")
            if record.get("response", {}).get("id"):
                print(f"pod_id: {record['response']['id']}")
        return 0 if record["status"] in ("dry_run_request", "created") else 2

    if args.command == "render-runpodctl-create":
        manifest = load_manifest(args.manifest)
        command = build_pod_create_command(manifest)
        payload = {"command": command, "shell": shell_join(command)}
        if args.as_json:
            sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        else:
            print(payload["shell"])
        return 0

    if args.command == "run-remote":
        manifest = load_manifest(args.manifest)
        provider_block = manifest.get("provider") if isinstance(manifest.get("provider"), dict) else {}
        provider_name = str(provider_block.get("name") or "runpod")
        if provider_name != "runpod":
            print(
                f"run-remote is the RunPod pod flow; provider {provider_name!r} uses its own command "
                "(e.g. run-job for huggingface)",
                file=sys.stderr,
            )
            return 2
        if args.execute and not args.yes_create_paid_runpod:
            print("--execute requires --yes-create-paid-runpod", file=sys.stderr)
            return 2
        if args.execute and not args.yes_cleanup_runpod:
            print("--execute requires --yes-cleanup-runpod", file=sys.stderr)
            return 2
        try:
            record = run_remote_flow(
                manifest,
                out_dir=args.out_dir,
                execute=args.execute,
                max_spend_usd=args.max_spend_usd,
                allow_duplicate=args.allow_duplicate,
                verification_mode=args.verification_mode,
                port=args.port,
                timeout_seconds=args.timeout_seconds,
                interval_seconds=args.interval_seconds,
                cleanup_action=args.cleanup_action,
                cleanup_wait=not args.no_wait_cleanup,
                cleanup_timeout_seconds=args.cleanup_timeout_seconds if args.cleanup_timeout_seconds is not None else 120,
                lock_dir=args.lock_dir,
            )
        except (RunpodRestError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        else:
            print(f"status: {record['status']}")
            print(f"remote_run_record: {Path(args.out_dir).resolve() / 'remote_run_record.json'}")
            pod_id = record.get("create", {}).get("pod_id") or record.get("create", {}).get("response", {}).get("id")
            if pod_id:
                print(f"pod_id: {pod_id}")
            cleanup_status = record.get("cleanup", {}).get("status")
            if cleanup_status:
                print(f"cleanup_status: {cleanup_status}")
        return 0 if record["status"] in ("dry_run_request", "succeeded") else 2

    if args.command == "run-job":
        manifest = load_manifest(args.manifest)
        provider_block = manifest.get("provider") if isinstance(manifest.get("provider"), dict) else {}
        provider_name = str(provider_block.get("name") or "")
        if provider_name != "huggingface":
            print(f"run-job currently supports the huggingface provider; got {provider_name or 'runpod'!r}", file=sys.stderr)
            return 2
        if args.execute and not args.yes_run_paid_hf_job:
            print("--execute requires --yes-run-paid-hf-job", file=sys.stderr)
            return 2
        try:
            record = run_job_flow(
                manifest,
                out_dir=args.out_dir,
                execute=args.execute,
                max_spend_usd=args.max_spend_usd,
                poll_timeout_seconds=args.poll_timeout_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
                log_tail=args.log_tail,
            )
        except (HfJobsError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        else:
            print(f"status: {record['status']}")
            print(f"hf_job_record: {Path(args.out_dir).resolve() / 'hf_job_record.json'}")
            job_id = record.get("submit", {}).get("job_id")
            if job_id:
                print(f"job_id: {job_id}")
            estimate = record.get("cost_estimate", {}).get("worst_case_usd")
            if estimate is not None:
                print(f"worst_case_usd: {estimate}")
            for blocker in record.get("blockers", []):
                print(f"blocker: {blocker}")
        return 0 if record["status"] in ("dry_run_request", "artifacts_verified") else 2

    if args.command == "run-handoff":
        if args.execute and not args.yes_create_paid_runpod:
            print("--execute requires --yes-create-paid-runpod", file=sys.stderr)
            return 2
        if args.execute and not args.yes_cleanup_runpod:
            print("--execute requires --yes-cleanup-runpod", file=sys.stderr)
            return 2
        try:
            record = run_handoff_flow(
                args.handoff,
                out_dir=args.out_dir,
                execute=args.execute,
                max_spend_usd=args.max_spend_usd,
                allow_duplicate=args.allow_duplicate,
                verification_mode=args.verification_mode,
                port=args.port,
                timeout_seconds=args.timeout_seconds,
                interval_seconds=args.interval_seconds,
                cleanup_action=args.cleanup_action,
                cleanup_wait=not args.no_wait_cleanup,
                cleanup_timeout_seconds=args.cleanup_timeout_seconds if args.cleanup_timeout_seconds is not None else 120,
                lock_dir=args.lock_dir,
            )
        except (RunpodRestError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        else:
            print(f"status: {record['status']}")
            print(f"handoff_run_record: {Path(args.out_dir).resolve() / 'handoff_run_record.json'}")
            pod_id = record.get("remote_run", {}).get("create", {}).get("pod_id")
            if pod_id:
                print(f"pod_id: {pod_id}")
            cleanup_status = record.get("remote_run", {}).get("cleanup", {}).get("status")
            if cleanup_status:
                print(f"cleanup_status: {cleanup_status}")
        return 0 if record["status"] in ("dry_run_request", "succeeded") else 2

    if args.command == "orchestrator-scan":
        scan = scan_handoffs(args.root)
        if args.as_json:
            sys.stdout.write(json.dumps(scan, indent=2, sort_keys=True) + "\n")
        else:
            print(f"ready: {len(scan['ready'])}")
            print(f"blocked: {len(scan['blocked'])}")
            for item in scan["handoffs"]:
                print(f"{item['validation'].get('status', '')} {item['path']}")
        return 0 if scan["ready"] else 1

    if args.command == "orchestrator-once":
        if args.execute and not args.yes_create_paid_runpod:
            print("--execute requires --yes-create-paid-runpod", file=sys.stderr)
            return 2
        if args.execute and not args.yes_cleanup_runpod:
            print("--execute requires --yes-cleanup-runpod", file=sys.stderr)
            return 2
        record = run_orchestrator_once(
            args.root,
            out_root=args.out_root,
            execute=args.execute,
            max_spend_usd=args.max_spend_usd,
            lock_dir=args.lock_dir,
        )
        if args.as_json:
            sys.stdout.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        else:
            print(f"runs: {len(record['runs'])}")
            print(f"ready: {len(record['scan']['ready'])}")
            print(f"blocked: {len(record['scan']['blocked'])}")
        failed = [item for item in record["runs"] if item.get("status") not in ("dry_run_request", "succeeded")]
        return 0 if not failed else 2

    if args.command == "list-pods":
        try:
            pods = RunpodRestClient().list_pods(args.name_prefix or None)
        except RunpodRestError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        payload = [summarize_pod(pod) for pod in pods]
        if args.as_json:
            sys.stdout.write(json.dumps(redact(payload), indent=2, sort_keys=True) + "\n")
        else:
            for pod in payload:
                print(f"{pod.get('id')} {pod.get('desiredStatus')} {pod.get('name')} costPerHr={pod.get('costPerHr')}")
        return 0

    if args.command == "get-pod":
        try:
            pod = RunpodRestClient().get_pod(args.pod_id)
        except RunpodRestError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(redact(pod), indent=2, sort_keys=True) + "\n")
        else:
            summary = summarize_pod(pod)
            for key, value in summary.items():
                print(f"{key}: {value}")
        return 0

    if args.command == "gpu-catalog":
        try:
            if args.manifest:
                report = build_gpu_catalog_report_from_manifest(load_manifest(args.manifest))
            else:
                report = build_gpu_catalog_report(
                    gpu_type_ids=args.gpu_type_id,
                    data_center_ids=args.data_center_id,
                    cloud_type=args.cloud_type,
                    gpu_count=args.gpu_count,
                    network_volume_id=args.network_volume_id,
                )
        except RunpodGraphqlError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(redact(report), indent=2, sort_keys=True) + "\n")
        if args.as_json:
            sys.stdout.write(json.dumps(redact(report), indent=2, sort_keys=True) + "\n")
        else:
            summary = report["summary"]
            constraints = report["query_constraints"]
            print(f"constraints_satisfied: {str(report['constraints_satisfied']).lower()}")
            print(f"cloud_type: {constraints['cloud_type']}")
            print(f"gpu_count: {constraints['gpu_count']}")
            print(f"requested_gpu_type_ids: {constraints['gpu_type_ids']}")
            print(f"requested_data_center_ids: {constraints['data_center_ids']}")
            print(f"offered_requested_combo_count: {summary['offered_requested_combo_count']}")
            print(f"available_requested_combo_count: {summary['available_requested_combo_count']}")
            for match in report["catalog_matches"]:
                print(
                    f"{match.get('gpu_type_id')} @ {match.get('data_center_id')}: "
                    f"{match.get('reason')} stockStatus={match.get('stockStatus')}"
                )
            for recommendation in report["recommendations"]:
                print(f"RECOMMEND {recommendation}")
        return 0 if report["constraints_satisfied"] else 2

    if args.command == "runtime-metrics":
        elapsed = args.expected_elapsed_seconds
        if elapsed is None and args.expected_elapsed_minutes is not None:
            elapsed = args.expected_elapsed_minutes * 60
        try:
            report = build_runtime_metrics_report(
                args.pod_id,
                expected_elapsed_seconds=elapsed,
                previous_report=load_previous_report(args.previous),
                crash_loop_uptime_threshold_seconds=args.crash_loop_uptime_threshold_seconds,
            )
        except RunpodGraphqlError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(redact(report), indent=2, sort_keys=True) + "\n")
        if args.as_json:
            sys.stdout.write(json.dumps(redact(report), indent=2, sort_keys=True) + "\n")
        else:
            metrics = report["metrics"]
            analysis = report["analysis"]
            print(f"pod_id: {report['pod_id']}")
            print(f"state: {analysis['state']}")
            print(f"crash_loop_suspected: {str(analysis['crash_loop_suspected']).lower()}")
            print(f"uptime_seconds: {metrics.get('uptimeInSeconds')}")
            print(f"container_cpu_percent: {analysis.get('container_cpu_percent')}")
            print(f"container_memory_percent: {analysis.get('container_memory_percent')}")
            print(f"gpu_util_percent: {analysis.get('gpu_util_percent')}")
            print(f"activity_sample: {analysis.get('activity_sample')}")
            for item in analysis["evidence"]:
                print(f"EVIDENCE {item}")
            for warning in analysis["warnings"]:
                print(f"WARN  {warning}")
            for item in analysis["recommendations"]:
                print(f"RECOMMEND {item}")
        return 0 if report["ok"] else 1

    if args.command == "progress-report":
        manifest = load_manifest(args.manifest)
        previous = read_json_file(args.previous) if args.previous else None
        report = build_progress_report(
            manifest,
            args.pod_id,
            previous_report=previous,
            mode=args.mode,
            public_ip=args.public_ip,
            external_port=args.external_port,
            progress_timeout_seconds=args.progress_timeout_seconds,
        )
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(redact(report), indent=2, sort_keys=True) + "\n")
        if args.as_json:
            sys.stdout.write(json.dumps(redact(report), indent=2, sort_keys=True) + "\n")
        else:
            classification = report["classification"]
            print(f"classification.state: {classification['state']}")
            print(f"classification.workload_progressing: {str(classification['workload_progressing']).lower()}")
            print(f"classification.monitor_alive: {str(classification['monitor_alive']).lower()}")
            print(f"classification.outage_suspected: {str(classification['outage_suspected']).lower()}")
            print(f"classification.cleanup_recommended: {str(classification['cleanup_recommended']).lower()}")
            print(f"classification.next_action: {classification['next_action']}")
            for item in classification["evidence"]:
                print(f"EVIDENCE {item}")
            for warning in classification["warnings"]:
                print(f"WARN  {warning}")
        return 0 if not report["classification"].get("cleanup_recommended") else 2

    if args.command == "pod-ssh-info":
        try:
            record = runpodctl_ssh_info(args.pod_id, verbose=args.verbose)
        except RunpodCtlError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        else:
            print(record["stdout"], end="" if record["stdout"].endswith("\n") else "\n")
        return 0

    if args.command == "cleanup-pod":
        if args.execute and not args.yes_cleanup_runpod:
            print("--execute requires --yes-cleanup-runpod", file=sys.stderr)
            return 2
        try:
            record = cleanup_pod_flow(
                args.pod_id,
                out_dir=args.out_dir,
                action=args.action,
                execute=args.execute,
                wait=args.wait,
                timeout_seconds=args.timeout_seconds,
                interval_seconds=args.interval_seconds,
            )
        except (RunpodRestError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        else:
            print(f"status: {record['status']}")
            cleanup_verified = record["status"] in ("verified", "already_absent")
            print(f"cleanup_verified: {str(cleanup_verified).lower()}")
            if record["status"] == "submitted":
                print("next_action: rerun cleanup-pod with --wait or verify get-pod returns absent/terminal before reporting closeout")
            print(f"cleanup_record: {Path(args.out_dir).resolve() / 'runpod_cleanup_record.json'}")
        if record["status"] in ("dry_run_request", "verified", "already_absent"):
            return 0
        if record["status"] == "submitted":
            return 2
        return 1

    if args.command == "cost-report":
        try:
            report = cost_report_from_record(args.record, fetch_billing=args.fetch_billing)
        except RunpodRestError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            print(f"pod_id: {report['pod_id']}")
            print(f"estimate_usd: {report['estimate']['amount_usd']}")
            if report["billing"]["amount_usd"] is not None:
                print(f"billing_usd: {report['billing']['amount_usd']}")
        return 0

    if args.command == "remote-outcome":
        try:
            payload = write_remote_outcome(args.record, args.out, fetch_billing=args.fetch_billing)
        except RunpodRestError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        else:
            print(f"outcome: {payload['outcome_path']}")
            print(f"status: {payload.get('status')}")
            print(f"cleanup_status: {payload.get('cleanup_status')}")
        return 0

    if args.command == "billing-pods":
        query = {
            "podId": args.pod_id,
            "startTime": args.start_time,
            "endTime": args.end_time,
            "bucketSize": args.bucket_size,
            "grouping": args.grouping,
            "gpuId": args.gpu_id,
        }
        if args.backend == "runpodctl":
            try:
                record = runpodctl_billing_pods(query)
            except RunpodCtlError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            if args.as_json:
                sys.stdout.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
            else:
                print(record["stdout"], end="" if record["stdout"].endswith("\n") else "\n")
            return 0
        try:
            records = RunpodRestClient().billing_pods(**query)
        except RunpodRestError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(redact(records), indent=2, sort_keys=True) + "\n")
        else:
            for item in records:
                print(f"{item.get('time')} pod={item.get('podId')} amount={item.get('amount')} billed_ms={item.get('timeBilledMs')}")
        return 0

    if args.command == "billing-endpoints":
        query = {
            "endpointId": args.endpoint_id,
            "podId": args.pod_id,
            "startTime": args.start_time,
            "endTime": args.end_time,
            "bucketSize": args.bucket_size,
            "grouping": args.grouping,
            "dataCenterId": args.data_center_id,
            "gpuTypeId": args.gpu_type_id,
            "gpuId": args.gpu_id,
            "imageName": args.image_name,
            "templateId": args.template_id,
        }
        if args.backend == "runpodctl":
            try:
                record = runpodctl_billing_serverless(query)
            except RunpodCtlError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            if args.as_json:
                sys.stdout.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
            else:
                print(record["stdout"], end="" if record["stdout"].endswith("\n") else "\n")
            return 0
        try:
            records = RunpodRestClient().billing_endpoints(**query)
        except RunpodRestError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(redact(records), indent=2, sort_keys=True) + "\n")
        else:
            for item in records:
                print(
                    f"{item.get('time')} endpoint={item.get('endpointId')} pod={item.get('podId')} "
                    f"amount={item.get('amount')} billed_ms={item.get('timeBilledMs')}"
                )
        return 0

    if args.command == "billing-network-volumes":
        query = {
            "startTime": args.start_time,
            "endTime": args.end_time,
            "bucketSize": args.bucket_size,
        }
        if args.backend == "runpodctl":
            try:
                record = runpodctl_billing_network_volume(query)
            except RunpodCtlError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            if args.as_json:
                sys.stdout.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
            else:
                print(record["stdout"], end="" if record["stdout"].endswith("\n") else "\n")
            return 0
        try:
            records = RunpodRestClient().billing_network_volumes(**query)
        except RunpodRestError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(redact(records), indent=2, sort_keys=True) + "\n")
        else:
            for item in records:
                print(
                    f"{item.get('time')} amount={item.get('amount')} disk_gb={item.get('diskSpaceBilledGb')} "
                    f"high_perf_amount={item.get('highPerformanceStorageAmount')}"
                )
        return 0

    if args.command == "verify-network-volume-s3":
        manifest = load_manifest(args.manifest)
        record = verify_network_volume_s3(
            manifest,
            out_dir=args.out_dir,
            execute=args.execute,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
        )
        if args.as_json:
            sys.stdout.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        else:
            print(f"status: {record['status']}")
            print(f"ok: {str(record['ok']).lower()}")
            print(f"record: {Path(args.out_dir).resolve() / 'network_volume_s3_verify.json'}")
            for blocker in record.get("blockers", []):
                print(f"ERROR {blocker}")
            for warning in record.get("warnings", []):
                print(f"WARN  {warning}")
            for command in record.get("plan", {}).get("commands", []):
                print(command)
        return 0 if record["ok"] else 1 if args.execute else 2

    if args.command == "list-network-volumes":
        try:
            volumes = RunpodRestClient().list_network_volumes()
        except RunpodRestError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(redact(volumes), indent=2, sort_keys=True) + "\n")
        else:
            for volume in volumes:
                print(f"{volume.get('id')} {volume.get('dataCenterId')} {volume.get('size')}GB {volume.get('name')}")
        return 0

    if args.command == "get-network-volume":
        try:
            volume = RunpodRestClient().get_network_volume(args.network_volume_id)
        except RunpodRestError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(redact(volume), indent=2, sort_keys=True) + "\n")
        else:
            for key, value in volume.items():
                print(f"{key}: {value}")
        return 0

    if args.command == "list-templates":
        try:
            templates = RunpodRestClient().list_templates()
        except RunpodRestError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(redact(templates), indent=2, sort_keys=True) + "\n")
        else:
            for template in templates:
                print(f"{template.get('id')} {template.get('name')} image={template.get('imageName') or template.get('image')}")
        return 0

    if args.command == "get-template":
        try:
            template = RunpodRestClient().get_template(args.template_id)
        except RunpodRestError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(redact(template), indent=2, sort_keys=True) + "\n")
        else:
            for key, value in template.items():
                print(f"{key}: {value}")
        return 0

    if args.command == "dashboard":
        records = scan_dashboard_records(args.scan_dir)
        result = write_dashboard(records, args.out)
        if args.as_json:
            sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        else:
            print(f"html: {result['html']}")
            print(f"json: {result['json']}")
            print(f"records: {result['records']}")
        return 0

    if args.command == "recover-run":
        if args.execute_cleanup and not args.yes_cleanup_runpod:
            print("--execute-cleanup requires --yes-cleanup-runpod", file=sys.stderr)
            return 2
        try:
            if args.execute_cleanup:
                result = recover_run(
                    args.record,
                    execute_cleanup=True,
                    cleanup_action=args.action,
                    out_dir=args.out_dir,
                )
            else:
                result = {"analysis": analyze_recovery(args.record)}
        except (RunpodRestError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.as_json:
            sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        else:
            analysis = result["analysis"]
            print(f"status: {analysis['status']}")
            print(f"risk: {analysis['risk']}")
            print(f"actions: {', '.join(analysis['actions'])}")
        return 0

    if args.command == "fetch-proxy-file":
        result = fetch_proxy_file(args.pod_id, args.port, args.remote_path, args.out, timeout_seconds=args.timeout_seconds)
        if args.as_json:
            sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        else:
            print(f"ok: {str(result['ok']).lower()}")
            print(f"url: {result['url']}")
            print(f"output_path: {result['output_path']}")
            if not result["ok"]:
                print(f"error: {result.get('error', '')}")
        return 0 if result["ok"] else 1

    if args.command == "verify-proxy-packet":
        manifest = load_manifest(args.manifest)
        result = verify_proxy_packet(
            manifest,
            args.pod_id,
            port=args.port,
            out_dir=args.out_dir,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
        )
        if args.as_json:
            sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        else:
            print(f"ok: {str(result['ok']).lower()}")
            print(f"base_url: {result['base_url']}")
            print(f"closeout_status: {result['closeout']['status']}")
            print(f"artifacts: {len(result['closeout']['artifacts'])}")
        return 0 if result["ok"] else 1

    if args.command == "fetch-tcp-file":
        result = fetch_tcp_file(args.host, args.external_port, args.remote_path, args.out, timeout_seconds=args.timeout_seconds)
        if args.as_json:
            sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        else:
            print(f"ok: {str(result['ok']).lower()}")
            print(f"url: {result['url']}")
            print(f"output_path: {result['output_path']}")
            if not result["ok"]:
                print(f"error: {result.get('error', '')}")
        return 0 if result["ok"] else 1

    if args.command == "verify-tcp-packet":
        manifest = load_manifest(args.manifest)
        api = RunpodRestClient()
        deadline = time.monotonic() + args.timeout_seconds
        pod = {}
        try:
            while time.monotonic() <= deadline:
                pod = api.get_pod(args.pod_id)
                if tcp_endpoint_from_pod(pod, args.port):
                    break
                time.sleep(args.interval_seconds)
        except RunpodRestError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        remaining = max(1, int(deadline - time.monotonic()))
        result = verify_tcp_packet(
            manifest,
            pod,
            internal_port=args.port,
            out_dir=args.out_dir,
            timeout_seconds=remaining,
            interval_seconds=args.interval_seconds,
        )
        if args.as_json:
            sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        else:
            print(f"ok: {str(result['ok']).lower()}")
            print(f"base_url: {result['base_url']}")
            print(f"closeout_status: {result['closeout']['status']}")
            print(f"artifacts: {len(result['closeout']['artifacts'])}")
            if not result["ok"] and result.get("error"):
                print(f"error: {result['error']}")
        return 0 if result["ok"] else 1

    return 1


def run_learnings(args) -> int:
    action = args.learnings_action

    if action == "record":
        entry = record_learning(
            provider=args.provider,
            symptom=args.symptom,
            category=args.category,
            severity=args.severity,
            status=args.status,
            context=args.context,
            resolution=args.resolution,
            evidence=args.evidence,
            tags=args.tags,
        )
        safety = ledger_safety_warning()
        if safety:
            print(f"warning: {safety}", file=sys.stderr)
        if args.as_json:
            sys.stdout.write(json.dumps(entry, indent=2, sort_keys=True) + "\n")
        else:
            print(f"recorded {entry['id']} [{entry['severity']}/{entry['status']}] {entry['provider']}: {entry['symptom']}")
            if entry.get("scrub_warning"):
                print(f"  scrub-warning: {', '.join(entry['scrub_warning'])} (excluded from promotion until cleaned)")
        return 0

    if action in {"list", "search"}:
        entries = read_entries()
        results = search_learnings(
            entries,
            provider=args.provider,
            query=getattr(args, "query", None),
            category=args.category,
            severity=args.severity,
            status=args.status,
            tag=args.tag,
        )
        results = results[: max(args.limit, 0)]
        if args.as_json:
            sys.stdout.write(json.dumps(results, indent=2, sort_keys=True) + "\n")
        else:
            if not results:
                print("no matching learnings")
            for record in results:
                flag = " (promoted)" if record.get("promoted") else ""
                warn = " !scrub" if record.get("scrub_warning") else ""
                print(f"{record.get('id','?')} {record.get('ts','?')} [{record.get('severity','?')}/{record.get('status','?')}] {record.get('provider','unknown')}/{record.get('category','other')}{flag}{warn}")
                print(f"  {record.get('symptom','')}")
                if record.get("resolution"):
                    print(f"  fix: {record['resolution']}")
        return 0

    if action == "brief":
        entries = read_entries()
        # Enforce "record before you escalate": auto-log an open learning unless one
        # already covers this symptom (or --no-record was passed).
        auto_recorded = None
        if not args.no_record:
            existing = search_learnings(entries, provider=args.provider, query=args.symptom)
            if not existing:
                auto_recorded = record_learning(
                    provider=args.provider,
                    symptom=args.symptom,
                    status="open",
                    context="auto-recorded at research-brief time",
                    evidence=args.failing_invocation,
                )
                entries = read_entries()
        adapter_status = None
        try:
            adapter_status = get_adapter(args.provider).capabilities()
        except KeyError:
            adapter_status = None
        brief = build_research_brief(
            provider=args.provider,
            symptom=args.symptom,
            entries=entries,
            adapter_status=adapter_status,
            failing_invocation=args.failing_invocation,
        )
        if args.as_json:
            sys.stdout.write(json.dumps(brief, indent=2, sort_keys=True) + "\n")
        else:
            print(f"research brief: {brief['provider']} / {brief['symptom']}")
            if auto_recorded:
                print(f"auto-recorded open learning {auto_recorded['id']} (record the fix when found)")
            if brief.get("failing_invocation"):
                print(f"failing invocation: {brief['failing_invocation']}")
            print(f"prior learnings: {len(brief['prior_learnings'])} (related total {brief['related_learnings_count']})")
            for record in brief["prior_learnings"]:
                print(f"  - {record['id']} [{record['status']}] {record['symptom']}" + (f" -> {record['resolution']}" if record.get("resolution") else ""))
            if brief["provider_known_patterns"]:
                print("known_patterns:")
                for pattern in brief["provider_known_patterns"]:
                    print(f"  - {pattern}")
            if brief["learnings_doc"]:
                print(f"learnings_doc: {brief['learnings_doc']}")
            for link in brief["doc_links"]:
                print(f"  doc: {link}")
            print("suggested searches:")
            for query in brief["suggested_search_queries"]:
                print(f"  - {query}")
            print()
            print(brief["agent_instruction"])
        return 0

    if action == "promote":
        entries = read_entries()
        if args.mark:
            try:
                marker = mark_promoted(args.mark)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            if args.as_json:
                sys.stdout.write(json.dumps(marker, indent=2, sort_keys=True) + "\n")
            else:
                print(f"marked {args.mark} promoted")
            return 0
        candidates = promotion_candidates(entries)
        if args.provider:
            candidates = [c for c in candidates if c.get("provider") == args.provider]
        if args.as_json:
            payload = [
                {
                    "id": c.get("id", "?"),
                    "provider": c.get("provider", "unknown"),
                    "provider_entry_file": provider_entry_file(c.get("provider", "unknown")),
                    "bullet": promotion_bullet(c),
                }
                for c in candidates
            ]
            sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        else:
            if not candidates:
                print("no scrub-clean resolved learnings awaiting promotion")
            for candidate in candidates:
                cid = candidate.get("id", "?")
                print(f"{cid} -> {provider_entry_file(candidate.get('provider', 'unknown'))}")
                print(f"  known_patterns bullet: {promotion_bullet(candidate)}")
                print(f"  (after editing the provider entry, run: learnings promote --mark {cid})")
        return 0

    if action == "stats":
        report = learning_stats(read_entries())
        report["corrupt_lines"] = learning_corrupt_lines()
        if args.as_json:
            sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            print(f"total: {report['total']}  open: {report['open']}  resolved: {report['resolved']}  promoted: {report['promoted']}  promotable: {report['promotable']}  scrub-warned: {report['with_scrub_warning']}  corrupt-lines: {report['corrupt_lines']}")
            for label, key in (("provider", "by_provider"), ("category", "by_category"), ("severity", "by_severity")):
                if report[key]:
                    rendered = ", ".join(f"{name}:{count}" for name, count in report[key].items())
                    print(f"by {label}: {rendered}")
        return 0

    return 1


def print_validation(result, stream=sys.stdout) -> None:
    if result.ok:
        print("manifest valid", file=stream)
    else:
        print("manifest invalid", file=stream)
    for issue in result.errors:
        print(f"ERROR {issue.path}: {issue.message}", file=stream)
    for issue in result.warnings:
        print(f"WARN  {issue.path}: {issue.message}", file=stream)


def print_plan(plan: dict) -> None:
    print(f"run_id: {plan.get('run_id')}")
    print(f"provider: {plan.get('provider')} ({plan.get('adapter')})")
    print(f"task_scale: {plan.get('task_scale')}")
    print(f"remote_ready: {str(plan.get('remote_ready')).lower()}")
    if plan.get("blockers"):
        print("blockers:")
        for blocker in plan["blockers"]:
            print(f"  - {blocker}")
    if plan.get("warnings"):
        print("warnings:")
        for warning in plan["warnings"]:
            print(f"  - {warning}")


def write_aws_helper_files(plan: dict[str, object], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    features = plan.get("features", {})
    if not isinstance(features, dict):
        return
    index: dict[str, list[str]] = {}
    for feature_name, feature in features.items():
        if not isinstance(feature, dict):
            continue
        helper_files = feature.get("helper_files", {})
        if not isinstance(helper_files, dict):
            continue
        written: list[str] = []
        for filename, payload in helper_files.items():
            path = out_dir / str(filename)
            path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            written.append(str(path))
        if written:
            index[str(feature_name)] = written
    if index:
        (out_dir / "aws-orchestrator-helper-index.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")


def print_manifest_audit_summary(report: dict[str, object], *, limit: int = 8) -> None:
    issue_summary = report.get("issue_summary")
    if not isinstance(issue_summary, dict):
        return
    print_issue_buckets("top_errors", issue_summary.get("errors"), limit=limit, message_key="message")
    print_issue_buckets("top_warnings", issue_summary.get("warnings"), limit=limit, message_key="message")
    print_issue_buckets("top_migration_hints", issue_summary.get("migration_hints"), limit=limit, message_key="hint")


def print_issue_buckets(label: str, buckets: object, *, limit: int, message_key: str) -> None:
    if not isinstance(buckets, list) or not buckets:
        return
    print(f"{label}:")
    for bucket in buckets[:limit]:
        if not isinstance(bucket, dict):
            continue
        count = bucket.get("count")
        message = bucket.get(message_key)
        path = bucket.get("path")
        prefix = f"  - {count}x"
        if path:
            print(f"{prefix} {path}: {message}")
        else:
            print(f"{prefix} {message}")


def print_manifest_audit_failure_paths(report: dict[str, object]) -> None:
    results = report.get("results")
    if not isinstance(results, list):
        return
    failures = [item for item in results if isinstance(item, dict) and not item.get("ok")]
    if not failures:
        return
    print("failure_paths:")
    for item in failures:
        print(f"  - {item.get('path')}")


def print_runpod_ops_summary(report: dict[str, object], *, limit: int = 8) -> None:
    buckets = report.get("finding_summary")
    if not isinstance(buckets, list) or not buckets:
        return
    print("top_findings:")
    for bucket in buckets[:limit]:
        if not isinstance(bucket, dict):
            continue
        print(f"  - {bucket.get('count')}x {bucket.get('severity')} {bucket.get('rule')}: {bucket.get('message')}")


def read_json_file(path: str | Path) -> dict[str, object]:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
