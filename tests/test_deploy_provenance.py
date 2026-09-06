from pathlib import Path


WORKFLOW = Path(".github/workflows/deploy-production.yml").read_text(encoding="utf-8")


def test_workflow_run_deploy_uses_the_ci_approved_sha():
    assert "github.event.workflow_run.head_sha" in WORKFLOW
    assert 'approved_sha="${CI_APPROVED_SHA:?workflow_run did not provide a head SHA}"' in WORKFLOW
    assert 'main_sha="$(git rev-parse origin/main)"' in WORKFLOW
    assert 'if [ "$approved_sha" != "$main_sha" ]; then' in WORKFLOW
    assert 'echo "should_deploy=false" >> "$GITHUB_OUTPUT"' in WORKFLOW


def test_deploy_checks_out_exact_resolved_commit_instead_of_pulling_latest_main():
    assert 'git checkout --detach "$deploy_sha"' in WORKFLOW
    assert 'test "$(git rev-parse HEAD)" = "$deploy_sha"' in WORKFLOW
    assert "git pull" not in WORKFLOW
    assert "git checkout main" not in WORKFLOW


def test_stale_ci_run_cannot_execute_deploy_or_liveness_steps():
    guard = "if: steps.target.outputs.should_deploy == 'true'"
    assert WORKFLOW.count(guard) == 2
