"""Provider adapter registry.

Single source of truth for compute-provider setup guidance and whether a given
provider path has automated launch support. The CLI uses this to introspect
providers and gate paid/mutating work behind ``assert_launch_supported``.
"""
from __future__ import annotations

from cloud_bridge.providers.aws.adapter import AwsAdapter
from cloud_bridge.providers.base import ProviderAdapter
from cloud_bridge.providers.beam.adapter import BeamAdapter
from cloud_bridge.providers.boltz.adapter import BoltzAdapter
from cloud_bridge.providers.esm.adapter import EsmAdapter
from cloud_bridge.providers.fal.adapter import FalAdapter
from cloud_bridge.providers.gcp.adapter import GcpAdapter
from cloud_bridge.providers.huggingface.adapter import HuggingfaceAdapter
from cloud_bridge.providers.kaggle.adapter import KaggleAdapter
from cloud_bridge.providers.lambda_cloud.adapter import LambdaCloudAdapter
from cloud_bridge.providers.modal.adapter import ModalAdapter
from cloud_bridge.providers.nvidia_nim.adapter import NvidiaNimAdapter
from cloud_bridge.providers.replicate.adapter import ReplicateAdapter
from cloud_bridge.providers.runpod.adapter import RunpodAdapter
from cloud_bridge.providers.together.adapter import TogetherAdapter

_ADAPTERS: dict[str, ProviderAdapter] = {}


def register(adapter: ProviderAdapter) -> None:
    _ADAPTERS[adapter.name] = adapter


# Provider entries split into compute_rental (RunPod/Modal/Lambda/AWS/Beam),
# managed_inference (Boltz/ESM/NVIDIA-NIM/HuggingFace/Replicate/fal/Together),
# and notebook_job (HuggingFace/Kaggle/GCP). See docs/provider-adapter-contract.md
# "Provider Categories".
for _adapter in (
    RunpodAdapter(),
    ModalAdapter(),
    LambdaCloudAdapter(),
    AwsAdapter(),
    BoltzAdapter(),
    EsmAdapter(),
    NvidiaNimAdapter(),
    HuggingfaceAdapter(),
    ReplicateAdapter(),
    FalAdapter(),
    TogetherAdapter(),
    BeamAdapter(),
    KaggleAdapter(),
    GcpAdapter(),
):
    register(_adapter)


def get_adapter(name: str) -> ProviderAdapter:
    try:
        return _ADAPTERS[name]
    except KeyError:
        raise KeyError(f"unknown provider {name!r}; known providers: {', '.join(sorted(_ADAPTERS))}")


def available_adapters() -> list[ProviderAdapter]:
    return [_ADAPTERS[name] for name in sorted(_ADAPTERS)]


def adapter_names() -> list[str]:
    return sorted(_ADAPTERS)
