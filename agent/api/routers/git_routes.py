"""Router de Git — /v1/git endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel

from agent.api.deps import runner, registry, runtime
from agent.core.agentic_loop import AgenticLoop
from agent.core.git_workflow import GitWorkflow

router = APIRouter(prefix="/v1/git", tags=["git"])

_git = GitWorkflow()


class IssueRequest(BaseModel):
    repo:       str  # owner/repo
    issue_num:  int
    session_id: str = "default"


@router.post("/solve-issue")
async def solve_github_issue(req: IssueRequest):
    """Resuelve un GitHub Issue de forma autónoma."""
    ctx = await _git.clone_repo(f"https://github.com/{req.repo}.git")
    branch_name = f"fix/issue-{req.issue_num}"
    await _git.create_branch(branch_name)

    issue_context = await _git.build_issue_context(req.repo, req.issue_num)

    loop = AgenticLoop(runner=runner, runtime=runtime, max_iterations=25)
    result = await loop.run(
        task=issue_context, session_id=req.session_id, cwd=str(ctx.work_dir),
    )

    response = {
        "success": result.success, "message": result.message,
        "iterations": result.iterations,
    }

    if result.success:
        ok, out = await _git.commit_changes(
            f"fix: resolve issue #{req.issue_num}\n\n{result.message[:500]}"
        )
        response["commit"] = {"ok": ok, "output": out}

    return response


@router.get("/diff")
async def git_diff():
    return {"diff": await _git.get_diff()}


@router.get("/status")
async def git_status():
    return {"status": await _git.get_status()}
