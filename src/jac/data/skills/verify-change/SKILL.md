---
name: verify-change
description: Run the project's deterministic checks (typecheck, tests, lint) after editing code so the user doesn't have to. Use after any non-trivial code change, before declaring you're done.
tools_required:
  - run_shell
  - read_file
---

# Verifying a change before declaring done

"It compiles" is not done. "I think it's right" is not done. **Done** is "I ran the checks the project ships with and they pass."

## 1. Find the project's checks

Look for the recipes the maintainer already wrote. Check in this order:

1. **`justfile` or `Makefile`** — most projects expose `just check`, `just fix`, `make test`, etc. Use those; they encode the maintainer's intent.
2. **`pyproject.toml`** — look for `[tool.pytest]`, `[tool.ruff]`, `[tool.ty]`, or a `scripts` table.
3. **`package.json`** — for JS/TS projects, `scripts.test`, `scripts.lint`, `scripts.typecheck`.
4. **`CONTRIBUTING.md` / `AGENTS.md` / `README.md`** — the human-written instructions usually say "run X before committing."

If you find a single command that runs everything (e.g. `just check`), prefer that. If you have to run pieces individually, do all of them — don't stop at the first green.

## 2. Run the checks

```python
run_shell(
  reason="run the project's full check suite after editing src/foo.py",
  command="just check",
)
```

For JAC specifically, that's:

```
uv run python -m pytest    # tests
uv run ruff check src/      # lint
uv run ty check src/        # typecheck
```

Or, the wrapped form: `just check` (preferred when the recipe exists).

## 3. Interpret the output

**Tests fail:** read the failure. Don't immediately patch the test to pass — first understand whether your change broke the test (fix the code) or the test was already wrong (fix the test, but mention it). Re-run after the fix to confirm.

**Type errors:** read every error, not just the first. Sometimes one root cause produces a cascade — fix the root and re-run.

**Lint errors:** if they're auto-fixable (`ruff check --fix`), apply the fix. If they're about real complexity (unused imports, dead code), look at whether you introduced them; if yes, clean up.

## 4. When checks won't fit in one turn

If the test suite is slow (>30s) or there are many failures, **delegate to a sub-agent** with a hook so you don't burn a turn just to read "all green":

```python
spawn_sub_agent(
  reason="run + fix verification with hook-driven retries",
  task_summary="Run pytest + ruff + ty; fix anything that fails; return the final clean output.",
  tier="medium",
  task_packet=SubAgentTaskPacket(
    objective="Apply the fix described, then run all checks and ensure they pass.",
    success_criteria="All three checks return exit code 0.",
    relevant_paths=["src/", "tests/"],
    hooks=[
      Hook(name="pytest", kind="shell", target="uv run python -m pytest -x --tb=short"),
      Hook(name="ruff", kind="shell", target="uv run ruff check src/"),
      Hook(name="ty", kind="shell", target="uv run ty check src/"),
    ],
    max_turns=10,
  ),
)
```

When all three hooks pass, the sub-agent returns verbatim — **no extra LLM turn** spent confirming the obvious. That's the whole point of hooks (Phase C).

## 5. Report what you ran

Tell the user exactly what passed:

> ✓ ran `just check` — 47 tests pass, no lint, typecheck clean

If something failed and you couldn't fix it, tell the user that too — never claim done when the checks didn't pass. "I made the change but `tests/test_x.py::test_y` is failing for this reason: ..." is honest; "Done!" with a red CI is not.

## Anti-patterns to avoid

- ❌ Skipping checks because "it's a small change." Small changes break things too.
- ❌ Running only the test file you think is relevant. Run the whole suite — that's the project's contract.
- ❌ `pytest -k <name>` as the final check. Fine for iteration, not for "done."
- ❌ Disabling a failing test "to come back to later." Either fix it or leave it failing and flag it; don't hide it.
- ❌ Claiming done after `ruff` passed but before tests ran. Lint is the cheapest signal, not the strongest.
