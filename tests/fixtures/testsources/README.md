# Test source fixtures (synthetic)

Everything here is invented for the M15 tests. Nothing was recorded from a live Qase, TestRail, Playwright or Cypress setup, and every
name, id, title and address is made up.

```text
qase/       project, suites (a tree three deep), cases (30, two pages of 15), 401, 404, 429, 500, a page that is not JSON and an
            answer with status false. Shaped like the Qase API v1: {"status": true, "result": {"total", "filtered", "count",
            "entities": [...]}}, limit and offset paging, a case code made of the project code and the id.
testrail/   project, sections (a tree three deep), cases (30, two pages of 15 linked by _links.next), 401, 403, 404, 429, 500,
            cut-off JSON, a next link to another host and the plain list an older server answers. Shaped like the TestRail API v2
            (TestRail 6.7 and later).
e2e/        a small web app (React Router screens for billing, cases and reports) with Playwright specs under e2e/ and Cypress specs
            under cypress/e2e/, and the branch topic/money-rounding that changes the Money component five screens render.
```

A response file is one response, in the form of `tests/fixtures/hosts`:

```json
{"status": 200, "headers": {"Content-Type": "application/json"}, "body": {...}}
```

`body_text` replaces `body` for a response that is not JSON. The tests serve them through `tests/support_testsources.py`, whose fake
transport fails the test on anything but a GET.

The Qase and TestRail corpora hold the same 30 titles, so that the matching tests read alike. Billing's words (`billing` and `invoice`)
are in exactly three titles each: that is under the floor of the document frequency cap (three cases, see `reachmap/testsources/words.py`),
so neither is "in too many cases". `amount` is in five titles, so it is.

`e2e/` is not one of the four M1 repositories: the M1 repositories, their shas and every `expected.json` are as they were. Its
history is `e2e/history.json` with an overlay per step, built by `tests/support_testsources.py` (the same builder code as M1, in
a temporary folder). The specs show what the reader has to get right:

- `e2e/billing.spec.ts` (Playwright): a literal, a template with a parameter, a literal id with a query and a fragment, a full address
  with an options object, and a `goto` in a comment.
- `e2e/cases.spec.ts`: a static screen beside a parameter screen (`/cases/new` and `/cases/:caseId`), member expressions in a template,
  an address in a variable, a concatenation, a template that starts with a substitution (these three are partial), and a `goto` in a
  block comment and in a string.
- `cypress/e2e/billing.cy.ts` and `misc.cy.js`: `cy.visit` with a literal, with a template whose substitution is a call, after a
  custom command, a concatenation and an options object (partial), and a comment.
- `src/lib/goto.test.ts` is a unit test of a helper that calls `goto`: no spec glob selects it.
