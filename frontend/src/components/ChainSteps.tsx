// A chain as a list of steps: the first line the run cited, then each step that reaches the area, with the line verbatim and coloured in a block
// of its own. Where the chain passes into another repository, a header names the repository (a lane); between two steps there is a link, named
// (`import`, `route`, `http`, `contract`, `package`) only when the report says what kind of step it is. Nothing is made up and nothing is drawn
// that the list of steps does not hold: the same data as `report/graph.ts` makes of a chain, as a list.

import { Badge, Text } from "@mantine/core";
import type { ChainGraph } from "../report/graph";
import type { Ev } from "../report/view";
import { W } from "../report/words";
import { Clip } from "./Content";
import { EvidenceBlock } from "./Evidence";

export function ChainSteps({ chain, model, index }: { chain: Ev[]; model: ChainGraph; index: number }) {
  const stem = `chain-${index}-step-`;
  return (
    <ol className="rm-chain" aria-label={`${W.chainNumber} ${index + 1}`}>
      {chain.map((ev, i) => {
        const node = model.nodes[i];
        const before = i > 0 ? model.nodes[i - 1] : null;
        const edge = i > 0 ? model.edges[i - 1] : null;
        const newLane = before === null || before.lane !== node.lane || before.repo !== node.repo;
        return (
          <li key={i} className="rm-chain-item">
            {newLane ? (
              <div className="rm-chain-lane" data-lane={node.lane}>
                <Text size="xs" fw={600} c="dimmed" component="span">
                  {W.laneWord}{" "}
                </Text>
                <Clip text={node.repo} n={60} />
              </div>
            ) : null}
            {edge !== null ? (
              <div className="rm-chain-link" data-kind={edge.label ?? "none"}>
                {edge.label !== null ? (
                  <Badge variant="default" className="rm-kind">
                    {edge.label}
                  </Badge>
                ) : null}
              </div>
            ) : null}
            <div id={`${stem}${i}`} tabIndex={-1} className="rm-step-target" data-step={i}>
              <Text size="xs" c="dimmed" mb={2}>{`${W.stepOf} ${i + 1} ${W.ofWord} ${chain.length}`}</Text>
              <EvidenceBlock ev={ev} />
            </div>
          </li>
        );
      })}
    </ol>
  );
}
