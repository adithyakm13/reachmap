import { Badge, Card, Group, Stack, Text } from "@mantine/core";
import { allAreas, type RecordView, type SuggestionView, type Suggested } from "../../report/view";
import { W } from "../../report/words";
import { useReportCtx } from "../../pages/reportContext";
import { EmptyState } from "../Boxes";
import { C, Code } from "../Content";
import { EvidenceList } from "../Evidence";
import { RecordItem } from "../Records";

/** Tests the configuration maps to an area: a section of its own, apart from the suggestions (rule 4.3: two different fields). */
export function MappedTests() {
  const { view } = useReportCtx();
  return (
    <Stack gap="xs" component="section" aria-labelledby="mapped-h">
      <h2 id="mapped-h">{W.sectionMapped}</h2>
      <Text size="sm" c="dimmed">
        {W.mappedNote}
      </Text>
      {view.mapped.length === 0 ? <Text size="sm">{W.testsMappedNone}</Text> : null}
      {view.mapped.map((m, i) => (
        <Card key={i} padding="sm">
          <Group gap="xs" wrap="wrap">
            <Badge variant="default" className="rm-kind">
              <Code text={m.source} />
            </Badge>
            <Text size="sm" component="span">
              case <Code text={m.caseId} />: <C text={m.title} />
            </Text>
          </Group>
          <Text size="xs" c="dimmed" mt={2}>
            area <Code text={m.areaId} />, mapping <Code text={m.mapping} />
          </Text>
        </Card>
      ))}
    </Stack>
  );
}

/** One suggested case: its source (dashed: a suggestion is a place to look), its title, the words it matched, and the spec line when a source read code. */
export function SuggestionCard({ x }: { x: SuggestionView }) {
  return (
    <Card padding="sm" className="rm-suggested">
      <Group gap="xs" wrap="wrap">
        <Badge variant="outline" color="gray" className="rm-dashed">
          <Code text={x.source} />
        </Badge>
        <Text size="sm" component="span">
          case <Code text={x.caseId} />: <C text={x.title} />
        </Text>
      </Group>
      {x.words.length > 0 ? (
        <Group gap={6} mt={4}>
          <Text size="xs" c="dimmed" component="span">
            {W.matchedWords}
          </Text>
          {x.words.map((w, j) => (
            <Code key={j} text={w} />
          ))}
        </Group>
      ) : null}
      {x.evidence.length > 0 ? (
        <Stack gap={4} mt={6}>
          <Text size="xs" c="dimmed">
            {W.labelSpecLine}
          </Text>
          <EvidenceList items={x.evidence} />
        </Stack>
      ) : null}
    </Card>
  );
}

/** What a spec source read and could not use, in words (`layout.py: _partial_count`). */
function partialCount(partial: RecordView[]): string {
  const urls = partial.filter((r) => r.kind === "spec_url_partial").length;
  const unmatched = partial.some((r) => r.kind === "spec_visit_unmatched");
  const parts: string[] = [];
  if (urls > 0) {
    parts.push(`${urls} ${urls === 1 ? W.partialUrlOne : W.partialUrlMany}`);
  }
  if (unmatched) {
    parts.push(W.partialUnmatched);
  }
  return parts.join(" and ");
}

function PartialRecords({ s, lead }: { s: Suggested; lead: string }) {
  if (s.partial.length === 0) {
    return null;
  }
  return (
    <>
      <Text size="sm">{`${lead} ${partialCount(s.partial)}:`}</Text>
      <Stack gap="xs">
        {s.partial.map((r, i) => (
          <RecordItem key={i} r={r} />
        ))}
      </Stack>
    </>
  );
}

const SOURCE_INTRO: [string, string][] = [
  ["source_unavailable", "suggestedIntroUnavailable"],
  ["suggestions_incomplete", "suggestedIntroIncomplete"],
  ["source_unconfigured", "suggestedIntroUnconfigured"],
];

/** What each test source that was not read completely says: its records, under a sentence that says what that means for the list. */
function SourceRecords({ records, skip }: { records: RecordView[]; skip?: string }) {
  return (
    <>
      {SOURCE_INTRO.filter(([kind]) => kind !== skip).map(([kind, word]) => {
        const mine = records.filter((r) => r.kind === kind);
        return mine.length === 0 ? null : (
          <Stack gap="xs" key={kind}>
            <Text size="sm">{W[word as keyof typeof W]}</Text>
            {mine.map((r, i) => (
              <RecordItem key={i} r={r} />
            ))}
          </Stack>
        );
      })}
    </>
  );
}

/** Suggested test cases: always under "Suggested, not verified coverage", never merged with the mapped tests, and per area (`layout.py: _suggested`).
 *  "None suggested." is said only when every source was read completely. */
export function SuggestedTests() {
  const { view } = useReportCtx();
  const s = view.suggested;
  const areas = allAreas(view);
  const order = [...view.mustTest, ...view.sample, ...view.candidates];
  const known = new Set(areas.map((a) => a.index));
  const rest = s.items.filter((x) => x.area === null || !known.has(x.area));
  const ownRecords = (kind: string) => s.records.filter((r) => r.kind === kind);
  return (
    <Stack gap="xs" component="section" aria-labelledby="suggested-h">
      <h2 id="suggested-h">{W.sectionSuggested}</h2>
      <Text size="sm" c="dimmed">
        {W.suggestedNote}
      </Text>
      {s.state === "listed" ? (
        <>
          {order.map((a) => {
            const mine = s.items.filter((x) => x.area === a.index);
            return mine.length === 0 ? null : (
              <Stack gap="xs" key={a.index} component="section" aria-labelledby={`suggested-${a.index}`} data-suggested-area={a.index}>
                <h3 id={`suggested-${a.index}`}>
                  <Code text={a.label} /> {W.suggestedIn} <Code text={a.repo} />
                </h3>
                {mine.map((x, i) => (
                  <SuggestionCard key={i} x={x} />
                ))}
              </Stack>
            );
          })}
          {rest.length > 0 ? (
            <Stack gap="xs" component="section" aria-labelledby="suggested-other">
              <h3 id="suggested-other">{W.suggestedNotTied}</h3>
              {rest.map((x, i) => (
                <SuggestionCard key={i} x={x} />
              ))}
            </Stack>
          ) : null}
          <SourceRecords records={s.records} />
          <PartialRecords s={s} lead={W.suggestedPartialIntro} />
        </>
      ) : null}
      {s.state === "unavailable" ? (
        <>
          <Text size="sm">{W.suggestedUnavailable}</Text>
          <Stack gap={2}>
            {ownRecords("source_unavailable").map((r, i) => (
              <Text key={i} size="sm">
                <C text={r.notEstablished} />
              </Text>
            ))}
          </Stack>
          <SourceRecords records={s.records} skip="source_unavailable" />
        </>
      ) : null}
      {s.state === "incomplete" ? (
        <>
          <Text size="sm">{W.suggestedIncomplete}</Text>
          <Stack gap={2}>
            {ownRecords("suggestions_incomplete").map((r, i) => (
              <Text key={i} size="sm">
                <C text={r.notEstablished} />
              </Text>
            ))}
          </Stack>
          <SourceRecords records={s.records} skip="suggestions_incomplete" />
        </>
      ) : null}
      {s.state === "unconfigured" ? (
        <>
          <Text size="sm">{s.sources.some((x) => x.state !== "unconfigured") ? W.suggestedSomeUnconfigured : W.suggestedUnconfigured}</Text>
          <Stack gap={2}>
            {s.records.map((r, i) => (
              <Text key={i} size="sm">
                <C text={r.notEstablished} />
              </Text>
            ))}
          </Stack>
        </>
      ) : null}
      {s.state === "read_none" ? (
        <>
          <Text size="sm">{s.partial.length > 0 ? `${W.suggestedNoneSoFar} ${partialCount(s.partial)}:` : W.suggestedNone}</Text>
          {s.partial.length > 0 ? (
            <Stack gap="xs">
              {s.partial.map((r, i) => (
                <RecordItem key={i} r={r} />
              ))}
            </Stack>
          ) : null}
        </>
      ) : null}
      {s.state === "not_read" ? <Text size="sm">{W.suggestedNotRead}</Text> : null}
    </Stack>
  );
}

export function TestsTab() {
  const { view } = useReportCtx();
  if (!view.completed) {
    return <EmptyState title={W.tabTests} />;
  }
  return (
    <Stack gap="lg">
      <MappedTests />
      <SuggestedTests />
    </Stack>
  );
}
