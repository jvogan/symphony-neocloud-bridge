from __future__ import annotations

from typing import Any

from .launch_env import git_source_aliases


def bootstrap_requirements_report(manifest: dict[str, Any]) -> dict[str, Any]:
    repo = manifest.get("repo", {}) if isinstance(manifest.get("repo"), dict) else {}
    runpod = manifest.get("runpod", {}) if isinstance(manifest.get("runpod"), dict) else {}
    startup = manifest.get("startup", {}) if isinstance(manifest.get("startup"), dict) else {}
    bootstrap = startup.get("bootstrap", {}) if isinstance(startup.get("bootstrap"), dict) else {}
    remote_launch_allowed = manifest.get("remote_launch_allowed") is True
    source = str(repo.get("source") or "")
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    recommendations: list[str] = []
    image_name = str(runpod.get("imageName") or "")
    requires_git = source in git_source_aliases()

    git_declared = image_declares_git(runpod, bootstrap) if requires_git else False
    if requires_git and not git_declared:
        issue = {
            "severity": "error" if remote_launch_allowed else "warning",
            "path": "runpod.image_capabilities",
            "message": (
                "repo.source=git_remote requires git inside the pod before startup.commands run; "
                "declare runpod.image_capabilities including git, use a template/image known to include git, "
                "or switch to inline_commands/snapshot/object-store bootstrap"
            ),
        }
        if remote_launch_allowed:
            errors.append(issue)
        else:
            warnings.append(issue)
        recommendations.append(
            "run a tiny image-native bootstrap canary before paid science work: command -v git && git --version && git ls-remote <repo> <sha>"
        )

    if requires_git and "pytorch/pytorch:" in image_name and not git_declared:
        recommendations.append(
            "public pytorch/pytorch runtime/devel images may omit git; verify the exact image or bake a tiny derived image with git installed"
        )

    private_registry = likely_private_registry_image(image_name)
    registry_auth_declared = image_registry_auth_declared(runpod)
    if private_registry and not registry_auth_declared:
        issue = {
            "severity": "error" if remote_launch_allowed else "warning",
            "path": "runpod.containerRegistryAuthId",
            "message": (
                "likely private registry image requires provider-side registry auth or explicit image_pull_verified before paid launch; "
                "do not rely on local Docker credentials from the worker"
            ),
        }
        if remote_launch_allowed:
            errors.append(issue)
        else:
            warnings.append(issue)
        recommendations.append(
            "prove exact image pullability with a tiny image-native canary or configure RunPod registry auth before launching the workload"
        )

    return {
        "ok": not errors,
        "source": source,
        "requires_git": requires_git,
        "git_available_declared": git_declared,
        "likely_private_registry_image": private_registry,
        "registry_auth_declared": registry_auth_declared,
        "errors": errors,
        "warnings": warnings,
        "recommendations": recommendations,
    }


def image_declares_git(runpod: dict[str, Any], bootstrap: dict[str, Any]) -> bool:
    if bootstrap.get("image_has_git") is True or bootstrap.get("git_available") is True:
        return True
    for key in ("image_capabilities", "imageCapabilities"):
        value = runpod.get(key)
        if isinstance(value, list) and any(str(item).lower() == "git" for item in value):
            return True
    return False


def likely_private_registry_image(image_name: str) -> bool:
    lower = image_name.lower()
    if not lower or "/" not in lower:
        return False
    if lower.startswith("public.ecr.aws/"):
        return False
    return (
        lower.startswith("ghcr.io/")
        or ".dkr.ecr." in lower
        or lower.startswith("registry.gitlab.com/")
        or lower.startswith("gcr.io/")
        or lower.startswith("us-docker.pkg.dev/")
        or lower.startswith("europe-docker.pkg.dev/")
        or lower.startswith("asia-docker.pkg.dev/")
    )


def image_registry_auth_declared(runpod: dict[str, Any]) -> bool:
    if runpod.get("image_pull_verified") is True or runpod.get("imagePullVerified") is True:
        return True
    for key in (
        "containerRegistryAuthId",
        "containerRegistryAuthId_ref",
        "containerRegistryAuthRef",
        "registry_auth_ref",
        "registryAuthRef",
    ):
        value = runpod.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False
