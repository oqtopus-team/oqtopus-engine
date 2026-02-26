import importlib
from typing import Any


class DiContainer:
    """A lightweight Dependency Injection (DI) container used in OQTOPUS Engine.

    The container receives a fully-parsed configuration dictionary
    (after environment-variable substitution via load_config()) and
    provides objects based on `_target_` class paths.

    Features:
      - Supports `_scope_` = "singleton" (default) or "prototype".
      - `_target_` is required for every component.
      - Keys starting with "_" (e.g., `_target_`, `_scope_`) are metadata
        and excluded from constructor arguments.
      - Uses Python's importlib to dynamically import target classes.
      - Singleton instances are cached inside the container.

    Example YAML:

        job_fetcher:
          _target_: oqtopus_engine_core.fetchers.OqtopusCloudJobFetcher
          _scope_: singleton
          url: "http://localhost:8888"
          interval_seconds: 10

    Example usage:

        dicon = DiContainer(config)
        job_fetcher = dicon.get("job_fetcher")
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize the DI container.

        Args:
            config: Configuration dictionary produced by load_config().
                   Top-level keys represent dependency names.

        """
        self._config = config
        self._instances: dict[str, Any] = {}  # cache for singletons

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get(self, name: str) -> Any:  # noqa: ANN401
        """Retrieve a dependency by name.

        Behavior:
          - Raise KeyError if the name does not exist in the config.
          - Import the target class defined by `_target_`.
          - Create an instance with kwargs from the component config.
          - Respect `_scope_`:
              - singleton (default): cache the instance
              - prototype: always create a fresh instance

        Args:
            name: Component name to retrieve.

        Returns:
            The created or cached instance.

        Raises:
            KeyError: If the component name is missing.
            ValueError: If `_target_` is missing.
            ImportError: If module/class cannot be imported.
            TypeError: If constructor arguments mismatch.

        """  # noqa: DOC502
        if name not in self._config:
            message = f"Unknown dependency: {name}"
            raise KeyError(message)

        # Singleton cache check
        if name in self._instances:
            return self._instances[name]

        instance = self._create_instance(name)

        # Cache only if singleton
        scope = self._config[name].get("_scope_", "singleton")
        if scope == "singleton":
            self._instances[name] = instance

        return instance

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _create_instance(self, name: str) -> Any:  # noqa: ANN401
        """Create a new instance for a given component name.

        `_target_` is mandatory. Other keys starting with "_" are ignored.
        All remaining keys are passed to the constructor as kwargs.

        Args:
            name: Component name to create.

        Returns:
            The created instance.

        Raises:
            ValueError: If `_target_` is missing.
            TypeError: If constructor arguments do not match.

        """
        cfg = self._config[name]

        if "_target_" not in cfg:
            message = f"Missing _target_ for dependency {name}"
            raise ValueError(message)

        klass = self._load_class(cfg["_target_"])

        # Filter out metadata keys (starting with "_")
        kwargs = {
            k: v for k, v in cfg.items()
            if not k.startswith("_")
        }

        # Instantiate with keyword arguments
        try:
            return klass(**kwargs)
        except TypeError as exc:
            message = (
                f"Failed to instantiate {klass.__name__} "
                f"with arguments {kwargs}"
            )
            raise TypeError(message) from exc

    @staticmethod
    def _load_class(target: str) -> type:
        """Load a class from a string path.

        Example path: "oqtopus_engine_core.fetchers.OqtopusCloudJobFetcher"

        Args:
            target: Fully-qualified class path.

        Returns:
            The class object.

        Raises:
            ImportError: If module or class cannot be imported.

        """
        try:
            module_path, class_name = target.rsplit(".", 1)
        except ValueError as exc:
            message = f"Invalid _target_ format: {target}"
            raise ImportError(message) from exc

        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            message = f"Cannot import module {module_path}"
            raise ImportError(message) from exc

        try:
            return getattr(module, class_name)
        except AttributeError as exc:
            message = f"Module '{module_path}' has no class {class_name}"
            raise ImportError(message) from exc
