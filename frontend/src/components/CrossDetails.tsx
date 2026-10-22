import type { ConsumerRow, ContractReadView, HopView, HttpView, View } from "../report/view";
import { ADOPTION_WORDS, BASIS_WORDS, CANDIDATE, W } from "../report/words";
import { C, Code } from "./Content";
import { EvidenceList } from "./Evidence";
import { Field, Join } from "./Fields";
import { own } from "../report/read";

function Evidence({ label, items }: { label: string; items: Parameters<typeof EvidenceList>[0]["items"] }) {
  return (
    <li>
      <strong>{label}</strong>
      <EvidenceList items={items} />
    </li>
  );
}

function Hop({ h }: { h: HopView }) {
  return (
    <li>
      <Code text={h.fromRepo} /> to <Code text={h.toRepo} />: <Code text={h.kind} /> {`edge, depth ${h.depth}`}
      {h.candidate ? ` (${CANDIDATE})` : null}
      <ul>
        {h.detail !== "" ? (
          <Field label={W.whatWasLooked}>
            <C text={h.detail} />
          </Field>
        ) : null}
        {h.pins.length > 0 ? (
          <Field label={W.pinsRead}>
            <Join
              items={h.pins.map(([ref, role, sha], i) => (
                <span key={i}>
                  <Code text={ref} /> (<C text={role} />) at <Code text={sha} />
                </span>
              ))}
            />
          </Field>
        ) : null}
        {h.complete === false || h.reasons.length > 0 ? (
          <Field label={W.readInPart}>
            {h.reasons.length > 0 ? <Join sep="; " items={h.reasons.map((r, i) => <C key={i} text={r} />)} /> : W.noReason}
          </Field>
        ) : null}
      </ul>
    </li>
  );
}

function consumerGroups(rows: ConsumerRow[]): ConsumerRow[][] {
  const keys: string[] = [];
  const groups = new Map<string, ConsumerRow[]>();
  for (const r of rows) {
    const k = JSON.stringify([r.producer, r.repo]);
    if (!groups.has(k)) {
      keys.push(k);
      groups.set(k, []);
    }
    groups.get(k)?.push(r);
  }
  return keys.map((k) => groups.get(k) as ConsumerRow[]);
}

function Consumers({ rows }: { rows: ConsumerRow[] }) {
  return (
    <ul>
      {consumerGroups(rows).map((mine, i) => (
        <li key={i}>
          <Code text={mine[0].producer} /> consumed by <Code text={mine[0].repo} />
          <ul>
            {mine.map((r, j) => (
              <li key={j}>
                <Code text={r.branch} /> {r.pin !== null ? <>pin <Code text={r.pin} /></> : "no pin read"} (<Code text={r.state} />)
                {r.tag !== null ? <>, tag <Code text={r.tag} /></> : null}
                {r.firstRelease !== null ? <>, first released in <Code text={r.firstRelease} /></> : null},{" "}
                {own(ADOPTION_WORDS, r.adoption) ?? <Code text={r.adoption} />}
                {r.detail !== "" ? <>: <C text={r.detail} /></> : null}
                {r.evidence.length > 0 ? (
                  <ul>
                    <Evidence label={W.labelEvidence} items={r.evidence} />
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}

function Http({ e }: { e: HttpView }) {
  const flags = [...(e.candidate ? [CANDIDATE] : []), ...(e.removed ? [W.routeRemoved] : [])];
  return (
    <li>
      <Code text={e.clientRepo} /> calls <Code text={e.route} /> in <Code text={e.serverRepo} />
      {flags.length > 0 ? ` (${flags.join("; ")})` : null}
      <ul>
        {e.method !== null ? (
          <Field label={W.labelMethod}>
            <Code text={e.method} />
          </Field>
        ) : null}
        <Field label={W.labelUrl}>
          <Code text={e.url} />
        </Field>
        <Field label={W.labelMatch}>
          <Code text={e.kind} />
        </Field>
        {e.rematched !== null ? (
          <Field label={W.answeredAfter}>
            <Code text={e.rematched} />
          </Field>
        ) : null}
        {e.note !== "" ? (
          <Field label={W.labelNote}>
            <C text={e.note} />
          </Field>
        ) : null}
        <Evidence label={W.clientLine} items={[e.client]} />
        {e.via.length > 0 ? <Evidence label={W.urlLines} items={e.via} /> : null}
        {e.routeEvidence.length > 0 ? <Evidence label={W.routeAndMounts} items={e.routeEvidence} /> : null}
      </ul>
    </li>
  );
}

function Read({ r }: { r: ContractReadView }) {
  const basis = own(BASIS_WORDS, r.basis);
  return (
    <li>
      <Code text={`${r.owner}.${r.field}`} /> read in <Code text={r.symbol} /> of <Code text={r.repo} /> on <Code text={r.branch} />
      {r.pin !== null ? <>, pin <Code text={r.pin} /></> : null}, {own(ADOPTION_WORDS, r.adoption) ?? <Code text={r.adoption} />}:{" "}
      {basis !== undefined ? basis : <Code text={r.basis} />} (<Code text={r.form} />)
      {r.evidence.length > 0 ? (
        <ul>
          <Evidence label={W.labelEvidence} items={r.evidence} />
        </ul>
      ) : null}
    </li>
  );
}

/** Cross-repository reach: per edge (http, package, contract) what was looked at, the pins per branch, adoption, the contract reads, candidates apart. */
export function CrossDetails({ view }: { view: View }) {
  const c = view.cross;
  const nothingElse = c.unfollowed.length === 0 && c.unread.length === 0;
  return (
    <div className="rm-detail">
      {c.empty ? <p>{W.noCross}</p> : null}
      {c.hops.length > 0 ? (
        <>
          <h3>{W.edgesFollowed}</h3>
          <ul>
            {c.hops.map((h, i) => (
              <Hop key={i} h={h} />
            ))}
          </ul>
        </>
      ) : null}
      {c.consumers.length > 0 ? (
        <>
          <h3>{W.packageEdges}</h3>
          <Consumers rows={c.consumers} />
        </>
      ) : null}
      {c.http.length > 0 ? (
        <>
          <h3>{W.httpEdges}</h3>
          <ul>
            {c.http.map((e, i) => (
              <Http key={i} e={e} />
            ))}
          </ul>
        </>
      ) : null}
      {c.changes.length > 0 || c.reads.length > 0 || c.candidates.length > 0 ? (
        <>
          <h3>{W.contractEdges}</h3>
          {c.changes.length > 0 ? (
            <>
              <p>{W.contractChanged}</p>
              <ul>
                {c.changes.map((ch, i) => (
                  <li key={i}>
                    <Code text={ch.symbol} /> <Code text={ch.change} /> in <Code text={ch.repo} /> <Code text={ch.path} />
                    <ul>
                      <Field label={W.labelContractBefore}>
                        <Code text={ch.before} />
                      </Field>
                      <Field label={W.labelContractAfter}>
                        <Code text={ch.after} />
                      </Field>
                      {ch.kinds.length > 0 ? (
                        <Field label={W.labelKinds}>
                          <Join items={ch.kinds.map((k, j) => <Code key={j} text={k} />)} />
                        </Field>
                      ) : null}
                      <Evidence label={W.labelEvidence} items={ch.evidence} />
                    </ul>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {c.reads.length > 0 ? (
            <>
              <p>{W.contractReads}</p>
              <ul>
                {c.reads.map((r, i) => (
                  <Read key={i} r={r} />
                ))}
              </ul>
            </>
          ) : null}
          {c.candidates.length > 0 ? (
            <>
              <h4>{W.candidates}</h4>
              <p>{`Reads that are ${CANDIDATE}.`}</p>
              <ul>
                {c.candidates.map((r, i) => (
                  <Read key={i} r={r} />
                ))}
              </ul>
            </>
          ) : null}
        </>
      ) : null}
      {!nothingElse ? (
        <>
          <h3>{W.notFollowed}</h3>
          <ul>
            {c.unfollowed.map((x, i) => (
              <li key={`u${i}`}>
                Edge not followed: <Code text={x} />
              </li>
            ))}
            {c.unread.map((x, i) => (
              <li key={`r${i}`}>
                Entry points not read in <Code text={x} />
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}
