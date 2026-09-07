"""Wires the Stage 16 deterministic evaluation suite into the main pytest run.

eval/cases.py is the single source of truth for the evaluation cases;
eval/run_evaluation.py turns them into the committed report artifact. This
file only asserts, inside the normal offline test suite, that every case
still passes and that every required Stage 16 category is actually covered
-- it adds no new evaluation logic of its own.
"""

from eval.cases import run_all


def test_every_evaluation_case_passes():
    results = run_all()
    failed = [r for r in results if not r.passed]
    assert not failed, "\n".join(f"{r.case_id} ({r.requirement}): {r.detail}" for r in failed)


def test_evaluation_suite_covers_all_seventeen_requirements():
    results = run_all()
    requirement_numbers = {r.requirement.split(".")[0] for r in results}
    assert requirement_numbers == {str(i) for i in range(1, 18)}


def test_evaluation_suite_has_no_live_dependency():
    # Static guarantee that this suite cannot exercise a live Gemini or
    # GitHub call: the fakes/fixtures in eval/cases.py never construct the
    # real GeminiAnalystClient/GeminiCriticClient/GeminiRevisionClient or
    # GitHubRestIssueClient, and always inject a client explicitly.
    import inspect

    from eval import cases

    source = inspect.getsource(cases)
    assert "GeminiAnalystClient(" not in source
    assert "GeminiCriticClient(" not in source
    assert "GeminiRevisionClient(" not in source
    assert "GitHubRestIssueClient(" not in source
    assert "urlopen" not in source
