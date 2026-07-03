from dataclasses import dataclass


@dataclass
class CFGNode:

    node_id: int

    statement_type: str

    successors: list[int]


@dataclass
class ControlFlowGraph:

    entry_node: int

    exit_node: int

    nodes: list[CFGNode]


class ControlFlowGraphEngine:

    def build(
        self,
        ast
    ):

        entry = CFGNode(

            node_id=0,

            statement_type="entry",

            successors=[1]
        )

        exit = CFGNode(

            node_id=1,

            statement_type="exit",

            successors=[]
        )

        return ControlFlowGraph(

            entry_node=0,

            exit_node=1,

            nodes=[

                entry,

                exit
            ]
        )
