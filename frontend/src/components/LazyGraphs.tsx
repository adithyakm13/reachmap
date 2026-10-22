// The graphs load on the page that shows one: React Flow and dagre are in a chunk of their own, fetched when a graph is first drawn. While
// it loads there is a grey block; if it cannot be fetched or drawn there are fixed words, and the lists next to every graph say the same.

import { Alert } from "@mantine/core";
import { lazy, Suspense } from "react";
import type { CrossGraph, ReachGraph } from "../report/graph";
import { W } from "../report/words";
import { Loading, SectionBoundary } from "./Boxes";

const ReachFlow = lazy(() => import("../graphs/Graphs").then((m) => ({ default: m.ReachFlow })));
const CrossFlow = lazy(() => import("../graphs/Graphs").then((m) => ({ default: m.CrossFlow })));

function Wait({ children }: { children: React.ReactNode }) {
  return (
    <SectionBoundary>
      <Suspense fallback={<Loading rows={3} />}>{children}</Suspense>
    </SectionBoundary>
  );
}

export function ReachGraphView({ model, onArea }: { model: ReachGraph; onArea: (id: string) => void }) {
  if (model.repos.length === 0) {
    return <Alert color="gray">{W.graphFailed}</Alert>;
  }
  return (
    <Wait>
      <ReachFlow model={model} onArea={onArea} />
    </Wait>
  );
}

export function CrossGraphView({ model }: { model: CrossGraph }) {
  if (model.repos.length === 0) {
    return null;
  }
  return (
    <Wait>
      <CrossFlow model={model} />
    </Wait>
  );
}
