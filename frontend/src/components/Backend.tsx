import type { View } from "../report/view";
import { W } from "../report/words";
import { C, Code, Pre } from "./Content";
import { EvidenceList } from "./Evidence";
import { Field, Join } from "./Fields";

/** For a frontend change, the backend routes it calls and the modules each handler runs. */
export function Backend({ view }: { view: View }) {
  return (
    <section aria-labelledby="backend-h" className="rm-detail">
      <h2 id="backend-h">{W.sectionBackend}</h2>
      {view.backend.length === 0 ? <p>{W.noBackend}</p> : null}
      {view.backend.length > 0 ? (
        <ul>
          {view.backend.map((b, i) => (
            <li key={i}>
              <Code text={b.client.locator} /> calls <Code text={`${b.method} ${b.path}`} /> in <Code text={b.repo} />
              <ul>
                <li>
                  <strong>{W.clientLine}</strong>
                  <EvidenceList items={[b.client]} />
                </li>
                <Field label={W.labelHandler}>
                  <Code text={b.handler} />
                </Field>
                {!b.complete || b.reasons.length > 0 ? (
                  <Field label={W.modulesNotWhole}>
                    {b.reasons.length > 0 ? <Join sep="; " items={b.reasons.map((r, j) => <C key={j} text={r} />)} /> : W.noReason}
                  </Field>
                ) : null}
                {b.modules.length > 0 ? (
                  <li>
                    <strong>{W.modulesRun}</strong>
                    <ul>
                      {b.modules.map((m, j) => (
                        <li key={j}>
                          {`Depth ${m.depth}: `}
                          <Code text={m.path} /> imported by <Code text={m.importedBy} />
                          <Pre text={m.evidence.detail} />
                        </li>
                      ))}
                    </ul>
                  </li>
                ) : null}
              </ul>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
