"""L5 Identity — self-model, capabilities, and identity updater.

Public API::

    from agent.memory.identity import (
        IdentityStore,
        SelfModel,
        CapabilityModel,
        IdentityUpdater,
    )
"""

from .identity_store import IdentityStore
from .self_model import SelfModel, WindowStats
from .capability_model import CapabilityModel
from .updater import IdentityUpdater, StatisticsUpdateResult, RedesignResult

__all__ = [
    "IdentityStore",
    "SelfModel",
    "WindowStats",
    "CapabilityModel",
    "IdentityUpdater",
    "StatisticsUpdateResult",
    "RedesignResult",
]