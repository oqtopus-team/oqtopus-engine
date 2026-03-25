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

    JobContext behaves like a mutable mapping for storing job-related execution
    state. In addition to storing arbitrary key-value pairs, it optionally
    supports a simple hierarchical structure through the `parent` and
    `children` attributes.

    The hierarchy is general-purpose and does not impose any particular
    semantics. It can be used by higher-level components to build nested or
    tree-structured execution contexts.
    """

    def __init__(
        self,
        initial: dict | None = None,
        *,
        parent: JobContext | None = None,
        children: list[JobContext] | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        """Initialize a JobContext.

        Args:
            initial:
                Optional initial key-value pairs for the context. If None, an
                empty mapping is created.

            parent:
                Optional parent context. Stored as an attribute and not as
                part of the mapping.

            children:
                Optional list of child contexts. Stored as an attribute and not
                as part of the mapping. A new list is created if None is given.

            **kwargs:
                Additional key-value pairs to be inserted into the mapping.

        """
        super().__init__(initial or {}, **kwargs)

        # parent/children are maintained as attributes, not inside the mapping
        super().__setattr__("parent", parent)
        super().__setattr__("children", children or [])

        # ------------------------------------------------------------
        # Lightweight step history for debugging.
        # The pipeline can append tuples: (step_phase, cursor)
        # ------------------------------------------------------------
        self.data.setdefault("step_history", [])

    # ------------------------------------------------------------------
    # attribute-style access
    # ------------------------------------------------------------------

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
        if name in {"parent", "children", "data", "step_history"}:
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
        if name in {"parent", "children", "data", "step_history"}:
            message = f"'{name}' is a reserved attribute and cannot be deleted"
            raise AttributeError(message)
        if name in self.data:
            del self.data[name]
        else:
            message = f"{self.__class__.__name__!r} object has no attribute {name!r}"
            raise AttributeError(message)
