"""The SAME workflow expressed with pydantic-graph.

Read this *after* `workflow.py`. Every node body here delegates to the plain
node functions; the framework contributes structure, not logic.

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
from typing import Callable

from pydantic_graph import BaseNode, End, GraphBuilder, GraphRunContext

from .state import RunState, Stage, Transition
from .workflow import (
    Deps,
    NodeFn,
    n_await_approval,
    n_classify,
    n_intake,
    n_prepare,
    n_route,
    n_verify,
)


@dataclass
class GraphState:
    """Mutable holder so a frozen `RunState` can be replaced in place."""

    current: RunState


Outcome = RunState


def _advance(
    holder: GraphState,
    deps: Deps,
    node_fn: NodeFn,
) -> RunState:
    """Run one plain node, apply its declared transition, store the result.

    Identical to what the plain runner's loop does — which is exactly the
    point: the graph is a different *driver* for the same semantics.
    """
    state, transition, _detail = node_fn(holder.current, deps)
    holder.current = state.moved(transition)
    return holder.current


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
    ) -> Classify | AwaitApproval:
        if ctx.state.current.stage is Stage.APPROVE:
            return AwaitApproval()
        _advance(ctx.state, ctx.deps, n_intake)
        return Classify()


@dataclass
class Classify(BaseNode[GraphState, Deps, Outcome]):
    async def run(self, ctx: GraphRunContext[GraphState, Deps]) -> Route | End[Outcome]:
        _advance(ctx.state, ctx.deps, n_classify)
        return Route()


@dataclass
class Route(BaseNode[GraphState, Deps, Outcome]):
    async def run(self, ctx: GraphRunContext[GraphState, Deps]) -> Prepare | End[Outcome]:
        state = _advance(ctx.state, ctx.deps, n_route)
        if state.terminal is not None:
            return End(state)
        return Prepare()


@dataclass
class Prepare(BaseNode[GraphState, Deps, Outcome]):
    async def run(self, ctx: GraphRunContext[GraphState, Deps]) -> Verify:
        _advance(ctx.state, ctx.deps, n_prepare)
        return Verify()


@dataclass
class Verify(BaseNode[GraphState, Deps, Outcome]):
    async def run(self, ctx: GraphRunContext[GraphState, Deps]) -> AwaitApproval | End[Outcome]:
        state = _advance(ctx.state, ctx.deps, n_verify)
        if state.terminal is not None:
            return End(state)
        return AwaitApproval()


@dataclass
class AwaitApproval(BaseNode[GraphState, Deps, Outcome]):
    async def run(self, ctx: GraphRunContext[GraphState, Deps]) -> End[Outcome]:
        return End(_advance(ctx.state, ctx.deps, n_await_approval))


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
    for node_type in (Intake, Classify, Route, Prepare, Verify, AwaitApproval):
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
    holder = GraphState(current=resumed)
    build_graph().run_sync(state=holder, deps=deps, inputs=Intake())
    return holder.current


def transition_for(node_fn: Callable) -> Transition:
    """Exposed for lesson 06: proves the two runners agree edge-for-edge."""
    return TRANSITIONS_BY_NODE[node_fn.__name__]


TRANSITIONS_BY_NODE: dict[str, Transition] = {
    "n_intake": Transition.TO_CLASSIFY,
    "n_classify": Transition.TO_ROUTE,
    "n_route": Transition.TO_PREPARE,
    "n_prepare": Transition.TO_VERIFY,
    "n_verify": Transition.TO_APPROVE,
    "n_await_approval": Transition.TO_DONE,
}
