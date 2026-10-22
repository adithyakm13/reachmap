# How to read a Reachmap report: what it says, and what it never says

Reachmap answers one question about a change: what does it reach, and what could you test?
It answers with evidence you can re-read. This page is for the person who reads the report
and decides what to test. It explains the few rules the tool keeps in every output, so that
you know how far to trust each line and what a missing line does not mean.

## What Reachmap never claims

**It never claims safety.** No report says a release is fine to ship. A report lists what a
change reaches, with the lines that show it, and what the run could not look at. Deciding
whether to release is a person's job, and the report is one input to it.

**It never claims coverage.** No report says a test covers a change, and none says that a
screen needs no testing. The tool cannot know what a test really exercises, so it does not
guess. Two things follow:

- A **suggested test case** (from a test management system or a local end-to-end spec) is a
  place to look. It sits under "Suggested, not verified coverage", in its own section, and it
  is never counted as a test mapped to an area, and no report says an area has a test that
  handles it because a suggestion exists. In 0.1.0 the tool makes suggestions only: it maps no
  test to an area.

The analyser enforces this in code: every sentence the tool writes itself is checked against a
list of phrases that claim safety or coverage, and a phrase on the list is refused and never
reaches a report. Text quoted from your repositories (a route, a path, a line of code) is shown
as quoted content and is not the tool's own wording.

**It makes no model calls.** The analyser is deterministic: the same repositories and the same
change give the same areas, findings and records. Only the revision number and the `run` block,
which holds times, differ between two runs. A model client can read a report and add notes beside it (see
[Notes by a model](#notes-by-a-model)), and those notes are never findings.

## Every claim cites a line

Each finding and each area carries evidence: a repository, a file, a line number, the line
itself, and the commit it was read at. In a report it looks like this:

```text
`acme/web` `src/modules/billing/routes.tsx:9` at `7fbf97d99854`
    <Route path="/billing/invoices" element={<InvoiceListPage />} />
```

You can open that file at that commit and find the line. A finding with no citation does not
exist, and this holds for the things the tool excludes too: a "not reached" finding cites why,
for example the import line that names only other exports of the changed file.

Reads happen at the commit that is cited. Files are read from that commit and searches run on
it, never on the default branch as it is today, so a citation and the reading it came from
always agree.

## What "could not establish" means

Every read of a file, a search or a spec answers one of three things: **found**, **absent**
(the tool positively saw that it is not there), or **unreadable** (a read failed, a search was
cut short, a file did not parse). An unreadable read is never turned into "absent". If it
were, a rate limit would read as "nothing imports this file" and a short list would look like
the whole list.

When a run could not establish something, it writes a record. A record says:

- what kind of thing it is (`read_unreadable`, `hub_cutoff`, `edge_unfollowed`, and so on),
- the **subject**: the file, route or repository it is about,
- **what was not established**, as a plain sentence naming the conclusion the run cannot
  support,
- a **detail** with the reason, and
- whether it **blocks**.

This is the part of a report that says what is outside it. From a real run of the example
workspace (a contract field changed in one repository):

```text
- `edge_unfollowed` `edge:event:acme/api:acme/worker`
  - Not established: What lies past edge:event:acme/api:acme/worker was not established,
    because no adapter follows that kind of edge yet
  - Detail: the change reached code at one end of an edge of the kind 'event' that no
    adapter follows yet
- `entry_points_unread` `acme/worker`
  - Not established: What acme/worker runs was not established, because its entry points
    are of a kind that no adapter reads yet
```

Read a record as "the report is silent here, on purpose". The list of areas has no entry for
what lies past an unfollowed edge, and that silence is not a finding that nothing lies there.

The common causes, in plain words:

| Cause | What it means for you |
|---|---|
| A read failed (`read_unreadable`) | A file or the diff could not be read, so what it holds, or that it exists, was not established. Blocking. |
| A search was cut short (`search_truncated`) | More matches may exist. Blocking. |
| The change has no earlier version (`pre_change_unavailable`) | Removed routes, removed calls and narrowed exports cannot be computed. Blocking. |
| The diff and the host disagree (`diff_count_mismatch`) | The full list of changed files was not established. Blocking. |
| The run reached its time limit (`timed_out`) | The report has no counts and no findings. Blocking. |
| An import or a mount could not be resolved (`specifier_unresolved`, `mount_unresolved`, `routes_unread`, `edge_incomplete`) | A path from the change to an entry point may exist that the run could not follow. Blocking. |
| A routing glob or search matched nothing (`routes_unmatched`) | The configuration points at routing files and the tree has none: a typo in `routes_glob`, or a change that deleted or moved them. No screen or route was read. Blocking. |
| A configured spec cannot be read (`spec_unreadable`) | The routes or operations it declares are not known: it is missing, not JSON, or YAML, which is not read. Blocking. |
| A stated limit was reached (`hop_cap`, `module_cap`, `hub_cutoff`) | The walk stopped on purpose and says where. Not blocking. |
| A name is too common to link by (`not_distinctive`) | Links made through that word alone were dropped, so the word does not pull in unrelated code. Not blocking. |
| A kind of edge or entry point has no adapter yet (`edge_unfollowed`, `entry_points_unread`) | The configuration names it and the tool does not read it. Not blocking. |
| A test source is missing, unconfigured or did not answer (`source_unconfigured`, `source_unavailable`, `suggestions_incomplete`) | Suggestions are missing or partial. See scope below. |

A **timeout is not a result.** A run that hits its limit is reported as `timed_out`, with no
areas and no findings, never as a short list. The same holds for a failed run.

## Blocking and non-blocking records, and the scope field

A record **blocks** when the run needed something and could not read it, so an empty or short
answer may be a failure of the tool and not a fact about your code. A blocking record in the
`selection` scope means the list of areas may be missing items. The command line summary starts
with it, before the areas, and the report lists the records before the areas too. From a real
run on a branch whose history has no merge base with the branch it is compared with:

```text
Blocking: Removed routes, removed calls and narrowed exports for scenario/s16-no-merge-base were not established, because there is no pre-change version (pre_change_unavailable: scenario/s16-no-merge-base)
Blocking: Which screens or routes acme/web:src/modules/*/routes.tsx serves was not established, because nothing in the tree matches what the configuration looks for (routes_unmatched: acme/web:src/modules/*/routes.tsx)
reachmap 0.1.0: acme/web, compare main...scenario/s16-no-merge-base, head 5862ef935e34
Areas to test: none were established; the blocking records above say what could not be read.
The lists above may be incomplete: the report says what was not established.
```

Notice what the tool does not say: it does not say that no area is reached. It says none
were established.

A **non-blocking** record states a limit or an optional source: the run tells you what is
outside what it looked at, and the areas and findings it did list are not affected by that record. Several of them can
still make a run incomplete (an unfollowed edge, a reached hop cap), and then the report says
the run did not establish everything. A run that established everything says nothing about
being complete: the word is used only for what is not.

Each record has a **scope**:

| Scope | What it concerns | Blocks the areas? |
|---|---|---|
| `selection` | The areas and findings: what the change reaches. | Yes, when the record is blocking. |
| `suggestions` | The suggested test cases from a test source. | Never. |

The areas come from the code, so a test source that is down or misconfigured cannot make them
incomplete. A `suggestions` record that blocks (`source_unavailable`, `suggestions_incomplete`)
blocks only the claim "none suggested": the report will not say that no case was found when
the source could not be read. In the report's counts, `blocking_records` counts the `selection`
records and `blocking_suggestion_records` the others.

A missing optional source is recorded and does not block. A missing required read does.

## Risk and confidence are two different things

Every finding has both, and they are never averaged into one score.

- **Risk** is how bad it would be if the claim is true. A change to a field that other
  services must now supply is high risk.
- **Confidence** is how well the evidence supports the claim. A match made only because two
  things share a name is low confidence.

A severe consequence on thin evidence is **high risk, low confidence**, and the report shows
both. The clearest case is a route the change removes while a client still names its URL: the
finding is high risk, and its confidence is low because the link between them is made by URL
only.

Confidence is the weakest link of the best chain: the tool picks the strongest chain from the
change to an area, and its confidence is that of the chain's weakest step. The **confidence
basis** line says why, weakest first, in fixed terms: `import-backed`, `typed read`, `http
edge`, `by name`, `guard proven`, `undecidable`, `partial read`, `pin read`.

| Finding kind | What it says | Risk | Confidence |
|---|---|---|---|
| `direct` | The change reaches an area in the same repository. | medium | high when import-backed and read whole; medium when a call site cannot be decided (treated as affected) or a read was partial; low by name |
| `cross-repo` | The change reaches an area in another repository. | medium | as `direct`; medium for an HTTP match that is a literal in a parameter, a tie or of unknown method |
| `exclusion` | A file is not reached, with the evidence why. | low | high: the importer's own import line names only other exports |
| `guard-excluded` | A call cannot enter the changed code, with the guard and the call. | low | high |
| `contract-tightened` | A shared field became stricter and consumers read it. | high | per consumer: high when every read is typed; medium when one is by name; low when no read was found |
| `contract-relaxed` | A shared field became looser. | medium | as `contract-tightened` |
| `removed_route_still_called` | A route is gone and a client still names its URL. | high | low |
| `added_route_uncalled` | A route is new and no client calls it yet. | low | medium |
| `operation-changed` | An operation of a committed spec changed. | medium | high when both spec versions were read and compared; medium when a reference points outside the spec |
| `release` | Whether a consumer has the change, per branch, from its pinned version and the producer's tags. | high when adopted; medium on adoption or undecided | high when decided from the pin and the tag's ancestry; low when not |
| `branch-missing` | The change is on one branch and not on another. | high | high when all changed files agree, medium for at least half, low below that |

### Priority

An area is **must test** when the change reaches eight screens or fewer, and **sample** when it
reaches more: past eight, every area is still listed and the priority tells you the tool does not ask for all of them. The
count that decides is the number of screens reached through imports. An area found only by a
shared name is a **candidate**: it is shown apart and is never must test, because a name match
can be a coincidence.

### Undecidable means affected

When the tool cannot decide whether a call reaches the changed code (an argument that is not a
literal, a name it cannot resolve), it treats the call as affected and says so, with a lower
confidence. A call is excluded only when the change sits under a guard that the call provably
cannot enter. The tool leans toward listing too much over listing too little.

## Decisions are risk acceptance

In the workspace or with `reachmap decide`, a person can record what they will do about an area
or a finding:

| Target | Verdicts |
|---|---|
| an area | `test`, `sample`, `skip` |
| a finding | `accept`, `reject` |

A reason is needed for `skip` and `reject`. A `skip` reads, in every output, **risk accepted,
not tested**, with the reason and who gave it:

```text
area /cases (a-fe06a0e8ed4389b0): risk accepted, not tested (reason from Quinn: "only the badge colour changed here")
```

A decision is a record of a person's judgement. It is not evidence, and it changes nothing in
the analysis: no priority, risk, confidence, count or gap moves because someone decided. A
`skip` does not make the area "handled" anywhere in a report.

A decision is tied to the **claim** it was made about (a fingerprint of the evidence that says
what is reached), not to the commit. When you rerun on a later commit, each decision is one of:

- **current**: the claim is the same, so the decision still applies;
- **needs review** (stale): the claim changed, for example a new route to the area or a
  different import. The decision is not applied, and the report shows what changed when the
  earlier report is still beside the new one;
- **orphaned**: the area or finding is gone from the new report.

Reformatting a file, or spelling an import another way that means the same file, does not make
a decision stale.

## Notes by a model

A model client connected over MCP can add notes beside a report. A note is stamped with
"recorded via MCP, client reported as" and the client's own name, which is the client's claim.
Notes are kept apart from the report, are never findings, evidence or coverage, go stale when
the analysis is rerun, and are shown under "Model notes, recorded via MCP; not evidence." A
note that proposes a file is marked verified only when that file exists at a commit the report
read and names the change by a distinctive name in code, outside a comment; even then it is a
note, and you should read the file.

## Quick guide to a report

1. Read the header and the line about blocking records first. If a record blocks, treat the
   list of areas as possibly short.
2. Read **Areas to test**: must test first. For each area open the chain of lines.
3. Read the findings for risk and confidence together, not one of them.
4. Read **What the run could not establish** to see what is outside the report.
5. Use the branch matrix when the change must reach several release branches, and the author
   view for the files beyond the diff and the unit tests to run.
6. Treat suggestions as places to look.
7. Decide, with a reason. Your decision is risk acceptance and travels with later revisions.
