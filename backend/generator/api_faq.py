from dataclasses import dataclass


@dataclass
class FAQItem:

    question: str

    answer: str


@dataclass
class APIFAQ:

    items: list[FAQItem]


class APIFAQGenerator:

    def generate(
        self,
        endpoint
    ):

        return APIFAQ(

            items=[

                FAQItem(

                    question=
                        (
                            "What format "
                            "should requests use?"
                        ),

                    answer=
                        (
                            "JSON request "
                            "payloads."
                        )
                ),

                FAQItem(

                    question=
                        (
                            "How should "
                            "errors be handled?"
                        ),

                    answer=
                        (
                            "Review API error "
                            "documentation."
                        )
                ),

                FAQItem(

                    question=
                        (
                            "How do I call "
                            "this endpoint?"
                        ),

                    answer=
                        (
                            f"Use "
                            f"{endpoint.path}"
                        )
                )
            ]
        )
