from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from .runtime import DEFAULT_GRAPHQL_URL, RunpodGraphqlClient


GPU_TYPES_QUERY = """
query GpuTypes($filter: GpuTypeFilter, $gpuCount: Int!, $secureCloud: Boolean!) {
  gpuTypes(input: $filter) {
    id
    displayName
    nodeGroupDatacenters {
      id
      name
      gpuAvailability(input: {gpuCount: $gpuCount, secureCloud: $secureCloud}) {
        available
        stockStatus
      }
    }
  }
}
""".strip()


def build_gpu_catalog_report(
    *,
    gpu_type_ids: list[str] | None = None,
    data_center_ids: list[str] | None = None,
    cloud_type: str = "SECURE",
    gpu_count: int = 1,
    network_volume_id: str = "",
    client: RunpodGraphqlClient | None = None,
) -> dict[str, Any]:
    requested_gpu_type_ids = clean_strings(gpu_type_ids or [])
    requested_data_center_ids = clean_strings(data_center_ids or [])
    normalized_cloud = "COMMUNITY" if str(cloud_type).upper() == "COMMUNITY" else "SECURE"
    normalized_gpu_count = max(1, int_or_default(gpu_count, 1))
    variables = {
        "filter": {"ids": requested_gpu_type_ids} if requested_gpu_type_ids else None,
        "gpuCount": normalized_gpu_count,
        "secureCloud": normalized_cloud == "SECURE",
    }
    api = client or RunpodGraphqlClient()
    payload = api.request(GPU_TYPES_QUERY, variables)
    gpu_types = normalize_gpu_types(payload)
    matches = build_requested_matches(gpu_types, requested_gpu_type_ids, requested_data_center_ids)
    summary = summarize_matches(matches, requested_gpu_type_ids, requested_data_center_ids)
    recommendations = build_recommendations(
        requested_gpu_type_ids=requested_gpu_type_ids,
        requested_data_center_ids=requested_data_center_ids,
        summary=summary,
        network_volume_id=network_volume_id,
    )
    return {
        "ok": True,
        "source": "runpod_graphql_gpu_types",
        "source_url": DEFAULT_GRAPHQL_URL,
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "query_constraints": {
            "gpu_type_ids": requested_gpu_type_ids,
            "data_center_ids": requested_data_center_ids,
            "cloud_type": normalized_cloud,
            "gpu_count": normalized_gpu_count,
            "network_volume_id": network_volume_id,
        },
        "constraints_satisfied": not bool(gpu_catalog_launch_blockers_from_summary(summary)),
        "summary": summary,
        "catalog_matches": matches,
        "gpu_types": gpu_types,
        "recommendations": recommendations,
    }


def build_gpu_catalog_report_from_manifest(
    manifest: dict[str, Any],
    *,
    client: RunpodGraphqlClient | None = None,
) -> dict[str, Any]:
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    gpu_type_ids = runpod.get("gpuTypeIds") if isinstance(runpod.get("gpuTypeIds"), list) else []
    data_center_ids = runpod.get("dataCenterIds") if isinstance(runpod.get("dataCenterIds"), list) else []
    return build_gpu_catalog_report(
        gpu_type_ids=[str(item) for item in gpu_type_ids if item],
        data_center_ids=[str(item) for item in data_center_ids if item],
        cloud_type=str(runpod.get("cloudType") or "SECURE"),
        gpu_count=int_or_default(runpod.get("gpuCount"), 1),
        network_volume_id=str(runpod.get("networkVolumeId") or ""),
        client=client,
    )


def should_check_gpu_catalog(manifest: dict[str, Any]) -> bool:
    # Operator override: when the bridge's GraphQL gpuTypes catalog returns stale or empty
    # results that disagree with the REST API's actual inventory, the pre-flight blocker
    # becomes a false-positive. Setting RUNPOD_BRIDGE_SKIP_CATALOG_CHECK=1 skips the gate
    # so the REST create call can decide for itself ("no instances" vs success).
    if os.environ.get("RUNPOD_BRIDGE_SKIP_CATALOG_CHECK", "").lower() in {"1", "true", "yes"}:
        return False
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    gpu_count = int_or_default(runpod.get("gpuCount"), 0)
    gpu_type_ids = runpod.get("gpuTypeIds") if isinstance(runpod.get("gpuTypeIds"), list) else []
    return bool(gpu_count > 0 and gpu_type_ids)


def gpu_catalog_launch_blockers(report: dict[str, Any]) -> list[str]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return gpu_catalog_launch_blockers_from_summary(summary)


def gpu_catalog_launch_blockers_from_summary(summary: dict[str, Any]) -> list[str]:
    requested = int_or_default(summary.get("requested_gpu_type_count"), 0)
    offered = int_or_default(summary.get("offered_requested_combo_count"), 0)
    if requested > 0 and offered == 0:
        return [
            "runpod.gpuTypeIds/dataCenterIds: RunPod GraphQL gpuTypes found no offered GPU/DC combo for this request; REST 'no instances' would be catalog mismatch, not capacity"
        ]
    return []


def normalize_gpu_types(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
    raw_gpu_types = data.get("gpuTypes", []) if isinstance(data.get("gpuTypes"), list) else []
    normalized: list[dict[str, Any]] = []
    for gpu in raw_gpu_types:
        if not isinstance(gpu, dict):
            continue
        datacenters = []
        for dc in gpu.get("nodeGroupDatacenters", []) if isinstance(gpu.get("nodeGroupDatacenters"), list) else []:
            if not isinstance(dc, dict):
                continue
            availability = dc.get("gpuAvailability", {}) if isinstance(dc.get("gpuAvailability"), dict) else {}
            datacenters.append(
                {
                    "id": dc.get("id"),
                    "name": dc.get("name"),
                    "available": bool(availability.get("available")),
                    "stockStatus": availability.get("stockStatus"),
                }
            )
        normalized.append(
            {
                "id": gpu.get("id"),
                "displayName": gpu.get("displayName"),
                "datacenters": datacenters,
            }
        )
    return normalized


def build_requested_matches(
    gpu_types: list[dict[str, Any]],
    requested_gpu_type_ids: list[str],
    requested_data_center_ids: list[str],
) -> list[dict[str, Any]]:
    if not requested_gpu_type_ids:
        return []
    by_id = {str(gpu.get("id")): gpu for gpu in gpu_types if gpu.get("id")}
    matches: list[dict[str, Any]] = []
    for gpu_type_id in requested_gpu_type_ids:
        gpu = by_id.get(gpu_type_id)
        if not gpu:
            matches.append(
                {
                    "gpu_type_id": gpu_type_id,
                    "data_center_id": None,
                    "offered": False,
                    "available": False,
                    "stockStatus": None,
                    "reason": "gpu_type_not_returned_by_graphql",
                }
            )
            continue
        datacenters = gpu.get("datacenters", []) if isinstance(gpu.get("datacenters"), list) else []
        if requested_data_center_ids:
            selected = [dc for dc in datacenters if str(dc.get("id")) in requested_data_center_ids or str(dc.get("name")) in requested_data_center_ids]
            if not selected:
                for data_center_id in requested_data_center_ids:
                    matches.append(
                        {
                            "gpu_type_id": gpu_type_id,
                            "data_center_id": data_center_id,
                            "offered": False,
                            "available": False,
                            "stockStatus": None,
                            "reason": "gpu_type_not_offered_in_requested_data_center",
                        }
                    )
            else:
                for dc in selected:
                    matches.append(match_for_datacenter(gpu_type_id, dc))
        else:
            for dc in datacenters:
                matches.append(match_for_datacenter(gpu_type_id, dc))
    return matches


def match_for_datacenter(gpu_type_id: str, dc: dict[str, Any]) -> dict[str, Any]:
    available = bool(dc.get("available"))
    return {
        "gpu_type_id": gpu_type_id,
        "data_center_id": dc.get("id"),
        "data_center_name": dc.get("name"),
        "offered": True,
        "available": available,
        "stockStatus": dc.get("stockStatus"),
        "reason": "available" if available else "offered_but_no_current_capacity",
    }


def summarize_matches(
    matches: list[dict[str, Any]],
    requested_gpu_type_ids: list[str],
    requested_data_center_ids: list[str],
) -> dict[str, Any]:
    missing_gpu_type_ids = sorted({str(match.get("gpu_type_id")) for match in matches if match.get("reason") == "gpu_type_not_returned_by_graphql"})
    not_offered = [match for match in matches if match.get("reason") == "gpu_type_not_offered_in_requested_data_center"]
    offered = [match for match in matches if match.get("offered")]
    available = [match for match in offered if match.get("available")]
    return {
        "requested_gpu_type_count": len(requested_gpu_type_ids),
        "requested_data_center_count": len(requested_data_center_ids),
        "requested_combo_count": len(matches),
        "offered_requested_combo_count": len(offered),
        "available_requested_combo_count": len(available),
        "missing_gpu_type_ids": missing_gpu_type_ids,
        "not_offered_requested_combos": [
            {"gpu_type_id": match.get("gpu_type_id"), "data_center_id": match.get("data_center_id")}
            for match in not_offered
        ],
        "available_requested_combos": [
            {"gpu_type_id": match.get("gpu_type_id"), "data_center_id": match.get("data_center_id"), "stockStatus": match.get("stockStatus")}
            for match in available
        ],
    }


def build_recommendations(
    *,
    requested_gpu_type_ids: list[str],
    requested_data_center_ids: list[str],
    summary: dict[str, Any],
    network_volume_id: str,
) -> list[str]:
    recommendations: list[str] = []
    if not requested_gpu_type_ids:
        recommendations.append("GPU launch has no explicit gpuTypeIds; pin a deliberate GPU list before paid retries so the scheduler does not choose expensive oversubscribed cards")
    if not requested_data_center_ids:
        recommendations.append("No dataCenterIds were supplied; catalog probe can validate GPU IDs globally but cannot distinguish wrong-DC catalog mismatch from capacity")
    if gpu_catalog_launch_blockers_from_summary(summary):
        recommendations.append("Do not retry REST create with this exact GPU/DC request; change gpuTypeIds or dataCenterIds first")
    elif summary.get("offered_requested_combo_count") and not summary.get("available_requested_combo_count"):
        recommendations.append("Requested GPU/DC combos are in catalog but show no current capacity; this is a capacity retry or fallback decision, not a catalog mismatch")
    if network_volume_id:
        recommendations.append("GraphQL GPU availability does not prove the network-volume-attached machine subset has capacity; if no-volume succeeds and with-volume fails, treat it as an NV-bound capacity intersection")
    return recommendations


def clean_strings(values: list[str]) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()]


def int_or_default(value: Any, default: int) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default
