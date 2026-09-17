from dataclasses import dataclass


@dataclass
class MemoryEntry:

    memory_id: str

    namespace: str

    content: str

    importance: float


@dataclass
class MemoryStore:

    entries: list[MemoryEntry]


class AiMemoryManagementEngine:

    def create_store(
        self,
        namespace: str
    ):

        return MemoryStore(

            entries=[]
        )
