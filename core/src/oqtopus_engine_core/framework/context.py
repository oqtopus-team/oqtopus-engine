from __future__ import annotations

from collections import UserDict
from typing import Any

from pydantic import BaseModel, ConfigDict

from .device_repository import DeviceRepository  # noqa: TC001
from .job_repository import JobRepository  # noqa: TC001
from .model import Device  # noqa: TC001


class GlobalContext(BaseModel):
    """A context shared across all jobs and steps."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    config: dict[str, Any]
    device: Device | None = None
    job_repository: JobRepository | None = None
    device_repository: DeviceRepository | None = None


class JobContext(UserDict):
    """Job-related context with attribute-style access.

    This is a dictionary-like object used to store job-specific information.
    It supports accessing keys as attributes, enabling convenient syntax.
    """

    def __init__(self, initial: dict | None = None, **kwargs: Any) -> None:  # noqa: ANN401
        """Initialize the JobContext with an optional initial dictionary.

        Args:
            initial: Initial key-value pairs to populate the context.
            **kwargs: Additional key-value pairs to populate the context.

        """
        super().__init__(initial or {}, **kwargs)

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        """Retrieve an attribute from the internal data store.

        Args:
            name: The name of the attribute/key to retrieve.

        Returns:
            The value associated with the given key.

        Raises:
            AttributeError: If the key is not found in the data store.

        """
        if name in self.data:
            return self.data[name]
        message = f"{self.__class__.__name__!r} object has no attribute {name!r}"
        raise AttributeError(message)

    def __setattr__(self, name: str, value: Any) -> None:  # noqa: ANN401
        """Set an attribute in the internal data store.

        Args:
            name: The name of the attribute/key to set.
            value: The value to associate with the given key.

        """
        if name == "data":
            super().__setattr__(name, value)
        else:
            self.data[name] = value

    def __delattr__(self, name: str) -> None:
        """Delete an attribute from the internal data store.

        Args:
            name: The name of the attribute/key to delete.

        Raises:
            AttributeError: If the key is not found in the data store.

        """
        if name in self.data:
            del self.data[name]
        else:
            message = f"{self.__class__.__name__!r} object has no attribute {name!r}"
            raise AttributeError(message)
