# AR team-lead cockpit

A working slice for a manager of an accounts-receivable and credit team. It drafts coaching, a performance review, and, when the rules say so, a stay conversation. The manager confirms the rating. Nothing is sent to HR or the employee until that confirm, and confirming itself does not send.

The operating figures are computed in code from cash applications, promises, closed disputes, QA scores, closed cases, and weekly overtime. The relationship label is read from the manager's own 1:1 notes and QA comments. The narrative model writes prose from those facts. It does not choose the rating or the flight-risk flag.

Offline and deterministic by default. Set `TEAM_LEAD_LLM_PROVIDER=openai` when you want a hosted model to write the prose. Ratings and flags stay in code either way.

## The four people

| Teammate | What the quarter shows |
| --- | --- |
| Maya Chen | Exceeds. Engaged. Not a flight risk. |
| Andre Walsh | Meets. Withdrawing. Flight risk, with a stay conversation for the manager. |
| Priya Nair | Meets on the new-hire ramp bar. Engaged. Not a flight risk. |
| Jordan Hale | Below. Strained. A fair review that cites the book. Not a flight risk. |

Flight risk is raised only when all four gates are true:

- relationship is strained or withdrawing
- the rating is still meets or exceeds
- tenure is at least 12 months
- overtime was at least 10 hours in at least 4 weeks

A high performer who is fine fails the relationship gate. A new hire fails the tenure gate. Someone below standard is handled in the review, not with a stay conversation.

## Run it

Python 3.10+. From this directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

pytest
team-lead outcomes
team-lead serve
```

The cockpit is at <http://127.0.0.1:47261>. Open a person, read the figures and the quotes, confirm the rating, then post. Posting before confirm returns an error and writes nothing.

The same drafts from the command line:

```bash
team-lead seed
team-lead run --all
team-lead show TL-ANDRE
team-lead confirm TL-ANDRE --rating meets --manager "Alex Okonkwo"
team-lead post TL-ANDRE --to hr
```

`confirm` does not send. `post` sends the confirmed rating and the review only. The stay conversation stays in the case file.

SQLite is created at `data/cockpit.db` in the working directory. Override it with `TEAM_LEAD_DB`.

## Hosted prose

```bash
pip install -e ".[openai]"
export TEAM_LEAD_LLM_PROVIDER=openai
export OPENAI_API_KEY=...
# optional: TEAM_LEAD_LLM_MODEL=gpt-4o-mini
```

Copy `.env.example` if you want the variable names in one place. Unset `TEAM_LEAD_LLM_PROVIDER` to go back to the offline writer.

## What is computed

Quarter bars for a full book:

- Cash applied: meets at 90% of $1,800,000, exceeds at 110%. Under 6 months, the cash bar is 65% of that ($1,170,000).
- Promises kept: meets at 80%, exceeds at 92%. The rate is promises kept divided by promises made.
- Dispute cycle time: exceeds at 8 days or fewer, meets at 14 (18 on the ramp). Open disputes are not in the average.
- Quality: meets at 85, exceeds at 95 (ramp meets bar is 80). The score is the mean of QA reviews.
- Workload is the count of closed cases, cited next to a 140-case quarter expectation. It is not a fifth score. Sustained overtime is an attrition gate, not a performance bonus.

The quarter rating is exceeds when at least three core measures are exceeds and none are below. It is below when two or more are below. Otherwise it is meets.

## Tests

`pytest` covers the metric math, the four outcomes, the confirm-before-send rule, and the rule that attrition is not raised for the stable high performer.
