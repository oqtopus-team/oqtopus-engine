from abc import ABC, abstractmethod

from .context import GlobalContext


class DeviceFetcher(ABC):
    """Abstract base class for periodically fetching device data."""

    def __init__(self) -> None:
        self._gctx: GlobalContext | None = None

    @property
    def gctx(self) -> GlobalContext | None:
        """Get the current global context.

        Returns:
            The GlobalContext instance or None.

        """
        return self._gctx

    @gctx.setter
    def gctx(self, gctx: GlobalContext) -> None:
        """Set the global context shared across fetchers.

        Args:
            gctx: Shared GlobalContext.

        """
        self._gctx = gctx

    @abstractmethod
    async def start(self) -> None:
        """Start the device fetch loop.

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = "`start` must be implemented in subclasses of DeviceFetcher."
        raise NotImplementedError(message)
