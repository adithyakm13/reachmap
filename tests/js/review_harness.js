"use strict";
/* Runs the decision controls of reachmap/report/assets/report.js against a small fake DOM, with the report and a list of steps on
   standard input, and prints what happened as JSON. Usage: node review_harness.js <path to report.js>. Nothing here is shipped. */
const fs = require("fs");

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const calls = { network: 0, innerHTML: 0 };
const trap = () => { calls.network += 1; throw new Error("network"); };
global.fetch = trap;
global.XMLHttpRequest = trap;
global.WebSocket = trap;
global.setTimeout = (fn) => { fn(); return 0; };  /* the revoke after a download runs at once */
global.document = { getElementById: () => null };
global.window = {};
const review = require(process.argv[2]);

class Text {
  constructor(t) { this.nodeType = 3; this.textContent = String(t); this.parent = null; }
}

class El {
  constructor(tag) {
    this.nodeType = 1; this.tagName = String(tag).toUpperCase(); this.children = []; this.attrs = {}; this.listeners = {};
    this.className = ""; this.value = ""; this.type = ""; this.disabled = false; this.maxLength = -1; this.hidden = false; this.parent = null; this._text = "";
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this._text = String(v); this.children.forEach((c) => { c.parent = null; }); this.children = []; }
  appendChild(c) { if (c.parent) { c.parent.removeChild(c); } c.parent = this; this.children.push(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); c.parent = null; return c; }
  insertBefore(c, ref) {
    if (c.parent) { c.parent.removeChild(c); }
    const at = ref ? this.children.indexOf(ref) : -1;
    c.parent = this;
    if (at < 0) { this.children.push(c); } else { this.children.splice(at, 0, c); }
    return c;
  }
  get firstChild() { return this.children[0] || null; }
  remove() { if (this.parent) { this.parent.removeChild(this); } }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  click() { (this.listeners.click || []).forEach((fn) => fn({ target: this, preventDefault() {} })); }
  set innerHTML(v) { calls.innerHTML += 1; throw new Error("innerHTML was set"); }
  set outerHTML(v) { calls.innerHTML += 1; throw new Error("outerHTML was set"); }
  insertAdjacentHTML() { calls.innerHTML += 1; throw new Error("insertAdjacentHTML was called"); }
  all() { return this.children.filter((c) => c.nodeType === 1).flatMap((c) => [c, ...c.all()]); }
  querySelectorAll(sel) {
    const m = /^li\[(data-[af])\]$/.exec(sel);
    if (!m) { throw new Error("selector not supported by the fake: " + sel); }
    return this.all().filter((e) => e.tagName === "LI" && e.getAttribute(m[1]) !== null);
  }
}

const tags = new Set();
const doc = {
  createElement(tag) { tags.add(String(tag).toUpperCase()); return new El(tag); },
  createTextNode(t) { return new Text(t); },
};

const downloads = [];
const anchors = [];
const revoked = [];
const env = {
  doc,
  Blob: class { constructor(parts, opts) { this.text = parts.join(""); this.type = opts && opts.type; } },
  URL: { createObjectURL(blob) { const url = "blob:fake/" + downloads.length; anchors.push({ blob, url }); return url; }, revokeObjectURL(u) { revoked.push(u); } },
};
const origCreate = doc.createElement;
doc.createElement = function (tag) {
  const el = origCreate.call(doc, tag);
  if (String(tag).toLowerCase() === "a") {
    el.click = function () { downloads.push({ name: el.download, href: el.href, text: anchors[anchors.length - 1].blob.text, type: anchors[anchors.length - 1].blob.type, inDocument: !!el.parent }); };
  }
  return el;
};

function storageObject(opts) {
  const map = Object.assign({}, opts.initial || {});
  return {
    getItem(k) { if (opts.throws) { throw new Error("blocked"); } return Object.prototype.hasOwnProperty.call(map, k) ? map[k] : null; },
    setItem(k, v) { if (opts.throws) { throw new Error("blocked"); } map[k] = String(v); },
    removeItem(k) { delete map[k]; },
    dump() { return map; },
  };
}

const storage = storageObject(input.storage || {});
const win = { localStorage: storage };

const main = new El("main");
const controls = new El("div");
main.appendChild(controls);
const lists = { a: new El("ul"), f: new El("ul") };
main.appendChild(lists.a);
main.appendChild(lists.f);
const items = { a: [], f: [] };
(input.report.areas || []).forEach((_, i) => { const li = new El("li"); li.setAttribute("data-a", String(i)); lists.a.appendChild(li); items.a.push(li); });
(input.report.findings || []).forEach((_, i) => { const li = new El("li"); li.setAttribute("data-f", String(i)); lists.f.appendChild(li); items.f.push(li); });

let clock = input.clock || 1767323045000;
const now = () => new Date((clock += 1000));
review.attach(doc, main, controls, input.report, win, { now, env });

function find(el, pred) { return el.all().filter(pred); }
function box(kind, index) { return items[kind][index]; }
function field(li, name) {
  const decide = find(li, (e) => e.className === "decide")[0];
  if (!decide) { return null; }
  if (name === "verdict") { return find(decide, (e) => e.tagName === "SELECT")[0]; }
  if (name === "reason") { return find(decide, (e) => e.tagName === "INPUT")[0]; }
  if (name === "author") { return find(decide, (e) => e.tagName === "INPUT")[1]; }
  if (name === "status") { return find(decide, (e) => e.className === "status")[0]; }
  return find(decide, (e) => e.tagName === "BUTTON" && e.textContent === name)[0];
}
function barButton(label) { return find(controls, (e) => e.tagName === "BUTTON" && e.textContent === label)[0]; }

const out = { errors: [], status: {}, downloads, revoked };
(input.steps || []).forEach((step) => {
  try {
    const li = step.kind ? box(step.kind, step.index) : null;
    if (step.op === "set") { field(li, step.field).value = step.value; }
    else if (step.op === "click") { field(li, step.button).click(); }
    else if (step.op === "bar") { barButton(step.button).click(); }
    else if (step.op === "read") { out.status[step.kind + step.index] = field(li, "status").textContent; }
    else if (step.op === "options") { out.status[step.kind + step.index + "-options"] = field(li, "verdict").children.map((o) => o.value); }
    else { out.errors.push("unknown step " + step.op); }
  } catch (err) {
    out.errors.push(String(err && err.message));
  }
});
out.tags = Array.from(tags).sort();
out.hasControls = { a: items.a.map((li) => !!field(li, "verdict")), f: items.f.map((li) => !!field(li, "verdict")) };
out.bar = find(controls, (e) => e.tagName === "BUTTON").map((b) => b.textContent);
out.barStatus = find(controls, (e) => e.className === "status").map((e) => e.textContent);
out.storage = storage.dump();
out.calls = calls;
out.anchorsInDocument = find(main, (e) => e.tagName === "A").length + find(controls, (e) => e.tagName === "A").length;
process.stdout.write(JSON.stringify(out));
