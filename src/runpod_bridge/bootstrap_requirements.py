from __future__ import annotations

from typing import Any


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

    if source != "git_remote_or_snapshot":
        return {
            "ok": True,
            "source": source,
            "requires_git": False,
            "git_available_declared": False,
            "errors": [],
            "warnings": [],
            "recommendations": [],
        }

    git_declared = image_declares_git(runpod, bootstrap)
    if not git_declared:
        issue = {
            "severity": "error" if remote_launch_allowed else "warning",
            "path": "runpod.image_capabilities",
            "message": (
                "repo.source=git_remote_or_snapshot requires git inside the pod before startup.commands run; "
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

    image_name = str(runpod.get("imageName") or "")
    if source == "git_remote_or_snapshot" and "pytorch/pytorch:" in image_name and not git_declared:
        recommendations.append(
            "public pytorch/pytorch runtime/devel images may omit git; verify the exact image or bake a tiny derived image with git installed"
        )

    return {
        "ok": not errors,
        "source": source,
        "requires_git": True,
        "git_available_declared": git_declared,
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
