// Which key presses belong to a control that has the focus, and which belong to the page's shortcuts.

const TEXT_INPUTS = new Set(["text", "search", "email", "url", "tel", "password", "number", ""]);

/** The focus is where a person types (a text field, a text area, a list of choices, an editable element): the shortcuts leave every key to it. */
export function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  if (target.closest('[contenteditable=""], [contenteditable="true"], [contenteditable="plaintext-only"]') !== null || target.tagName === "TEXTAREA" || target.tagName === "SELECT") {
    return true;
  }
  return target instanceof HTMLInputElement && TEXT_INPUTS.has(target.type);
}

/** Enter is the control's own on a button, a link, a summary and a check box or radio. */
export function takesEnter(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  return ["BUTTON", "A", "SUMMARY"].includes(target.tagName) || (target instanceof HTMLInputElement && !TEXT_INPUTS.has(target.type));
}

/** Space is the control's own on a button, a summary and a check box or radio; on a link it does nothing, so the page's shortcut may take it. */
export function takesSpace(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  return ["BUTTON", "SUMMARY"].includes(target.tagName) || (target instanceof HTMLInputElement && !TEXT_INPUTS.has(target.type));
}
