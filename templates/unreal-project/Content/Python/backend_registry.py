"""Safe declarative backend registry for Unreal JSON adapters."""


class BackendError(RuntimeError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class BackendRegistry:
    """Registry for the deliberately small, declarative adapter contract."""

    OPERATIONS = ("capabilities", "describe", "validate", "inspect", "mutate", "diagnostics")
    LIFECYCLE_OPERATIONS = ("validate", "inspect", "mutate", "diagnostics")

    def __init__(self):
        self._adapters = {}

    @staticmethod
    def _non_empty_string(value, field):
        if not isinstance(value, str) or not value.strip():
            raise BackendError("invalid_backend", field + " must be a non-empty string")
        return value

    @classmethod
    def _metadata(cls, adapter):
        for field in ("version", "engine"):
            cls._non_empty_string(adapter[field], field)
        capabilities = adapter["capabilities"]
        if not isinstance(capabilities, list) or any(
            not isinstance(item, str) or not item.strip() for item in capabilities
        ):
            raise BackendError("invalid_backend", "capabilities must be a list of non-empty strings")
        if len(set(capabilities)) != len(capabilities):
            raise BackendError("invalid_backend", "capabilities must not contain duplicates")
        lifecycle = adapter.get("lifecycle")
        if lifecycle is None:
            lifecycle = [name for name in cls.LIFECYCLE_OPERATIONS if name in adapter["operations"]]
        if not isinstance(lifecycle, list) or any(
            item not in cls.LIFECYCLE_OPERATIONS for item in lifecycle
        ) or len(set(lifecycle)) != len(lifecycle):
            raise BackendError("invalid_backend", "lifecycle must be a list of unique lifecycle operations")
        dependency = adapter.get("dependency")
        if dependency is not None and not isinstance(dependency, (str, dict)):
            raise BackendError("invalid_backend", "dependency must be a string or object")
        if isinstance(dependency, str) and not dependency.strip():
            raise BackendError("invalid_backend", "dependency must be a non-empty string")
        if isinstance(dependency, dict):
            name = dependency.get("name")
            if not isinstance(name, str) or not name.strip():
                raise BackendError("invalid_backend", "dependency.name must be a non-empty string")
            if "available" in dependency and not isinstance(dependency["available"], bool):
                raise BackendError("invalid_backend", "dependency.available must be a boolean")
        return lifecycle

    def register(self, adapter):
        if not isinstance(adapter, dict):
            raise BackendError("invalid_backend", "Backend adapter must be an object")
        backend_id = adapter.get("backend_id")
        if not isinstance(backend_id, str) or not backend_id.strip():
            raise BackendError("invalid_backend", "backend_id must be a non-empty string")
        if backend_id in self._adapters:
            raise BackendError("backend_conflict", "Backend is already registered: " + backend_id)
        required = {"backend_id", "version", "engine", "capabilities", "operations"}
        missing = sorted(required - set(adapter))
        if missing:
            raise BackendError("invalid_backend", "Missing backend fields: " + ", ".join(missing))
        if not isinstance(adapter["operations"], dict):
            raise BackendError("invalid_backend", "operations must be an object")
        lifecycle = self._metadata(adapter)
        for operation, handler in adapter["operations"].items():
            if operation not in self.OPERATIONS or not callable(handler):
                raise BackendError("invalid_backend", "Unsupported backend operation: " + str(operation))
        if any(operation not in adapter["operations"] for operation in lifecycle):
            raise BackendError("invalid_backend", "lifecycle references an unavailable operation")
        stored = dict(adapter)
        stored["lifecycle"] = list(lifecycle)
        stored["_explicit_lifecycle"] = "lifecycle" in adapter
        self._adapters[backend_id] = stored
        return self.describe(backend_id)

    def capabilities(self):
        return [self.describe(name) for name in sorted(self._adapters)]

    def describe(self, backend_id):
        adapter = self._adapters.get(backend_id)
        if adapter is None:
            raise BackendError("backend_not_found", "Unknown backend: " + str(backend_id))
        result = {
            "backend_id": backend_id,
            "version": adapter["version"],
            "engine": adapter["engine"],
            "capabilities": list(adapter["capabilities"]),
            "enabled": adapter.get("enabled", True) is True,
            "dependency": adapter.get("dependency"),
        }
        if adapter.get("_explicit_lifecycle"):
            result["lifecycle"] = list(adapter["lifecycle"])
        return result

    def invoke(self, backend_id, operation, payload):
        adapter = self._adapters.get(backend_id)
        if adapter is None:
            raise BackendError("backend_not_found", "Unknown backend: " + str(backend_id))
        if adapter.get("enabled", True) is not True:
            raise BackendError("backend_disabled", "Backend is disabled: " + backend_id)
        dependency = adapter.get("dependency")
        if isinstance(dependency, dict) and dependency.get("available") is False:
            raise BackendError("backend_dependency_missing", "Backend dependency is unavailable: " + dependency["name"], {"backend_id": backend_id, "dependency": dependency["name"]})
        handler = adapter["operations"].get(operation)
        if handler is None:
            raise BackendError("backend_dependency_missing", "Backend operation is unavailable: " + operation, {"backend_id": backend_id, "operation": operation})
        if not isinstance(payload, dict):
            raise BackendError("invalid_backend", "Backend payload must be an object", {"backend_id": backend_id, "operation": operation})
        try:
            return handler(payload)
        except BackendError:
            raise
        except Exception as error:
            raise BackendError("backend_operation_failed", str(error), {"backend_id": backend_id, "operation": operation})

    def validate(self, backend_id, payload):
        return self.invoke(backend_id, "validate", payload)

    def inspect(self, backend_id, payload):
        return self.invoke(backend_id, "inspect", payload)

    def mutate(self, backend_id, payload):
        return self.invoke(backend_id, "mutate", payload)

    def diagnostics(self, backend_id, payload):
        return self.invoke(backend_id, "diagnostics", payload)
