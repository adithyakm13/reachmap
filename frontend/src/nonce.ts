// The style nonce. The runner answers the index page with a policy of `style-src 'self' 'nonce-<random>'` and writes the same random value
// into the meta element named reachmap-style-nonce of that response. Mantine writes its theme variables and its responsive rules into style
// elements at run time, and passes this nonce to them; the style element of the scroll lock of a drawer takes it from the global that
// bundlers name `__webpack_nonce__`. Nothing else is inline. With no nonce (a dev server, a test, a runner of an earlier version) the
// page runs without one and a policy that has none would refuse those style elements, so `reachmap serve` always sends one.

const META = "reachmap-style-nonce";
const NONCE = /^[A-Za-z0-9_-]{16,64}$/;
// what the built index page holds until the runner replaces it (a real nonce is random: it may start with underscores, so the placeholder is matched whole)
const PLACEHOLDER = "__REACHMAP_STYLE_NONCE__";

/** The nonce the runner wrote into this page, or undefined. The element is taken out of the page once it has been read. */
export function readNonce(doc: Document): string | undefined {
  const meta = doc.querySelector(`meta[name="${META}"]`);
  if (meta === null) {
    return undefined;
  }
  const value = meta.getAttribute("content") ?? "";
  meta.remove();
  return value !== PLACEHOLDER && NONCE.test(value) ? value : undefined; // a placeholder that the runner did not replace is no nonce
}

/** Tell the libraries that make their own style element which nonce to use. */
export function publishNonce(nonce: string | undefined): void {
  if (nonce !== undefined) {
    (globalThis as { __webpack_nonce__?: string }).__webpack_nonce__ = nonce;
  }
}
