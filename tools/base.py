from abc import ABC, abstractmethod


class Tool(ABC):
    @abstractmethod
    def install(self) -> None:
        raise NotImplementedError("Subclasses must implement this method.")
