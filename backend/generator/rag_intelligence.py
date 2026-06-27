from dataclasses import dataclass


@dataclass
class RAGIntelligence:

    retrieval_strategy: str

    embedding_model: str

    vector_database: str

    chunking_strategy: str


class RAGIntelligenceEngine:

    def generate(
        self
    ):

        return RAGIntelligence(

            retrieval_strategy=
                "hybrid_search",

            embedding_model=
                "text-embedding-3-large",

            vector_database=
                "Qdrant",

            chunking_strategy=
                "semantic_chunking"
        )
