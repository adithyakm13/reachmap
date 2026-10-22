// The stand-in for the workspace: the list of reports and jobs. The token is in the address fragment (it never reaches a server or a log): it is
// read once, taken out of the address bar and the history, kept in this variable only, and sent in a header. Everything from the API is
// written with textContent, never as markup. A report is offered as a download: its page has a policy of its own, which a page opened from a blob
// of this one would not keep.
(function () {
  "use strict";
  var match = /(?:^|[#&])token=([A-Za-z0-9_-]+)/.exec(location.hash);
  var status = document.getElementById("status");
  if (!match) { return; }
  var token = match[1];
  history.replaceState(null, "", location.pathname + location.search);
  function call(path) {
    return fetch(path, { headers: { "X-Reachmap-Token": token }, cache: "no-store", credentials: "omit" });
  }
  function fill(id, rows) {
    var list = document.getElementById(id);
    list.textContent = "";
    rows.forEach(function (row) { list.appendChild(row); });
  }
  function download(name) {
    call("/api/reports/" + encodeURIComponent(name) + ".html").then(function (r) { return r.ok ? r.blob() : null; }).then(function (blob) {
      if (!blob) { return; }
      var url = URL.createObjectURL(new Blob([blob], { type: "text/html" }));
      var link = document.createElement("a");
      link.href = url;
      link.download = name + ".html";
      link.rel = "noopener";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      setTimeout(function () { URL.revokeObjectURL(url); }, 10000);
    });
  }
  function reportRow(r) {
    var li = document.createElement("li");
    var code = document.createElement("code");
    code.textContent = r.name;
    li.appendChild(code);
    li.appendChild(document.createTextNode(" " + r.status + (r.revision ? ", revision " + r.revision : "")));
    var button = document.createElement("button");
    button.textContent = "Download HTML";
    button.addEventListener("click", function () { download(r.name); });
    li.appendChild(button);
    return li;
  }
  function jobRow(j) {
    var li = document.createElement("li");
    li.textContent = j.id + " " + j.repo + " " + j.state;
    return li;
  }
  function refresh() {
    Promise.all([call("/api/reports"), call("/api/jobs")]).then(function (rs) {
      if (!rs[0].ok || !rs[1].ok) { status.textContent = "The runner refused the request: open the address it printed, with the part after the # sign."; return null; }
      return Promise.all([rs[0].json(), rs[1].json()]);
    }).then(function (data) {
      if (!data) { return; }
      status.textContent = "Connected.";
      fill("reports", data[0].reports.map(reportRow));
      fill("jobs", data[1].jobs.map(jobRow));
    }).catch(function () { status.textContent = "The runner cannot be reached."; });
  }
  refresh();
  setInterval(refresh, 3000);
})();
