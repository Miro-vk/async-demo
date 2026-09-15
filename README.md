# Client intake triage

A law firm's `info@` inbox gets a lot of mail. Most of it is marketing, some of it is
existing clients asking about invoices, and a few of them are people with a legal
problem who might become clients. This reads each one and decides what happens to
it — classify, extract, check for conflicts, route — rather than summarising it for
someone else to act on.

The interesting part is not the extraction. It is what the system does when it
isn't sure.

```bash
docker compose up        # then open http://localhost:8000
```

No API key, no network, no external services. The whole run replays from a cached
set of model responses committed to this repository.

---

## What this is not

**The data is synthetic.** All 51 emails, 200 clients and 150 matters are generated
from a seed. No real person wrote any of it, no real firm's records are involved,
and the names are drawn from word lists. There is no email integration: the inbox
is a table.

**Conflict checking is naive string matching, not a conflicts database.** It
normalises names, expands a hand-written list of abbreviations, and compares tokens.
It gets `Meridian Manufacturing Corp.` and `Meridian Mfg. Corp.` right. It has no
idea that a company is a wholly-owned subsidiary of a client under a completely
different name, because there is no corporate-family graph here, no entity
resolution service, and no beneficial-ownership data. A real conflicts system is
mostly that missing data, and this has none of it.

**There is no legal rules engine.** Nothing computes a limitation period, applies a
court rule, or knows anything jurisdiction-specific. Jurisdiction is extracted as a
string and validated only to the extent of checking it names a US state.

**Nothing is ever sent.** There is no send path, no SMTP, and no `SENT` state in the
enum. Approving an acknowledgment moves it to `approved_not_sent` and that is the
last thing that happens to it. A test asserts the only mutating route in the entire
API is the review endpoint.

---

## Why abstention and auditability, in this domain specifically

Most of what makes an intake assistant valuable is throughput, and most of what
makes one dangerous is that the failure modes are quiet and expensive.

**A wrong conflicts answer does not look wrong.** If the system misses that an
opposing party is an existing client, nothing visibly breaks. The matter opens, work
begins, and the problem surfaces weeks later when it costs a disqualification, a
fee write-off, and a malpractice conversation. Compare that to the cost of asking:
one email in a queue, thirty seconds of a person's attention. The asymmetry is
extreme, and it is the entire argument for the system leaning heavily toward asking.

That is why any conflict hit at all reaches a human, including the weak ones. The
email-domain rule cannot tell the difference between a client's general counsel
writing about new work and an employee writing about something adverse to their own
employer. Those two need opposite handling, and no amount of tuning will let that
signal separate them — so it always asks.

**"No conflicts found" and "we had nothing to check" must not look the same.** An
empty conflicts list from a check that had no party names to look up is not a
clearance, and this system tracks the difference explicitly: `Resolution` carries
`checked_party_names`, and a check that ran against nothing is flagged as vacuous.
One email in the corpus is a person writing on an injured friend's behalf without
naming her. The model correctly identified the sender as a third party. An earlier
version of the policy checked only that *some* party existed, so the conflicts check
ran against the wrong person, came back clean, and the email proceeded. A clean
result about somebody who is not the client is worse than no result.

**A confidence score a lawyer cannot check is worse than no score.** Language models
are confidently wrong in a register indistinguishable from being confidently right,
and their self-reported confidence is not calibrated. So every extracted value in
this system carries a character span pointing at the text it came from, and the
model is asked for a verbatim quote rather than for offsets — offsets are computed
here, and a quote that does not appear in the email is detectable. The confidence
shown to a human is not the model's number; it is that number multiplied by whether
the quote could be grounded and whether the value survived a field-specific check.
An ungrounded value is capped at 0.40 no matter how certain the model claimed to be.

The interface shows the arithmetic rather than the result. `0.28` tells a reviewer
nothing. `self-reported 0.95 | span not_found ×0.30 | validator passed ×1.00 = 0.28`
tells them the model was sure and could not back it up.

---

## What it does

Four stages, each a pure function over typed models, each independently testable.

| Stage | What it decides | How |
|---|---|---|
| **Classify** | new matter / existing client / vendor or spam / unclear | Claude |
| **Extract** | parties, matter type, jurisdiction, dates, amounts | Claude |
| **Resolve** | match against client and matter records, check conflicts | rules |
| **Dispatch** | route by practice area, open a matter stub, draft an acknowledgment | rules |
| **Decide** | act, or ask a person | rules |

Only the first two call a model. The acknowledgment is a filled template, not
generated prose — a first contact with a prospective client is the most dangerous
thing a firm sends, and firms use vetted templates for exactly that reason. The
model reads; the rules decide.

### Abstention

Every threshold in the system lives in `domain/policy.py`, and nothing else compares
a confidence to a number. They are not guesses: they were set against the measured
distribution, which is sharply bimodal — extracted fields land above 0.88 or below
0.60 with little in between — and the cuts sit in the gap.

They are also not uniform, because **a threshold is a statement about consequences,
not about the model.** A party name feeds the conflicts check and is held to 0.70. A
practice area only routes an email and is held to 0.60. A third party is read by no
rule at all and is held to nothing.

### Auditability

Every value carries a span, a composite confidence, and the arithmetic that produced
it. Every review reason cites the field or the rule that produced it. Every conflict
hit names the rule, the record, the field, and the spelling that matched — because
"the system flagged a conflict" is useless to the person who has to clear it.

In the UI the email is the primary object rather than a sidebar: hover any extracted
field and the exact characters it came from light up, and hover the text to find the
field. A quote the model invented is rendered beside the email, struck through,
because there is nowhere in the source to put it — which is the finding.

---

## Measured, not asserted

The generator knows every fact it planted, so stage quality is measured rather than
claimed. `make eval` against real Claude Opus 5 output:

```
CLASSIFY   accuracy 98.0% (50/51)
  confidence separation
    mean when correct   0.943
    mean when wrong     0.450     gap +0.493
    mean on ambiguous   0.781     mean on clear-cut 0.958

EXTRACT    practice area 100% (32/32 new-matter)   jurisdiction 100%
           party recall 97.1%   date recall 100%   amount recall 100%
           spans: 98.5% verified, 1.5% no quote offered, 0% ungrounded

DECIDE     agrees with expected action  94.1% (48/51)
           asked when it could have acted    3
           ACTED WHEN IT SHOULD HAVE ASKED   0

RESOLVE    conflict traps caught 7/7
           vacuous checks 7   (nothing to check — not a clearance)
```

Two of those matter more than the accuracy figures.

**Confidence separation** is the number the whole design rests on. If confidence on
correct answers were not meaningfully higher than on wrong ones, the score would be
decorative and no threshold could do anything with it. The gap is +0.49, and the
single misclassification came back at 0.45 — below the 0.75 bar, so the system is
wrong and knows it.

**Under-abstentions are counted separately and asserted at zero.** Over-abstaining
costs a reviewer a minute; under-abstaining is the failure the system exists to
prevent. Averaging them into one accuracy number would hide the only one that
matters.

### An honest note about spans

`not_found` is 0%. Across 274 extracted values the model never quoted text that
wasn't in the email. The span check is exercised thoroughly in unit tests, but on
this corpus it has never had to fire. That is a real result rather than a broken
check — and it would be dishonest to present the mechanism as battle-tested here.

---

## Running it

```bash
docker compose up               # everything, on :8000
```

Or locally:

```bash
make setup                      # venv + dependencies
make seed                       # generate the corpus, build the database
make process                    # run the inbox through all four stages
make api                        # API on :8000
make ui                         # front end on :5173, in another shell
```

```bash
make test        # 295 backend tests — no network, no API key, no database needed
make test-ui     # typecheck + 10 front-end tests
make eval        # score the pipeline against ground truth
```

### Reproducibility

Same seed in, byte-identical corpus out: no wall clock, no uuid, no global `random`,
and a fixed epoch. Model responses are cached by prompt hash in
`data/llm_cache.json` and committed, so the pipeline replays offline and produces
the same decisions every time. CI regenerates the corpus and fails if anything moved.

`make cache` repopulates the cache from live calls and needs `ANTHROPIC_API_KEY`.
Nothing else does.

---

## Architecture

```
backend/intake/
  domain/      pure: models, spans, confidence, validators, classify, extract,
               normalize, records, conflicts, resolve, dispatch, policy, review
  llm/         prompts, provider, response cache, offline stub
  pipeline/    orchestrator, runner, batch processing, eval harness
  db/          schema, repository, seeding
  api/         FastAPI — reads the database, calls domain functions, decides nothing
  corpus/      seeded generator, prose naturalisation
frontend/src/  React + TypeScript
```

**Domain modules import no infrastructure.** No FastAPI, no `sqlite3`, no
`anthropic`, no HTTP client. This is enforced, not documented: a test walks the AST
of every module in `domain/` and fails on a forbidden import. That is why every
decision in this system is testable with a fixture string and no network.

**Each stage is split in two.** Building the request and calling the provider is
impure and lives in `llm/` and `pipeline/`. Turning the response into typed data is
a pure function of `(email, response text)` and lives in `domain/`. Only the second
half contains judgement, and it is tested directly.

**Malformed model output is a value, not an exception.** A response that cannot be
parsed becomes a `ParseFailure`, which policy turns into a review reason. A bad
response must not take the other fifty emails down with it.

---

## Known limitations

Beyond the three headline ones above:

- **Naive matching has a false-positive mode.** Names whose distinguishing words
  match but whose corporate suffixes differ (`… Ltd.` vs `… LLP`) are reported as
  `STEM_ONLY` and graded POSSIBLE rather than treated as the same entity. They may
  be affiliated companies or unrelated ones sharing a name; this cannot tell.
- **Ground truth for incidental conflicts uses the same normalisation as the
  matcher**, so it agrees with the matcher by construction on how names are
  compared. Trap recall, scored against deliberately planted conflicts, is the
  independent measure.
- **Resolve scans linearly.** Fine for 200 clients, wrong for 200,000; the
  token-similarity pass is the part that would not survive the move.
- **Pipeline results are stored as JSON blobs**, so you cannot query inside a run
  from SQL. Adequate at 51 emails, the first thing to normalise at 51,000.
- **No auth, no multi-tenancy, no users.** Review actions are attributed to a single
  hard-coded reviewer so the audit trail has a name in it.
- **The offline stub is not a model.** With no cache and no API key the system falls
  back to keyword heuristics, labelled `stub` in the response, the trace, and the
  eval report. Its numbers measure regexes.
