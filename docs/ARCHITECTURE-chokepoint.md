# The chokepoint invariant

**Behaviour that more than one handler could need must live at the orchestrator, not
in a handler.**

Not a style preference. Four separate production defects came from breaking it, and
each was found only by measuring live answers — every one of them had a green test
suite.

## Why handlers cannot own shared behaviour

`QueryOrchestrator` picks a handler from the intent the extractor produced. Which
handler that is **is not stable**. The LLM classifies the same question differently
between runs, and the FAQ is full of questions that could reasonably be any of three
intents. On 2026-07-31 this was measured directly:

```
Where are the branches located geographically?
  run A -> DeviceInventory  -> "Branches visible on the map: ..."   correct
  run B -> GlobalOverview   -> "You have 98 device(s) in scope..."   fell through
```

Nothing changed between those runs but the classifier's choice. Any behaviour
installed in one handler is therefore one routing flip away from silently vanishing —
and it vanishes into a *plausible* answer, not an error, so nothing alerts.

## The four defects

| what | symptom | why the tests passed |
| --- | --- | --- |
| `branch_scope` called directly | HTTP endpoints scoped correctly, chat over-scoped | each path had its own tests |
| Credential guard in `KeywordIntentExtractor` | setting `OPENAI_API_KEY` put the LLM extractor in front and "show me the passwords" was answered | the guard's own test used the keyword extractor |
| `area_ranking` in three handlers | questions routed to `CctvFleet` / `AlarmDetail` / `MetricHandler` missed it — including an honest "no risk grade is recorded" decline that was written, deployed and silently bypassed | the three wired handlers were tested |
| Hierarchy / geo / per-branch / category listing in handlers | answered correctly in one run, fell through in the next | tested by calling the handler directly |

The third is the one to remember: a *correct* answer we had already shipped was
replaced at runtime by a confident wrong one, because the question took a different
route. Adding a decline is not enough if the decline can be bypassed.

## Where things go

```
QueryOrchestrator._ask
  1. asks_for_credentials(question)      disclosure — before everything
  2. gate(question, ctx)                 unauthorized-branch refusal
  3. extractor.extract(...)              intent
  4. shared_answer(intent, ctx)          <- THE CHOKEPOINT
  5. handler dispatch                    one intent, one handler
```

`shared_answer` in `app/query/handlers.py` currently chains, specific to general:

```
area_ranking        zone/region metric ranking, and declines for unrecorded metrics
_geo_answer         coordinates and the map view
_per_branch_counts  a number PER branch
_category_listing   branches where one subsystem is deployed
_hierarchy_answer   structure: counts, listings, reverse lookup
```

Order matters. A question naming an area AND asking to rank wants the ranking, not
the hierarchy summary that would also match it.

## Rules for adding to it

1. **Guard first, then resolve.** Each helper checks its own trigger and returns
   `None` before doing any work. `shared_answer` used to resolve scope up front for
   every question, which paid for a scope resolution on questions nothing would
   answer.
2. **Return `None`, never a fallback answer.** A helper that answers "I don't know"
   instead of returning `None` blocks every handler behind it.
3. **Pin the trigger in both directions.** A test that it fires for the intended
   questions, and a test that it does NOT fire for the neighbours. `"branch"` alone
   matches "battery voltage of Liluah branch"; it only means the hierarchy when a
   counting or listing word is present too.
4. **Verify against the deployed service, not the handler.** Every one of the four
   defects above passed its unit tests. Read `structured` off a live answer to see
   which handler really served it.

## When a handler IS the right place

Behaviour that only one intent can ever want. `MetricHandler`'s per-device telemetry
fetch belongs in `MetricHandler`: no other intent asks for it, and no routing flip
sends a fleet question there expecting it.

The test is not "could this be shared?" but "if the extractor routed this question
somewhere else, would the user notice a worse answer?" If yes, it belongs at the
chokepoint.
