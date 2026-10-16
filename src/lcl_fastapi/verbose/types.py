"""Immutable public runtime file-diagnostic state and process observations."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class FileVerboseState:
    """Describe the accepted target for one complete service start.

    :param enabled: Whether new executions should produce file diagnostics.
    :param revision: Nonnegative, unitless revision within this service start.
    """

    enabled: bool
    revision: int

    def __post_init__(self) -> None:
        """Reject coercions and negative revisions.

        :raises ValueError: If state values violate their public contracts.
        """
        if type(self.enabled) is not bool or type(self.revision) is not int or self.revision < 0:
            raise ValueError("file verbose state requires a Boolean and a nonnegative revision")


@dataclass(frozen=True, slots=True)
class FileVerboseProcess:
    """Report one identity-verified process's latest successful observation.

    :param role: Controller or API process; API processes also own Worker threads.
    :param pid: Operating-system process ID.
    :param enabled: Value observed for new executions, not already running work.
    :param revision: Observed unitless target revision.
    :param observed_at: Unix observation time in seconds; callers can assess age.
    """

    role: Literal["controller", "api"]
    pid: int
    enabled: bool
    revision: int
    observed_at: float


@dataclass(frozen=True, slots=True)
class FileVerboseStatus(FileVerboseState):
    """Combine the current target and eventually consistent live observations.

    :param enabled: Accepted target for new executions.
    :param revision: Accepted unitless revision.
    :param processes: Live matching process observations, ordered by PID.
    """

    processes: tuple[FileVerboseProcess, ...]
