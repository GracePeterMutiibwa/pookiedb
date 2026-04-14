
class ModelRegistry:
    """Keeps track of all registered Model subclasses."""

    def __init__(self):
        self._models: dict[str, type] = {}

    def register(self, model_class):
        key = f"{model_class.__module__}.{model_class.__name__}"
        self._models[key] = model_class

    def get(self, name: str):
        # Try full qualified name first
        if name in self._models:
            return self._models[name]
        # Try by class name only
        for key, cls in self._models.items():
            if cls.__name__ == name:
                return cls
        return None

    def all(self) -> list:
        return list(self._models.values())

    def __iter__(self):
        return iter(self._models.values())

    def __len__(self):
        return len(self._models)


registry = ModelRegistry()
