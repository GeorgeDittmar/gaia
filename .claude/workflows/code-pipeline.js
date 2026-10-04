export const meta = {
  name: 'code-pipeline',
  description: 'Full coding pipeline: write code → review for quality → QA test against spec',
  phases: [
    { title: 'Code' },
    { title: 'Review' },
    { title: 'QA' },
  ],
};

// ── Phase 1: Coder writes the code ──
phase('Code');

const codeResult = await agent(
  `You are a Python coder. Write simple, maintainable code.

RULES:
- Simple, boring code. No clever tricks that hurt readability.
- Every function has docstrings and type hints.
- Code IS the documentation — if it needs heavy comments to explain, refactor instead.
- Match existing project style (check CLAUDE.md).
- No spaghetti code: clear control flow, no deep nesting.
- No one-letter variables outside tight loops.
- Modern Python 3.10+ syntax (X | None, match/case, pathlib.Path).

TASK: ${args.task}

WRITE THE CODE. Output a summary of what files were created or modified.`,
  { label: 'coder', phase: 'Code' }
);

log('Code written. Running code review...');

// ── Phase 2: Code Reviewer checks quality ──
phase('Review');

const reviewResult = await agent(
  `You are a senior code reviewer. Review code for simplicity, maintainability, and clarity.

CRITERIA:
- Simple over clever. Boring code is good code.
- Clear naming, readable control flow. No over-engineering.
- Matches project conventions (check CLAUDE.md).
- Type hints present, imports clean.

TASK: ${args.task}

REVIEW THE CODE. If anything is off, reject with a specific list of issues. If it passes, say "APPROVED".`,
  { label: 'reviewer', phase: 'Review' }
);

log('Review complete. Running QA...');

// ── Phase 3: QA verifies correctness ──
phase('QA');

const qaResult = await agent(
  `You are a QA engineer. Verify the code works against the spec.

CHECKS:
1. Does the code meet the spec? Compare task vs implementation.
2. Run available tests: uv run pytest <path>
3. Try to break the code — edge cases, None inputs, empty collections, error paths
4. Check imports resolve: uv run python -c "from <module> import <thing>"

TASK: ${args.task}

If tests fail or gaps exist, reject with specifics. If everything holds, say "APPROVED".`,
  { label: 'qa', phase: 'QA' }
);

log('Pipeline complete.');

return {
  task: args.task,
  code: codeResult,
  review: reviewResult,
  qa: qaResult,
};
