# OOC — 7 Failure Patterns Phase-16 Will Reveal

<!-- Out-of-context notes: trajectory research from SWE-agent, OpenHands, etc. These patterns are what you will almost certainly see when running Phase-16 mining. 90% of performance improvements come from fixing them, not from adding new architecture. -->

These patterns come from trajectory studies of real coding agents like SWE-agent, OpenHands, and others. ([arXiv][1])

---

# The 7 Failure Patterns Your Phase-16 System Will Reveal

## 1. Retrieval Miss (Most common)

**Symptom**

Agent edits the wrong file or never finds the bug.

Typical trajectory:

```
SEARCH foo
READ foo.py
EDIT foo.py
tests fail
retry
```

Reality:

```
bug was in bar.py
```

Trajectory studies show agents often **identify the right file but fail to gather enough context for correct modification**. ([arXiv][2])

### Metrics you will see

```
retrieval_miss_rate ≈ 30-40%
```

### Fix later

* increase `MAX_REPO_SNIPPETS`
* expand symbol graph
* improve query rewrite

---

## 2. Wrong File Localization

Agent finds *a related file*, not the correct one.

Example:

```
bug in config_loader.py
agent edits settings.py
```

This happens when:

```
embedding search > lexical search
```

### Metrics

```
wrong_file_localization ≈ 15-25%
```

### Fix later

Improve:

```
anchor detection
symbol expansion
call graph expansion
```

---

## 3. Incorrect Patch

Agent edits the correct file but produces incorrect logic.

Example:

```
if x > 5:
```

should be

```
if x >= 5:
```

Trajectory research shows **correct localization happens much more often than correct modification**. ([arXiv][2])

### Metrics

```
incorrect_patch ≈ 20-30%
```

### Fix later

Improve:

```
patch generation prompt
AST validation
critic feedback
```

---

## 4. Syntax-Error Patch

Patch compiles incorrectly.

Example:

```
missing colon
broken indentation
invalid import
```

This will show up as:

```
syntax_error_patch
```

### Metrics

```
≈ 5-10%
```

### Fix later

Add:

```
AST validator
ruff
black formatting
```

---

## 5. Agent Looping

Agent repeats the same steps.

Example:

```
SEARCH foo
SEARCH foo
SEARCH foo
SEARCH foo
```

Or:

```
EDIT foo.py
tests fail
EDIT foo.py
tests fail
EDIT foo.py
```

Trajectory studies identify **repeated action loops** as a common failure pattern. ([NeurIPS Proceedings][3])

### Metrics

```
loop_failure ≈ 5-15%
```

### Fix later

Add:

```
loop detection
step diversity
retry strategy change
```

---

## 6. Premature Completion

Agent stops too early.

Example:

```
EXPLAIN bug
STOP
```

or

```
EDIT
(no test run)
STOP
```

This failure pattern is called **premature completion** in trajectory research. ([OpenReview][4])

### Metrics

```
premature_completion ≈ 5-10%
```

### Fix later

Improve:

```
task completion criteria
validator prompt
```

---

## 7. Hallucinated API / Symbol

Agent writes code using symbols that do not exist.

Example:

```
config.get_database()
```

when:

```
config.get_db()
```

This happens when the model guesses.

### Metrics

```
hallucinated_symbol ≈ 5-10%
```

### Fix later

Add:

```
repo graph validation
symbol existence check
```

---

# What Your First Failure Report Will Look Like

After Phase-16 runs 300 tasks:

```
Total tasks: 300
Success: 118
Success rate: 39%

Failure breakdown:

retrieval_miss: 33%
incorrect_patch: 27%
wrong_file_localization: 18%
syntax_error_patch: 9%
loop_failure: 6%
premature_completion: 4%
hallucinated_symbol: 3%
```

From that report you fix the **top 3 issues**.

---

# What Happens After 3 Cycles

Typical improvement loop:

```
Cycle 1
success rate: 35%

Cycle 2
success rate: 47%

Cycle 3
success rate: 58%
```

Exactly how SWE-agent and similar systems improved.

---

# The Most Important Insight

Trajectory research shows:

```
successful trajectories are shorter
failed trajectories are longer
```

with **higher variance and chaotic step patterns**. ([arXiv][2])

That's why your Phase-16 framework is critical.

---

# Final Advice (Principal Engineer)

Do not tune prompts randomly.

Follow this loop:

```
run 300 tasks
mine failures
fix top 3 issues
repeat
```

Architecture is already strong.

Now **data-driven tuning begins.**

---

# References

[1]: https://arxiv.org/abs/2511.00197 "Understanding Code Agent Behaviour: An Empirical Study of Success and Failure Trajectories"

[2]: https://arxiv.org/pdf/2511.00197 "Understanding Code Agent Behaviour: An Empirical Study ..."

[3]: https://proceedings.neurips.cc/paper_files/paper/2024/file/5a7c947568c1b1328ccc5230172e1e7c-Paper-Conference.pdf "SWE-agent: Agent-Computer Interfaces Enable Automated ..."

[4]: https://openreview.net/forum?id=ZBOFr4ryBk "Pattern-Guided Trajectory Selection for Coding Agents on ..."
