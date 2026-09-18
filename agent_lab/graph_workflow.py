"""The SAME workflow expressed with pydantic-graph.

Read this *after* `workflow.py`. Every node body here delegates to the shared
step in `workflow.py`; the framework contributes structure, not logic, so budget
accounting, failure handling and the evidence trail are literally the same code
whichever driver runs.

Three real pydantic-graph constraints, each learned from an actual failure
while writing this file:

1. `from __future__ import annotations` is REQUIRED. The `run()` return
   annotation names nodes defined later in the file; without postponed
   evaluation Python raises `NameError` at class-creation time.

2. The graph must be handed a first node INSTANCE (`inputs=`), because a graph
   executes a node object; it does not call a function to start.

3. The graph's state must be MUTABLE and mutated in place. pydantic-graph
   constructs a NEW `GraphRunContext` for each node, so `ctx.state = <new
   object>` is silently discarded. Our `RunState` is deliberately frozen, so
   it is wrapped in a small mutable holder and assigned into.

Point 3 is the argument for writing the plain version first: framework state
semantics are what bite, and they are obvious only once you already know what
correct looks like.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_graph import BaseNode, End, GraphBuilder, GraphRunContext

from .state import RunState, Stage
from .workflow import Deps, next_seq, step


@dataclass
class GraphState:
    """Mutable holder so a frozen `RunState` can be replaced in place.

    `seq` lives here too: every event needs a sequence number, and the graph has
    nowhere else to keep a counter that survives across nodes.
    """

    current: RunState
    seq: int = 0


Outcome = RunState


def _advance(holder: GraphState, deps: Deps, node_name: str) -> RunState:
    """Run one step through the shared helper, then store the result.

    Delegating here is the whole point: the graph is a different *driver* for the
    same semantics, not a second implementation of them.
    """
    holder.current = step(holder.current, deps, node_name, holder.seq)
    holder.seq += 1
    return holder.current


def _done(holder: GraphState) -> bool:
    """Whether the step just taken ended the run.

    Every node must consult this before handing control on. A run can terminate
    at ANY node — budget exhaustion at intake, a judgment that will not validate
    at classify — so "stop when terminal" is a property of the run, not of the
    two or three nodes where it used to be possible.
    """
    return holder.current.terminal is not None


@dataclass
class Intake(BaseNode[GraphState, Deps, Outcome]):
    """Validates input, then dispatches to the node the typed stage names.

    Re-entry lives HERE rather than as a second graph entry point. A
    pydantic-graph graph has one start; resuming a paused run is a matter of
    the typed state saying where it stopped, which is deterministic and
    inspectable. It also keeps structural validation meaningful.
    """

    async def run(
        self, ctx: GraphRunContext[GraphState, Deps]
    ) -> Classify | AwaitApproval | End[Outcome]:
        if ctx.state.current.stage is Stage.APPROVE:
            return AwaitApproval()
        _advance(ctx.state, ctx.deps, "intake")
        if _done(ctx.state):
            return End(ctx.state.current)
        return Classify()


@dataclass
class Classify(BaseNode[GraphState, Deps, Outcome]):
    async def run(self, ctx: GraphRunContext[GraphState, Deps]) -> Route | End[Outcome]:
        _advance(ctx.state, ctx.deps, "classify")
        if _done(ctx.state):
            return End(ctx.state.current)
        return Route()


@dataclass
class Route(BaseNode[GraphState, Deps, Outcome]):
    async def run(self, ctx: GraphRunContext[GraphState, Deps]) -> Prepare | End[Outcome]:
        _advance(ctx.state, ctx.deps, "route")
        if _done(ctx.state):
            return End(ctx.state.current)
        return Prepare()


@dataclass
class Prepare(BaseNode[GraphState, Deps, Outcome]):
    async def run(self, ctx: GraphRunContext[GraphState, Deps]) -> Verify | End[Outcome]:
        _advance(ctx.state, ctx.deps, "prepare")
        if _done(ctx.state):
            return End(ctx.state.current)
        return Verify()


@dataclass
class Verify(BaseNode[GraphState, Deps, Outcome]):
    async def run(self, ctx: GraphRunContext[GraphState, Deps]) -> AwaitApproval | End[Outcome]:
        _advance(ctx.state, ctx.deps, "verify")
        if _done(ctx.state):
            return End(ctx.state.current)
        return AwaitApproval()


@dataclass
class AwaitApproval(BaseNode[GraphState, Deps, Outcome]):
    async def run(self, ctx: GraphRunContext[GraphState, Deps]) -> End[Outcome]:
        _advance(ctx.state, ctx.deps, "await_approval")
        return End(ctx.state.current)


NODE_BY_STAGE = {
    Stage.INTAKE: Intake,
    Stage.CLASSIFY: Classify,
    Stage.ROUTE: Route,
    Stage.PREPARE: Prepare,
    Stage.VERIFY: Verify,
    Stage.APPROVE: AwaitApproval,
}


def _add_all(builder) -> None:
    """Wire the single graph entry, then every node's own outgoing edges."""
    builder.add(builder.edge_from(builder.start_node).to(Intake))
    # Each node's `run` return annotation supplies its own outgoing edges.
    for node_type in NODE_BY_STAGE.values():
        builder.add(builder.node(node_type))


def build_graph():
    """Explicit wiring: start -> Intake, then declared return annotations."""
    builder = GraphBuilder(state_type=GraphState, deps_type=Deps, output_type=Outcome)
    _add_all(builder)
    return builder.build()


def run_graph(state: RunState, deps: Deps, *, entry: Stage | None = None) -> RunState:
    """Run the framework graph; returns the same `RunState` type as `run_plain`.

    `entry` only sets the stage the typed state claims; `Intake` performs the
    dispatch. Both drivers therefore honour the same rule.
    """
    resumed = state if entry is None else state.model_copy(update={"stage": entry})
    holder = GraphState(current=resumed, seq=next_seq(resumed, deps))
    build_graph().run_sync(state=holder, deps=deps, inputs=Intake())
    return holder.current
