"use strict";
var review = (function () {
  /* Decisions: a verdict, a reason and a name per area and finding, kept in memory (and in the browser's storage as a convenience), and one
     file to export. The page never sends anything anywhere. The fingerprints in the file are the report's own: the page reads them from
     the report and computes nothing. Every piece of text is put in with createElement and textContent. */
  var FORMAT = "reachmap-decisions";
  var STORAGE_PREFIX = "reachmap-decisions:";
  var MAX_REASON = 2000;
  var MAX_AUTHOR = 100;
  var VERDICTS = { area: ["test", "sample", "skip"], finding: ["accept", "reject"] };
  var WORDS = {
    test: "marked to test", sample: "marked to sample", skip: "risk accepted, not tested",
    accept: "finding accepted as stated", reject: "finding rejected by the reviewer"
  };
  var CHOICES = { test: "Test", sample: "Sample", skip: "Skip (risk accepted, not tested)", accept: "Accept", reject: "Reject" };
  var NEEDS_REASON = { skip: true, reject: true };
  var ID_PATTERN = { area: /^a-[0-9a-f]{16}$/, finding: /^f-[0-9a-f]{16}$/ };
  var FINGERPRINT = /^[0-9a-f]{64}$/;
  var TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$/;
  var LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?:^|[^\uD800-\uDBFF])[\uDC00-\uDFFF]/;
  var KEYS = ["target_id", "verdict", "reason", "author", "fingerprint", "created_at"];

  function has(obj, key) { return Object.prototype.hasOwnProperty.call(obj, key); }
  function isText(v) { return typeof v === "string"; }
  function isObject(v) { return v !== null && typeof v === "object" && !Array.isArray(v); }

  /* The words for a decision: the verdict in this tool's words, the person's own words quoted and attributed. */
  function describe(verdict, reason, author) {
    var text = has(WORDS, verdict) ? WORDS[verdict] : "decision";
    if (reason.trim() !== "") { return text + " (reason from " + author + ": " + JSON.stringify(reason) + ")"; }
    return text + " (by " + author + ")";
  }

  /* Why a decision cannot be recorded, or an empty string. */
  function problem(kind, verdict, reason, author) {
    if (!has(VERDICTS, kind) || VERDICTS[kind].indexOf(verdict) < 0) { return "Choose a verdict."; }
    if (NEEDS_REASON[verdict] === true && reason.trim() === "") { return "A skip or a reject needs a reason."; }
    if (reason.length > MAX_REASON) { return "The reason is at most " + MAX_REASON + " characters."; }
    if (author.trim() === "") { return "Give your name."; }
    if (author.length > MAX_AUTHOR) { return "The name is at most " + MAX_AUTHOR + " characters."; }
    if (LONE_SURROGATE.test(reason) || LONE_SURROGATE.test(author)) { return "The text has a character that cannot be saved."; }
    return "";
  }

  /* The report's review data, or null when it has none (a report from before decisions, or one that did not complete): the key and the
     revision are in `review`, the id and fingerprint of every target on the area or finding itself. */
  function targetsOf(report) {
    var review = report && report.review;
    var key = isObject(review) ? review.key : null;
    if (!isObject(key) || !isText(report.ref) || typeof review.revision !== "number" || review.revision < 1) { return null; }
    if (!isText(key.workspace) || !isText(key.repo) || !isText(key.change) || !isText(key.slug)) { return null; }
    var out = { area: [], finding: [], byId: Object.create(null) };
    var lists = { area: report.areas, finding: report.findings };
    var kinds = ["area", "finding"];
    for (var k = 0; k < kinds.length; k += 1) {
      var kind = kinds[k];
      if (!Array.isArray(lists[kind])) { return null; }
      for (var i = 0; i < lists[kind].length; i += 1) {
        var entry = lists[kind][i];
        if (!isObject(entry) || !isText(entry.id) || !ID_PATTERN[kind].test(entry.id) || !isText(entry.fingerprint) || !FINGERPRINT.test(entry.fingerprint)) { return null; }
        var target = { id: entry.id, fingerprint: entry.fingerprint, kind: kind, index: i };
        out[kind].push(target);
        out.byId[entry.id] = target;
      }
    }
    return out;
  }

  function storageKey(report) {
    return STORAGE_PREFIX + report.review.key.slug + ":" + report.ref + ":" + report.review.revision;
  }

  /* What an earlier visit left in the browser's storage, checked item by item: only what this page would have written, for this report. */
  function readStored(win, key, targets) {
    var items = Object.create(null);
    var order = [];
    var raw = null;
    try {
      raw = win.localStorage.getItem(key);
    } catch (err) {
      return { items: items, order: order };
    }
    var list = null;
    try {
      list = raw === null ? null : JSON.parse(raw);
    } catch (err) {
      list = null;
    }
    if (!Array.isArray(list)) { return { items: items, order: order }; }
    list.forEach(function (item) {
      if (!isObject(item) || Object.keys(item).length !== KEYS.length) { return; }
      for (var i = 0; i < KEYS.length; i += 1) { if (!has(item, KEYS[i]) || !isText(item[KEYS[i]])) { return; } }
      var target = targets.byId[item.target_id];
      if (!target || item.fingerprint !== target.fingerprint || !TIME.test(item.created_at)) { return; }
      if (problem(target.kind, item.verdict, item.reason, item.author) !== "") { return; }
      if (!has(items, item.target_id)) { order.push(item.target_id); }
      items[item.target_id] = item;
    });
    return { items: items, order: order };
  }

  function writeStored(win, key, state) {
    try {
      win.localStorage.setItem(key, JSON.stringify(state.order.map(function (id) { return state.items[id]; })));
    } catch (err) {
      /* storage can be blocked or full: the decisions then last for this page only */
    }
  }

  /* The file to export: the report's key, head commit and revision, and each decision with the fingerprint the report holds for its target. */
  function exportDocument(report, targets, state, when) {
    return {
      format: FORMAT,
      version: 1,
      report: { key: { workspace: report.review.key.workspace, repo: report.review.key.repo, change: report.review.key.change }, head_sha: report.ref, revision: report.review.revision },
      exported_at: when,
      decisions: state.order.map(function (id) {
        var d = state.items[id];
        return { target_id: d.target_id, verdict: d.verdict, reason: d.reason, author: d.author, fingerprint: targets.byId[id].fingerprint, created_at: d.created_at };
      })
    };
  }

  /* The download is a link made for the click, to a Blob made here, and removed after it: no request is made. */
  function download(env, host, name, text) {
    var blob = new env.Blob([text], { type: "application/json" });
    var link = env.doc.createElement("a");
    link.href = env.URL.createObjectURL(blob);
    link.download = name;
    host.appendChild(link);
    link.click();
    host.removeChild(link);
    var url = link.href;
    setTimeout(function () { env.URL.revokeObjectURL(url); }, 1000);
  }

  function labelled(doc, labelText, control) {
    var label = doc.createElement("label");
    label.appendChild(doc.createTextNode(labelText + " "));
    label.appendChild(control);
    return label;
  }

  function button(doc, text) {
    var b = doc.createElement("button");
    b.type = "button";
    b.textContent = text;
    return b;
  }

  function attach(doc, main, controls, report, win, opts) {
    var options = opts || {};
    var now = options.now || function () { return new Date(); };
    var env = options.env || { doc: doc, Blob: win.Blob, URL: win.URL };
    var targets = targetsOf(report);
    if (!targets) { return; }
    var key = storageKey(report);
    var state = readStored(win, key, targets);
    var barStatus = doc.createElement("span");
    barStatus.className = "status";
    barStatus.setAttribute("aria-live", "polite");

    function count() {
      var n = state.order.length;
      barStatus.textContent = n + (n === 1 ? " decision" : " decisions") + " recorded in this page.";
    }

    function control(kind, target, li) {
      var wrap = doc.createElement("div");
      wrap.className = "decide";
      var choice = doc.createElement("select");
      var none = doc.createElement("option");
      none.value = "";
      none.textContent = "No decision";
      choice.appendChild(none);
      VERDICTS[kind].forEach(function (v) {
        var option = doc.createElement("option");
        option.value = v;
        option.textContent = CHOICES[v];
        choice.appendChild(option);
      });
      var reason = doc.createElement("input");
      reason.type = "text";
      reason.maxLength = MAX_REASON;
      var author = doc.createElement("input");
      author.type = "text";
      author.maxLength = MAX_AUTHOR;
      var status = doc.createElement("span");
      status.className = "status";
      status.setAttribute("aria-live", "polite");
      var record = button(doc, "Record decision");
      var clear = button(doc, "Clear");
      function show() {
        var d = has(state.items, target.id) ? state.items[target.id] : null;
        status.textContent = d === null ? "" : "Recorded: " + describe(d.verdict, d.reason, d.author) + ".";
      }
      var saved = has(state.items, target.id) ? state.items[target.id] : null;
      if (saved !== null) {
        choice.value = saved.verdict;
        reason.value = saved.reason;
        author.value = saved.author;
      }
      record.addEventListener("click", function () {
        var why = problem(kind, choice.value, reason.value, author.value);
        if (why !== "") { status.textContent = why; return; }
        if (has(state.items, target.id)) { state.order = state.order.filter(function (id) { return id !== target.id; }); }
        state.items[target.id] = { target_id: target.id, verdict: choice.value, reason: reason.value, author: author.value, fingerprint: target.fingerprint, created_at: now().toISOString() };
        state.order.push(target.id);
        writeStored(win, key, state);
        show();
        count();
      });
      clear.addEventListener("click", function () {
        if (has(state.items, target.id)) {
          delete state.items[target.id];
          state.order = state.order.filter(function (id) { return id !== target.id; });
          writeStored(win, key, state);
        }
        choice.value = "";
        status.textContent = "Cleared in this page.";
        count();
      });
      wrap.appendChild(labelled(doc, "Decision", choice));
      wrap.appendChild(labelled(doc, "Reason", reason));
      wrap.appendChild(labelled(doc, "Your name", author));
      wrap.appendChild(record);
      wrap.appendChild(clear);
      wrap.appendChild(status);
      li.appendChild(wrap);
      show();
    }

    var sets = [["area", "li[data-a]", "data-a"], ["finding", "li[data-f]", "data-f"]];
    sets.forEach(function (set) {
      Array.prototype.forEach.call(main.querySelectorAll(set[1]), function (li) {
        var target = targets[set[0]][Number(li.getAttribute(set[2]))];
        if (target) { control(set[0], target, li); }
      });
    });

    var exportButton = button(doc, "Export decisions");
    exportButton.addEventListener("click", function () {
      var file = exportDocument(report, targets, state, now().toISOString());
      download(env, main, "reachmap-decisions-r" + report.review.revision + ".json", JSON.stringify(file, null, 2));
      barStatus.textContent = "Exported " + file.decisions.length + (file.decisions.length === 1 ? " decision." : " decisions.");
    });
    controls.appendChild(exportButton);
    controls.appendChild(barStatus);
    count();
  }

  return { attach: attach, describe: describe, exportDocument: exportDocument, problem: problem, targetsOf: targetsOf };
}());

if (typeof module === "object" && module !== null && typeof module.exports === "object") { module.exports = review; }

(function () {
  /* The page works without this script. It adds interaction only: filters, a theme toggle and printing with
     everything open. It reads the report from the embedded JSON and builds elements with createElement and
     textContent; nothing from the report is ever turned into markup, a link or a source. */
  var dataNode = document.getElementById("report-data");
  var main = document.getElementById("main");
  var report;
  if (!dataNode || !main) { return; }
  try { report = JSON.parse(dataNode.textContent); } catch (err) { return; }
  if (!report || typeof report !== "object") { return; }

  var root = document.documentElement;
  var THEME_KEY = "reachmap-theme";

  function each(list, fn) { Array.prototype.forEach.call(list, fn); }
  function list(value) { return Array.isArray(value) ? value : []; }

  function readTheme() {
    try {
      var saved = window.localStorage.getItem(THEME_KEY);
      return saved === "dark" ? "dark" : "light";
    } catch (err) {
      return "light";
    }
  }

  function writeTheme(value) {
    try {
      window.localStorage.setItem(THEME_KEY, value);
    } catch (err) {
      /* storage can be blocked or full: the choice then lasts for this page only */
    }
  }

  /* Theme: light by default, a button to change it, remembered when storage works. */
  var theme = readTheme();
  root.setAttribute("data-theme", theme);
  var controls = document.createElement("div");
  controls.className = "controls";
  var toggle = document.createElement("button");
  toggle.type = "button";
  toggle.textContent = "Dark theme";  /* a fixed label: the button says whether it is on with aria-pressed */
  function paintToggle() {
    toggle.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
  }
  paintToggle();
  toggle.addEventListener("click", function () {
    theme = theme === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", theme);
    writeTheme(theme);
    paintToggle();
  });
  controls.appendChild(toggle);
  main.insertBefore(controls, main.firstChild);
  review.attach(document, main, controls, report, window, {});

  /* Chains, notes and the file list start folded (the page says so itself); printing opens everything and puts them back after. */
  var folds = main.querySelectorAll("details.fold");
  var wasOpen = [];
  window.addEventListener("beforeprint", function () {
    wasOpen = [];
    each(folds, function (d) { wasOpen.push(d.open); d.open = true; });
  });
  window.addEventListener("afterprint", function () {
    each(folds, function (d, i) { d.open = wasOpen[i] === true; });
  });

  /* Filters: by repository, area kind, priority and finding kind. The list items carry the index of their area or finding in
     the report; what a filter looks at is read from the report, not from the page. */
  var areas = list(report.areas);
  var findings = list(report.findings);
  if (!areas.length && !findings.length) { return; }

  function uniqueSorted(values) {
    var seen = {};
    var out = [];
    values.forEach(function (v) {
      if (typeof v === "string" && !Object.prototype.hasOwnProperty.call(seen, v)) { seen[v] = true; out.push(v); }
    });
    return out.sort();
  }

  function priorityOf(area) { return area.by_name ? "candidate" : String(area.priority); }
  function reposOf(finding) { return list(finding.evidence).map(function (e) { return e && e.repo; }); }

  var repos = uniqueSorted(areas.map(function (a) { return a.repo; }).concat([].concat.apply([], findings.map(reposOf))));
  var filter = { repo: "", areaKind: "", priority: "", findingKind: "" };
  var status = document.createElement("span");
  status.setAttribute("aria-live", "polite");

  /* A group is a heading, maybe a note, and the list after them: when a filter hides every item of the list the group goes with it. */
  function foldGroups() {
    each(main.querySelectorAll("ul"), function (list) {
      var items = list.querySelectorAll(":scope > li[data-a], :scope > li[data-f]");
      if (!items.length) { return; }
      var visible = false;
      each(items, function (li) { if (!li.hidden) { visible = true; } });
      var node = list.previousElementSibling;
      while (node && node.tagName !== "H3" && node.tagName !== "H2") {
        node.hidden = !visible;
        node = node.previousElementSibling;
      }
      if (node && node.tagName === "H3") { node.hidden = !visible; }
    });
  }

  function apply() {
    var shownAreas = 0;
    var shownFindings = 0;
    each(main.querySelectorAll("li[data-a]"), function (li) {
      var a = areas[Number(li.getAttribute("data-a"))];
      var ok = !!a && (!filter.repo || a.repo === filter.repo) && (!filter.areaKind || a.kind === filter.areaKind) &&
        (!filter.priority || priorityOf(a) === filter.priority);
      li.hidden = !ok;
      if (ok) { shownAreas += 1; }
    });
    each(main.querySelectorAll("li[data-f]"), function (li) {
      var f = findings[Number(li.getAttribute("data-f"))];
      var ok = !!f && (!filter.findingKind || f.kind === filter.findingKind) && (!filter.repo || reposOf(f).indexOf(filter.repo) >= 0);
      li.hidden = !ok;
      if (ok) { shownFindings += 1; }
    });
    foldGroups();
    status.textContent = "Showing " + shownAreas + " of " + areas.length + " areas and " + shownFindings + " of " + findings.length + " findings.";
  }

  function choose(labelText, values, key) {
    var label = document.createElement("label");
    label.appendChild(document.createTextNode(labelText + " "));
    var select = document.createElement("select");
    var all = document.createElement("option");
    all.value = "";
    all.textContent = "All";
    select.appendChild(all);
    values.forEach(function (v) {
      var option = document.createElement("option");
      option.value = v;
      option.textContent = v;
      select.appendChild(option);
    });
    select.addEventListener("change", function () { filter[key] = select.value; apply(); });
    label.appendChild(select);
    return { label: label, select: select, key: key };
  }

  var bar = document.createElement("div");
  bar.className = "filters";
  var chosen = [
    choose("Repository", repos, "repo"),
    choose("Area kind", uniqueSorted(areas.map(function (a) { return a.kind; })), "areaKind"),
    choose("Priority", uniqueSorted(areas.map(priorityOf)), "priority"),
    choose("Finding kind", uniqueSorted(findings.map(function (f) { return f.kind; })), "findingKind")
  ];
  chosen.forEach(function (c) { bar.appendChild(c.label); });
  var reset = document.createElement("button");
  reset.type = "button";
  reset.textContent = "Show everything";
  reset.addEventListener("click", function () {
    chosen.forEach(function (c) { c.select.value = ""; filter[c.key] = ""; });
    apply();
  });
  bar.appendChild(reset);
  bar.appendChild(status);
  var anchor = document.getElementById("sec-areas") || document.getElementById("sec-findings");
  if (anchor) { main.insertBefore(bar, anchor); }
  apply();
}());
