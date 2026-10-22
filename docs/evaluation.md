# Measuring whether it helps

A tool that says what to test is easy to build and hard to trust. Do not claim that Reachmap
helps, for your team or for anyone's, without measuring it, and report what you measured in the
way this page describes. The rules are about reporting honestly. Most of them exist because a
simple count made the tool look better than it was.

Contents: [what to measure](#what-to-measure), [how to report results](#how-to-report-results),
[the citation audit](#the-citation-audit), [reach beyond the diff](#reach-beyond-the-diff),
[human judging sheets](#human-judging-sheets), [what cannot be measured](#what-cannot-be-measured).

## What to measure

Three questions, from the one that is cheapest and most certain to the one that matters most:

1. **Are the citations true?** Every finding cites a line at a commit. Re-read each cited line at
   its commit and check that it is there and says what the report quotes. This is mechanical and
   has an exact answer. It found defects that no acceptance test could see, so run it after any
   change to how citations are made.
2. **Does the report reach what a change really affected?** Take changes from your history whose
   later follow-up change (a fix, a second pull request) touched files outside the original
   diff. Analyse the original as it was when it merged, and count whether the report cited any
   file of the follow-up. This is weak evidence and must be said to be weak.
3. **Do the listed screens and suggested cases make sense to a person?** One row per screen or
   suggested case, judged by a person who did not write the rules.

Whether it finds the defects that matter is a fourth question, and it cannot be answered
without data most teams do not have. See [what cannot be measured](#what-cannot-be-measured).

## How to report results

These apply to every result you write down, whatever tool made it.

**Counts, not percentages.** Write "3 of 20", never "15%". On a small sample a percentage
suggests a precision the sample does not have, and a reader remembers the percentage and forgets
the 20. Give the number of cases, the number of hits, and the number of cases you set aside and
why.

**Independence on both axes.** No two cases may share a subject, and no two may share a witness.
A subject is the thing the change was about, and a witness is the follow-up change used to judge
it. Each subject and each witness may appear in at most one case. When several cases share one,
keep a single case of them and say how many you dropped. One follow-up change that is the witness
of three original changes is one piece of evidence counted three times, which is the easiest way
to make a result look better than it is.

**Weak small samples are stated as weak.** Twenty cases are a probe, not a verdict. Say how many
cases there were, say that the sample is small, and do not compare two small counts as if the
difference were a measurement. A report that quotes a hit count without the sample behind it is
not a result.

**Exclusions are not predictions.** Score findings that say something is reached. Drop
findings that say something is not reached (the exclusions): "not reached" is a claim that the
absence of a follow-up cannot confirm. Match files exactly, or at a path boundary, never as a
substring.

**Reverts cannot test reach.** A revert undoes exactly the diff it reverts, so it touches
nothing outside it. Leave reverts out of the cases.

**A person judges, not a model.** The judging of screens and suggested cases is done by a
person, and before you tune any rule, so that the judging is not shaped by the tuning. A model's
opinion may be recorded as a note next to a report, and it is never a result.

**State what was not looked at.** A run that could not establish something (see
[rules](rules.md#what-could-not-establish-means)) says so in its report. A result that counts
such a run as a miss, or leaves it out without saying, hides the thing that most needs to be
known.

A result written down in a form like this is a result you can be held to:

```text
Citation audit: <N> citations in <R> reports re-read at their commits; <K> did not resolve.
Reach beyond the diff: <C> cases of <T> candidate changes (<T - C> set aside: <reasons>).
  Independent by subject and by witness: yes (<n> cases removed to make it so).
  Reports that cited a file of the witness: <H> of <C> (findings only); <S> of <C> (screens too).
  The sample is small and the result is weak.
Judged by: <names of the people>, before any rule was changed.
```

The angle brackets are blanks to fill in. Leave none empty.

## The citation audit

A citation is a repository, a path, a line, the line's text and a commit. The audit reads each
one back from git at that commit and compares the line. A report whose every citation resolves
has not been shown to be right; a report with one that does not resolve has been shown to be
wrong somewhere, and that is the defect to find.

Without any tool you can check one citation by hand. In the example workspace, the report for the
first change cites `src/modules/billing/routes.tsx:9` at `7fbf97d99854`:

```text
git show 7fbf97d99854:src/modules/billing/routes.tsx
```

run in the `acme-web` repository shows the file at that commit, and line 9 is the `<Route>`
line the report quotes.

The project's own test suite runs this check over the reports of every fixture scenario, and
fails when one citation stops resolving.

### Running it: `reachmap audit`

```text
reachmap audit REPORT.json... [--config reachmap.toml] [--show N] [--allow unreadable]
```

Give it one report or several (a shell glob such as `reports/*.json` does for a folder). The
reports do not say where their repositories are, so give the configuration they were made with:
the audit finds each repository's remote there and reads the commits through the same git layer
as `reachmap analyse`, using the same cache. A commit that is not in the cache is fetched, so an
audit needs the network unless the cache already holds the commits.

Every citation anywhere in a report is audited: the findings and areas, the branch rows, every
pass and hop of the propagation, call sites, consumers, contract reads, suggestions. Each distinct
citation is read once, in a batch per commit, so a report with thousands of citations costs a few
git processes. Each gets one answer:

| answer | meaning |
|---|---|
| `ok` | the file has that line and its text equals what the report quotes (a trailing carriage return of a CRLF line is not part of the text) |
| `mismatch` | the file is there and the line is not: another text, or no such line. Both texts are shown |
| `absent` | the path is not in the commit |
| `unreadable` | nothing was established: the repository is not in the configuration, the commit could not be fetched, the file could not be read. Never counted as `ok` and never as a mismatch |

Beside the citations it checks, from the report alone, that every finding has evidence, that every
finding and area hashes to its own id and fingerprint, that no id is repeated, and that each claim
on a line (the import, route, definition, wrapper and operation claims) is well formed and agrees
with its line where the line can say so. It also reads again the verified proposed files of the
model notes stored beside the report. A note store or decision store that could not be read is
counted as one `unreadable`, because the notes in it were never audited.

What it prints, per report and then in total (counts only):

```text
acme-web-compare-main-scenario-s01-shared-component-8b4ea2a7-7fbf97d99854-r1.json: FAILED
  citations: 32 distinct (ok 29, mismatch 1, absent 1, unreadable 1); 121 places cite them
  structure problems: 0
  mismatch: acme/web src/modules/billing/routes.tsx:9 at 7fbf97d99854 (areas[0].route_line)
    report: <Route path="/billing/invoices" element={<InvoiceListPage />} /> // edited
    commit: <Route path="/billing/invoices" element={<InvoiceListPage />} />
  absent: acme/web src/nowhere.tsx:9 at 7fbf97d99854 (areas[2].route_line)
    report: <Route path="/cases" element={<CaseListPage />} />
    why: the path is not in the commit
  unreadable: acme/web src/modules/cases/routes.tsx:10 at 333333333333 (areas[3].route_line)
    report: <Route path="/cases/:caseId" element={<CaseDetailPage />} />
    why: commit 333333333333 is not available: ...
1 report: 32 distinct citations (ok 29, mismatch 1, absent 1, unreadable 1), 0 structure problems; 1 report(s) failed
```

`--show N` lists the first N failures of each report (default 10) and counts the rest.

Exit code: 0 only when every report was audited and has no mismatch, no absent citation, no
unreadable one and no structure problem. 1 when any does. A report from a run that failed or timed
out has nothing to read and is never a pass: it is printed as `NOT AUDITED` and the exit code is 1.
`--allow unreadable` lets an audit without network pass over citations that could not be read, and
still says how many there were; it never forgives a mismatch, an absent citation or a structure
problem. 2 for a file that is not a report reachmap can read (not JSON, another major version, a
completed report without its lists), a missing file or configuration, or a bad option.

A pass says that every citation resolves at its commit. It does not say the analysis found
everything, and it does not re-run the analysis, so a claim that is consistent and wrong is not
found.

## Reach beyond the diff

The question: when a change was followed by another change that fixed something the first one
broke, did the first one's report point at the files of the second?

The method, in the order you do it:

1. From your history, choose original changes that have a **witness**: a later change, in the
   same repositories, that fixed a defect the original introduced. A person decides what counts as
   a defect and as caused by the original, and writes the link down. A witness that merely
   follows the original in time is not one.
2. Keep only witnesses that touch files **outside** the original diff. A witness inside the
   original's own diff says nothing about reach.
3. Apply the independence rule: no two cases share a subject or a witness.
4. Analyse each original **as of its merge time** with `--as-of`, so that the report cannot use
   anything that came after. `--as-of` pins the branches and heads read by name, and a commit
   named by hash is already a pin. It does **not** pin the tags of a producer package (they are
   listed as they are now), the commits a host reports for a pull request, or the hosted test
   sources (Qase and TestRail are read as they are now). So `release` findings, which depend on
   the tags and the pins, are not as of the merge time: discount them for witness cases, and score
   findings and areas, not `release` findings. Prefer a comparison or a range named by commit
   hashes over a pull request number.
5. Count whether the report cited any file of the witness: first for findings alone, then also
   counting the screens and routes of the areas.
6. Write the result as counts, with the sample size and the word "weak" if it is.

### Running it: `reachmap witness`

```text
reachmap witness CASES.toml [--config reachmap.toml]
```

The case file is written by a person: the links between an original change and its witness are a
judgement, and nothing here finds them for you. Each case is one table:

```toml
config = "../reachmap.toml"        # optional, relative to the file; --config wins

[[case]]
id = "c01"
repo = "acme/web"                   # the repository of the original change
merged_at = "2023-11-15T09:13:20Z"  # the original is analysed as of this time
original = { compare = "main...topic" }                    # or pr = 12, or range = "a..b"
witness = { range = "topic..fix", repo = "acme/web" }      # repo defaults to the case's
```

The original is always in the case's `repo`; only the witness may name another repository (a
screen can be in another repository than the change that reaches it). For each case the harness
analyses the original as of `merged_at`, then looks at the witness files that are outside the
original diff (the original's changed paths, and the paths its renames came from, are inside) and
counts whether the report cited one. Paths are compared exactly, in one repository: a file is a
hit when the report cites that very path, never a suffix of it and never a substring.

- **By findings**: a finding that is not an exclusion cites a witness file.
- **By screens**: an area that is reached through imports (not a candidate found by name) cites
  one in a chain or its route line.
- **By any evidence**: a finding that is not an exclusion, or any area, a candidate included.

Exclusions are not predictions: an exclusion never counts. Nor does a `release` finding, because it
rests on producer tags and consumer pins that `--as-of` does not move. A witness file that only an
exclusion or only a release finding cites is listed apart, and is not a hit.

The harness refuses the whole case set, before it analyses anything, and says which cases and why,
when:

- two cases share a **subject** (the same pull request, the same head commit, or the same set of
  changed files in a repository) or a **witness** (the same, for the witness change);
- a witness is a **revert**: its diff holds the inverse of the original's for every file of the
  original, with or without other files beside it. A revert undoes the diff, so it cannot test
  reach, and a fix committed next to a revert is not evidence of reach either;
- a witness has no file outside the original diff, is not later than `merged_at`, or a change
  cannot be read.

Two cases whose witnesses are different changes can still both cite one file. That file is listed
("counted for more than one case") so that you can decide what it means; it is not refused.

What it prints:

```text
witness cases: 4 read, 4 analysed
  c01 (acme/web, as of 2023-11-15T09:13:20Z): hit by findings, screens, any evidence; 1 witness file outside the original diff; cited: acme/web:src/modules/billing/pages/InvoiceListPage.tsx
  c02 (acme/api, as of 2023-11-15T06:13:20Z): hit by screens, any evidence; 1 witness file outside the original diff; cited: acme/web:src/modules/cases/routes.tsx
  c03 (acme/web, as of 2023-11-15T16:13:20Z): no witness file cited; 1 witness file outside the original diff
  c04 (acme/web, as of 2023-11-15T12:13:20Z): no witness file cited; 1 witness file outside the original diff
    cited only by an exclusion, which is not a prediction and does not count: acme/web:src/modules/reports/pages/ReportsHomePage.tsx
hits: 1 of 4 by findings; 2 of 4 by screens; 2 of 4 by any evidence
```

followed by what `--as-of` does not pin (the tags of a producer, the commits a host reports for a
pull request, the hosted test sources) and the sentence that small samples are weak and that no
recall number exists. A run that failed or timed out is listed as not analysed and is in no count:
the counts are still printed and the exit code is 1. A run with blocking records is counted and said
to be incomplete, because its miss may be something the analysis could not establish. Exit code 2
means the case set was refused or could not be read, and nothing was counted.

The case file is the place to keep the person's decision: which change was a fix for which, and
why. The example cases in the project's tests (`tests/fixtures/eval/cases/`) show a hit by findings,
a hit by screens only, a miss, a file only an exclusion cites, and one refused set for each rule.

## Human judging sheets

For the third question, the report's listed screens and its suggested cases go onto a sheet, one
row per screen or per suggested case, with columns for a person to fill in (is it a place that
could be affected by the change, and a note). The person who judges is not a model and has not
seen how the rules were tuned. The sheet is filled in before any rule is changed in response to
it.

### Running it: `reachmap sheet`

```text
reachmap sheet REPORT.json [--out sheet.csv] [--format csv|md]
reachmap sheet --score FILLED.csv
```

The first form writes the sheet: one row for each screen or route the report lists (every
priority, candidates found by name included) and one for each suggested case. The columns are
`report key`, `revision`, `kind` (the kind of the area, or `suggested case`), `area id`, `label`,
`priority`, `chain (first and last hop)`, and then four that are left empty for a person:
`relevant (yes / no / unsure)`, `would have tested (yes / no)`, `notes` and `judge`. Write your
own name in `judge`. A file name ending in `.md` (or `--format md`) gives the same rows as a
Markdown table, to read or print; the CSV is the form that is scored.

The CSV is RFC 4180 text in UTF-8 with a byte order mark, so a spreadsheet opens it as UTF-8, and it
is protected against formula injection: a cell that starts with `=`, `+`, `-`, `@`, a tab or a
carriage return is written with a leading quote, so that a label or a quoted source line cannot run
as a formula. Reading removes that quote again, so a sheet written and read back is the same sheet.
Text from the report is made visible first (control and direction characters are written out).

**A model must not fill a sheet.** Sheets are for people. The MCP server has no tool that writes,
fills or scores a sheet, and nothing else in Reachmap does either; the judge column is a person's own
word and no tool can check it, so keep the sheets under your own control. Fill them before you
change any rule.

The second form counts a filled sheet and never changes it: how many rows were answered, the
answers per column, the relevance answers per kind and priority, how many rows have notes, and the
rows per judge. Counts only. It refuses (exit 2, naming the file and the row) a sheet with no
`judge` column, a header that is not the sheet's, an answer that is not one the column allows, an
answer with no judge name, a repeated row, a row of the wrong width, or a file with no rows. Answers
may be written in any case.

Suggested test cases are judged on their own sheet and never counted as coverage. A suggestion
that a person marks as a place that could be affected is still only a place to look.

## What cannot be measured

**Recall against real regressions needs defects linked to the change that caused them.** To say
"the tool would have pointed at the area that broke", you need a list of defects that escaped, each
one tied to the change that introduced it, with the area that broke written down. Most
organisations do not record this. Without it, no honest recall number exists, for this tool or for
any alternative, including your current process. A figure that claims otherwise has been made by
counting something else.

So:

- Do not quote a recall, a miss rate, or a "percentage of regressions caught".
- Do not compare the tool with your current process on regressions unless both were scored on the
  same linked defects.
- Start linking each escaped defect to the change that caused it now, in a place you can query
  later. In a year you will have data that a recall count can be made from.

What you can say, with the counts above, is narrower and true: how many citations resolved, how
often a follow-up fix was within the report's reach on a small independent sample, and what people
who judged the listed screens thought of them.
