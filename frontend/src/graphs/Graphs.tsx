// The two graphs that are drawn with React Flow: what the change reaches (repositories as groups, the screens and routes inside them) and the
// repositories and the edges between them (the layout is `layout.ts`, the data `report/graph.ts`). A chain is a list of steps, drawn in HTML
// (`components/ChainSteps.tsx`): it is a line of boxes, and a list reads better than a diagram of one line. This module and its libraries are
// loaded only by a page that shows a graph (`components/LazyGraphs.tsx`). Everything a box shows is drawn as text through `components/Content`;
// the boxes make no link or address of their own: an action is a function the page gives them.

import "@xyflow/react/dist/style.css";
import { useComputedColorScheme } from "@mantine/core";
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps, type NodeTypes } from "@xyflow/react";
import { createContext, useContext, useMemo, type ReactNode } from "react";
import { Clip } from "../components/Content";
import type { CrossGraph, ReachGraph } from "../report/graph";
import { visible } from "../report/text";
import { W } from "../report/words";
import { layoutCross, layoutReach, type Laid } from "./layout";

export interface FlowActions {
  /** An area box was chosen: the page opens its drawer. */
  openArea?: (id: string) => void;
}

const ActionsContext = createContext<FlowActions>({});

function RepoNode({ data }: NodeProps) {
  const d = data as unknown as { repo: string; origin: boolean; empty: boolean };
  return (
    <div className={d.origin ? "rm-repo rm-repo-origin" : "rm-repo"}>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <span className="rm-repo-label">
        <Clip text={d.repo} n={30} />
      </span>
      {d.empty ? <span className="rm-repo-empty">{W.reachEmpty}</span> : null}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}

function AreaNode({ data }: NodeProps) {
  const d = data as unknown as { id: string | null; label: string; kind: string; candidate: boolean };
  const actions = useContext(ActionsContext);
  const open = d.id !== null && actions.openArea !== undefined ? () => actions.openArea?.(d.id as string) : undefined;
  return (
    <div className={d.candidate ? "rm-node rm-area rm-area-candidate" : "rm-node rm-area"}>
      <button type="button" className="rm-area-button" disabled={open === undefined} onClick={open} aria-label={visible(d.label).slice(0, 160)}>
        <Clip text={d.label} n={26} />
      </button>
    </div>
  );
}

function MoreNode({ data }: NodeProps) {
  const d = data as unknown as { n: number };
  return <div className="rm-node rm-more">{`${d.n} ${W.reachMore}`}</div>;
}

function PlainNode({ data }: NodeProps) {
  const d = data as unknown as { repo: string };
  return (
    <div className="rm-node rm-plain">
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <Clip text={d.repo} n={28} />
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}

const NODE_TYPES: NodeTypes = { repo: RepoNode, area: AreaNode, more: MoreNode, plain: PlainNode };
const MARKER = { type: MarkerType.ArrowClosed, width: 14, height: 14 };
// the boxes are not zoomed out below this to fit a small window: a graph that is larger than its frame is panned, and stays readable
const FIT = { padding: 0.08, minZoom: 0.8, maxZoom: 1.2 };
const ARIA = {
  "node.a11yDescription.default": "",
  "node.a11yDescription.keyboardDisabled": "",
  "edge.a11yDescription.default": "",
  "controls.ariaLabel": W.graphControls,
  "controls.zoomIn.ariaLabel": W.graphZoomIn,
  "controls.zoomOut.ariaLabel": W.graphZoomOut,
  "controls.fitView.ariaLabel": W.graphFit,
  "handle.ariaLabel": "",
};
const FRAME_HEIGHTS = [200, 260, 320, 400, 480];

/** The frame is as tall as the boxes need, in steps (a class each: nothing is computed into a style), and at most 480 px. */
function frameSize(height: number): number {
  return FRAME_HEIGHTS.find((h) => h >= height) ?? FRAME_HEIGHTS[FRAME_HEIGHTS.length - 1];
}

function Flow({ laid, label, actions }: { laid: Laid; label: string; actions: FlowActions }) {
  const scheme = useComputedColorScheme("light");
  const edges = useMemo<Edge[]>(() => laid.edges.map((e) => ({ ...e, markerEnd: MARKER, markerStart: (e.data as { both?: boolean } | undefined)?.both === true ? MARKER : undefined })), [laid]);
  const nodes = useMemo<Node[]>(() => laid.nodes, [laid]);
  return (
    <ActionsContext.Provider value={actions}>
      <div className="rm-flow" role="group" aria-label={label} data-graph={label} data-size={frameSize(laid.height)}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          colorMode={scheme}
          fitView
          fitViewOptions={FIT}
          minZoom={0.4}
          maxZoom={1.6}
          nodesDraggable={false}
          nodesConnectable={false}
          nodesFocusable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          zoomOnScroll={false}
          preventScrolling={false}
          panOnDrag
          ariaLabelConfig={ARIA}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={18} />
          <Controls showInteractive={false} position="bottom-right" />
        </ReactFlow>
      </div>
    </ActionsContext.Provider>
  );
}

export function ReachFlow({ model, onArea }: { model: ReachGraph; onArea: (id: string) => void }) {
  const laid = useMemo(() => layoutReach(model), [model]);
  const actions = useMemo(() => ({ openArea: onArea }), [onArea]);
  return <Flow laid={laid} label={W.reachTitle} actions={actions} />;
}

export function CrossFlow({ model }: { model: CrossGraph }): ReactNode {
  const laid = useMemo(() => layoutCross(model), [model]);
  return <Flow laid={laid} label={W.crossGraphTitle} actions={{}} />;
}
