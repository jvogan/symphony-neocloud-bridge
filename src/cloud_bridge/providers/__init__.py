"""Provider adapters for the Symphony cloud bridge.

Provider entries share the same portable contract. Some entries have automated
launch support; setup-only entries still provide guidance but reject paid launch.
"""
from __future__ import annotations

from .capabilities import AWS_DOCS, RUNPOD_DOCS, provider_capabilities
from .base import PORTABLE_CONTRACT, ProviderAdapter, ProviderLaunchUnsupported
from .registry import adapter_names, available_adapters, get_adapter

__all__ = [
    "AWS_DOCS",
    "RUNPOD_DOCS",
    "provider_capabilities",
    "PORTABLE_CONTRACT",
    "ProviderAdapter",
    "ProviderLaunchUnsupported",
    "adapter_names",
    "available_adapters",
    "get_adapter",
]
