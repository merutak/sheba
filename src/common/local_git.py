import os
import datetime
import asyncio
import logging
from typing import Annotated

from common import AGENT_BASEDIR

logger = logging.getLogger(__name__)


MY_GIT_PREFIX = 'amichai'  # TODO configurable

GIT_WORKSPACE = f'{AGENT_BASEDIR}/git-workspace'

async def prepare_git_workspace(repo: str) -> str:
    workspace = f'{GIT_WORKSPACE}/{repo}'
    if not os.path.exists(workspace):
        logger.info("Cloning repository %s into %s", repo, workspace)
        proc = await asyncio.create_subprocess_exec('git', 'clone', f'git@github.com:{GITHUB_ORG}/{repo}.git', workspace)
    else:
        logger.info("Repository %s already exists in %s, pulling latest changes", repo, workspace)
        proc = await asyncio.create_subprocess_exec('git', 'fetch', 'origin', cwd=workspace)
    await proc.wait()
    return workspace


async def list_commits_from_branch(repo: str,
                                   branch: str,
                                   starting_from_branch: Annotated[str, "useful if you want to compare a branch to its base"] | None = None,
                                   search_for_string: str | None = None,
                                   start_date: datetime.datetime | None = None,
                                   max_commits: int | None = 50,
                                   ) -> str:
    """
    It's better to use this tool rather than list_commits, because it's more focused.
    """
    def _add_origin(branch: str | None) -> str | None:
        if not branch:
            return branch
        return "origin/" + branch if not branch.startswith('origin/') else branch
    branch = _add_origin(branch)
    starting_from_branch = _add_origin(starting_from_branch)
    await prepare_git_workspace(repo)
    # execute 'git log':
    cmd = ['git', 'log', '--pretty=format:%H - %an, %ar : %s']
    if search_for_string:
        cmd += ['--grep', search_for_string]
    if start_date:
        cmd += ['--since', start_date.strftime('%Y-%m-%d')]
    if max_commits:
        cmd += ['-n', str(max_commits)]
    if starting_from_branch:
        cmd += [f'{starting_from_branch}..{branch}']
    else:
        cmd += [branch]
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=f'{GIT_WORKSPACE}/{repo}', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return f"Failed to list commits from branch {branch} in {repo}: {stderr.decode().strip()}"
    commits = stdout.decode().strip().split('\n')
    if not commits:
        return f"No commits found in branch {branch} of repository {repo}."
    return f"Commits in branch {branch} of repository {repo}:\n" + "\n".join(commits)


async def git_log(repo: str, params: Annotated[list[str], "e.g. ['--since=2023-01-01', '-n', '50']"] | None = None) -> str:
    """
    Generic git log accessor. Note that branches should be prefixed with 'origin/'.
    """
    await prepare_git_workspace(repo)
    # execute 'git log':
    cmd = ['git', 'log'] + (params if params else [])
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=f'{GIT_WORKSPACE}/{repo}', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return f"Failed to list commits from branch {branch} in {repo}: {stderr.decode().strip()}"
    return f"Git log for repository {repo}:\n" + stdout.decode().strip()


async def create_branch_with_cherry_picks(repo: str,
                                          base_branch: Annotated[str, "where this will be merged into, typically either development/dev/main, or a release branch"],
                                          git_commit_shas_to_cherrypick: list[str],
                                          new_branch_name: Annotated[str, f"should start with {MY_GIT_PREFIX}/, preferably contain the ticket name, preferably the base branch"]) -> str:
    """
    Creates a new branch based on the specified base branch and cherry-picks the given commits into it.
    @return: A message indicating the result of the operation.
    """
    if not git_commit_shas_to_cherrypick:
        return "No commits to cherry-pick provided."

    if not new_branch_name.startswith(MY_GIT_PREFIX):
        return f"New branch name must start with {MY_GIT_PREFIX}/. Provided: {new_branch_name}"

    workspace = await prepare_git_workspace(repo)
    proc = await asyncio.create_subprocess_exec('git', 'checkout', base_branch, cwd=workspace)
    await proc.wait()
    if proc.returncode != 0:
        return f"Failed to checkout base branch {base_branch} in {repo}: {proc.returncode}"
    proc = await asyncio.create_subprocess_exec('git', 'checkout', '-b', new_branch_name, cwd=workspace)
    await proc.wait()
    if proc.returncode != 0:
        return f"Failed to create new branch {new_branch_name} in {repo}: {proc.returncode}"
    for commit_sha in git_commit_shas_to_cherrypick:
        proc = await asyncio.create_subprocess_exec('git', 'cherry-pick', commit_sha, cwd=workspace)
        await proc.wait()
        if proc.returncode != 0:
            return f"Failed to cherry-pick commit {commit_sha} in {repo}: {proc.returncode}"
    # now push the new branch to the remote repository
    proc = await asyncio.create_subprocess_exec('git', 'push', '-u', 'origin', new_branch_name, cwd=workspace)
    await proc.wait()
    if proc.returncode != 0:
        return f"Failed to push new branch {new_branch_name} to {repo}: {proc.returncode}"
    return f"Successfully created and pushed branch {new_branch_name} in {repo} with cherry-picked commits: {', '.join(git_commit_shas_to_cherrypick)}"


all_tools = [list_commits_from_branch, git_log, create_branch_with_cherry_picks]
