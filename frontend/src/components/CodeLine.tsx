// One cited line of source, coloured by token. The text is split into tokens by `prism-react-renderer` and every token is drawn as React text
// inside a span: no markup is made from it. The colours are static classes in `styles/code.css` (no inline style, nothing computed at run
// time), so the page works under the runner's style policy. The text is cut and made visible by `report/text.ts` before it is tokenised.

import { Highlight, type PrismTheme } from "prism-react-renderer";
import { languageOf } from "../report/lang";
import { show } from "../report/text";

// no colours of its own: the classes of the tokens carry them
const THEME: PrismTheme = { plain: {}, styles: [] };

export function CodeLine({ text, path }: { text: string; path: string }) {
  const shown = show(text);
  return (
    <Highlight code={shown} language={languageOf(path)} theme={THEME}>
      {({ tokens, getTokenProps }) => (
        <code className="rm-code-text" data-c="">
          {tokens.map((line, i) => (
            <span key={i} className="rm-code-line">
              {line.map((token, j) => {
                const { className, children } = getTokenProps({ token });
                return (
                  <span key={j} className={className}>
                    {children}
                  </span>
                );
              })}
              {i < tokens.length - 1 ? "\n" : null}
            </span>
          ))}
        </code>
      )}
    </Highlight>
  );
}
