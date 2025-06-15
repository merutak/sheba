import base64
import datetime
import enum
import re
from typing import Annotated, Literal
import aiohttp
import json
import os
import anyio
import mcp
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits
from pydantic_ai.mcp import MCPServerStdio, MCPServerHTTP
import readline  # noqa
import logging
import logfire
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.history import FileHistory
from common import event_handler, local_git
from common.utils import agent_loop  # Import the moved function
import asyncio
import signal
import sqlite3

import yaml

from common.models import VERBOSE_ACCOUNT_NAME, VERBOSE_REGION_NAME, OurAccounts, OurRegions


logfire.configure()
logfire.instrument_pydantic_ai()
logfire.instrument_mcp()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)


GITHUB_ORG = 'ggloat'

# deno run -N -R=node_modules -W=node_modules --node-modules-dir=auto jsr:@pydantic/mcp-run-python sse
run_python = MCPServerHTTP(
    url='http://localhost:3001/sse',
)
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', None)
if not GITHUB_TOKEN:
    raise ValueError("Need GITHUB_TOKEN")
JENKINS_TOKEN = os.environ.get('JENKINS_API_KEY', '').strip()


github = MCPServerStdio(
    command='docker',
    args=["run", "-i", "--rm", "--name", "github-mcp-server",
            "-e", f"GITHUB_PERSONAL_ACCESS_TOKEN={GITHUB_TOKEN}",
            "-e", "GITHUB_TOOLSETS=repos,pull_requests",
            "ghcr.io/github/github-mcp-server"])

jenkins = MCPServerStdio(
    command='uvx',
    args=["mcp-jenkins",
          "--jenkins-url", "https://jenkins.gloat-local.com",
            "--jenkins-username", "amichai@gloat.com",
            "--jenkins-password", JENKINS_TOKEN,
    ])

whatsapp = MCPServerHTTP(
    url='http://127.0.0.1:3005/sse',
)
# code_indexer = MCPServerStdio(
#     command='uvx',
#     args=['code-index-mcp'],
# )

db_path = "ilana.db"


mcps = [
    run_python,
    #code_indexer,
    github,
    jenkins,
    #whatsapp,
]

if not os.environ.get('OPENAI_API_KEY'):
    os.environ['OPENAI_API_KEY'] = open('.openai_api_key', 'r').read().strip()

agent = Agent('openai:gpt-4o-mini',
              #'openai:gpt-4o',
              mcp_servers=mcps,
              tools=[] + event_handler.all_tools + local_git.all_tools)

@agent.system_prompt
def system_prompt() -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return f"""You're a release manager in Gloat, an Israeli-American B2B SaaS that provides HR software.
It is now {now} (but you're in Israel). You're helping Amichai Schreiber (amichai@gloat.com) with software development.
The Github organization is "{GITHUB_ORG}". There are many repositroies -- the main one (of the monolith) is "tookyo".
The main branches are called "main" or "dev" or "development". The branches to be merged typically '<username>/<issue_id>-<description>'.
Versions are typically called e.g. 25.06.3 (2025, June, 3rd release). In tookyo the branches for them are called "release-25.06" (and the tag is 25.06.03).
Commit messages and/or PR names typically have the ticket names in them. We use JIRA to manage tickets, and the ticket names are typically like "INNER-12345".
When you create branches, they must always be prefixed with "{local_git.MY_GIT_PREFIX}/" (e.g. "{local_git.MY_GIT_PREFIX}/INNER-12345-fix-bug").

Avoid calling Jenkins "get_all_jobs" or "get_all_builds" tools. You may get specific jobs and builds.

When you call tools, mention that you did call them, and summarize their response (even if very briefly).
"""

# Initialize SQLite database
db_path = "ilana.db"
conn = sqlite3.connect(db_path)


"""
This is what a single PR looks like, returning from the `list_pull_requests` tool:

{'id': 2581071921, 'number': 468, 'state': 'open', 'locked': False,
'title': "[FIX] INNER-39920: user-skill integration: ignore (but don't die on) …",
 'body': "…skills that don't have presence in the ontology (supposedly invalid skills) (#448)\r\n\r\n(cherry picked from commit f8a6d6b26128506549db3d40d3497476672a2c39)", 'created_at': '2025-06-10T11:46:08Z', 'updated_at': '2025-06-10T12:53:05Z',
 'user': {'login': 'amichai-gloat', 'id': 138014978, 'node_id': 'U_kgDOCDnxAg', 'avatar_url': 'https://avatars.githubusercontent.com/u/138014978?v=4', 'html_url': 'https://github.com/amichai-gloat', 'gravatar_id': '', 'type': 'User', 'site_admin': False, 'url': 'https://api.github.com/users/amichai-gloat', 'events_url': 'https://api.github.com/users/amichai-gloat/events{/privacy}', 'following_url': 'https://api.github.com/users/amichai-gloat/following{/other_user}', 'followers_url': 'https://api.github.com/users/amichai-gloat/followers', 'gists_url': 'https://api.github.com/users/amichai-gloat/gists{/gist_id}', 'organizations_url': 'https://api.github.com/users/amichai-gloat/orgs', 'received_events_url': 'https://api.github.com/users/amichai-gloat/received_events', 'repos_url': 'https://api.github.com/users/amichai-gloat/repos', 'starred_url': 'https://api.github.com/users/amichai-gloat/starred{/owner}{/repo}', 'subscriptions_url': 'https://api.github.com/users/amichai-gloat/subscriptions'},
 'draft': False, 'url': 'https://api.github.com/repos/ggloat/tookyo/pulls/468', 'html_url': 'https://github.com/ggloat/tookyo/pull/468', 'issue_url': 'https://api.github.com/repos/ggloat/tookyo/issues/468',
 'statuses_url': 'https://api.github.com/repos/ggloat/tookyo/statuses/58b086a0e645f4cde39de34e70bbd3f94bacfb73', 'diff_url': 'https://github.com/ggloat/tookyo/pull/468.diff', 'patch_url': 'https://github.com/ggloat/tookyo/pull/468.patch', 'commits_url': 'https://api.github.com/repos/ggloat/tookyo/pulls/468/commits', 'comments_url': 'https://api.github.com/repos/ggloat/tookyo/issues/468/comments', 'review_comments_url': 'https://api.github.com/repos/ggloat/tookyo/pulls/468/comments', 'review_comment_url': 'https://api.github.com/repos/ggloat/tookyo/pulls/comments{/number}',
 'author_association': 'MEMBER', 'node_id': 'PR_kwDONptWyc6Z2Agx', 'merge_commit_sha': 'f4ac741ea15ed4bcd9e870185c6394c093ad60f3',
 '_links': {'self': {'href': 'https://api.github.com/repos/ggloat/tookyo/pulls/468'}, 'html': {'href': 'https://github.com/ggloat/tookyo/pull/468'}, 'issue': {'href': 'https://api.github.com/repos/ggloat/tookyo/issues/468'}, 'comments': {'href': 'https://api.github.com/repos/ggloat/tookyo/issues/468/comments'}, 'review_comments': {'href': 'https://api.github.com/repos/ggloat/tookyo/pulls/468/comments'}, 'review_comment': {'href': 'https://api.github.com/repos/ggloat/tookyo/pulls/comments{/number}'}, 'commits': {'href': 'https://api.github.com/repos/ggloat/tookyo/pulls/468/commits'}, 'statuses': {'href': 'https://api.github.com/repos/ggloat/tookyo/statuses/58b086a0e645f4cde39de34e70bbd3f94bacfb73'}}, 'head': {'label': 'ggloat:amichai/INNER-39920-userskill-ignore-unharmonized-25.08', 'ref': 'amichai/INNER-39920-userskill-ignore-unharmonized-25.08', 'sha': '58b086a0e645f4cde39de34e70bbd3f94bacfb73',
 'repo': {'id': 916149961, 'node_id': 'R_kgDONptWyQ', 'owner': {'login': 'ggloat', 'id': 125278208, 'node_id': 'O_kgDOB3eYAA', 'avatar_url': 'https://avatars.githubusercontent.com/u/125278208?v=4', 'html_url': 'https://github.com/ggloat', 'gravatar_id': '', 'type': 'Organization', 'site_admin': False, 'url': 'https://api.github.com/users/ggloat', 'events_url': 'https://api.github.com/users/ggloat/events{/privacy}', 'following_url': 'https://api.github.com/users/ggloat/following{/other_user}', 'followers_url': 'https://api.github.com/users/ggloat/followers', 'gists_url': 'https://api.github.com/users/ggloat/gists{/gist_id}', 'organizations_url': 'https://api.github.com/users/ggloat/orgs', 'received_events_url': 'https://api.github.com/users/ggloat/received_events', 'repos_url': 'https://api.github.com/users/ggloat/repos', 'starred_url': 'https://api.github.com/users/ggloat/starred{/owner}{/repo}', 'subscriptions_url': 'https://api.github.com/users/ggloat/subscriptions'}, 'name': 'tookyo', 'full_name': 'ggloat/tookyo', 'default_branch': 'development', 'created_at': '2025-01-13T14:51:57Z', 'pushed_at': '2025-06-10T13:28:55Z', 'updated_at': '2025-06-10T06:05:03Z', 'html_url': 'https://github.com/ggloat/tookyo', 'clone_url': 'https://github.com/ggloat/tookyo.git', 'git_url': 'git://github.com/ggloat/tookyo.git', 'ssh_url': 'git@github.com:ggloat/tookyo.git', 'svn_url': 'https://github.com/ggloat/tookyo', 'language': 'Python', 'fork': False, 'forks_count': 0, 'open_issues_count': 20, 'open_issues': 20, 'stargazers_count': 0, 'watchers_count': 0, 'watchers': 0, 'size': 107101, 'allow_forking': False, 'web_commit_signoff_required': False, 'archived': False, 'disabled': False, 'private': True, 'has_issues': True, 'has_wiki': True, 'has_pages': False, 'has_projects': True, 'has_downloads': True, 'has_discussions': False, 'is_template': False, 'url': 'https://api.github.com/repos/ggloat/tookyo', 'archive_url': 'https://api.github.com/repos/ggloat/tookyo/{archive_format}{/ref}', 'assignees_url': 'https://api.github.com/repos/ggloat/tookyo/assignees{/user}', 'blobs_url': 'https://api.github.com/repos/ggloat/tookyo/git/blobs{/sha}', 'branches_url': 'https://api.github.com/repos/ggloat/tookyo/branches{/branch}', 'collaborators_url': 'https://api.github.com/repos/ggloat/tookyo/collaborators{/collaborator}', 'comments_url': 'https://api.github.com/repos/ggloat/tookyo/comments{/number}', 'commits_url': 'https://api.github.com/repos/ggloat/tookyo/commits{/sha}', 'compare_url': 'https://api.github.com/repos/ggloat/tookyo/compare/{base}...{head}', 'contents_url': 'https://api.github.com/repos/ggloat/tookyo/contents/{+path}', 'contributors_url': 'https://api.github.com/repos/ggloat/tookyo/contributors', 'deployments_url': 'https://api.github.com/repos/ggloat/tookyo/deployments', 'downloads_url': 'https://api.github.com/repos/ggloat/tookyo/downloads', 'events_url': 'https://api.github.com/repos/ggloat/tookyo/events', 'forks_url': 'https://api.github.com/repos/ggloat/tookyo/forks', 'git_commits_url': 'https://api.github.com/repos/ggloat/tookyo/git/commits{/sha}', 'git_refs_url': 'https://api.github.com/repos/ggloat/tookyo/git/refs{/sha}', 'git_tags_url': 'https://api.github.com/repos/ggloat/tookyo/git/tags{/sha}', 'hooks_url': 'https://api.github.com/repos/ggloat/tookyo/hooks', 'issue_comment_url': 'https://api.github.com/repos/ggloat/tookyo/issues/comments{/number}', 'issue_events_url': 'https://api.github.com/repos/ggloat/tookyo/issues/events{/number}', 'issues_url': 'https://api.github.com/repos/ggloat/tookyo/issues{/number}', 'keys_url': 'https://api.github.com/repos/ggloat/tookyo/keys{/key_id}', 'labels_url': 'https://api.github.com/repos/ggloat/tookyo/labels{/name}', 'languages_url': 'https://api.github.com/repos/ggloat/tookyo/languages', 'merges_url': 'https://api.github.com/repos/ggloat/tookyo/merges', 'milestones_url': 'https://api.github.com/repos/ggloat/tookyo/milestones{/number}', 'notifications_url': 'https://api.github.com/repos/ggloat/tookyo/notifications{?since,all,participating}', 'pulls_url': 'https://api.github.com/repos/ggloat/tookyo/pulls{/number}', 'releases_url': 'https://api.github.com/repos/ggloat/tookyo/releases{/id}', 'stargazers_url': 'https://api.github.com/repos/ggloat/tookyo/stargazers', 'statuses_url': 'https://api.github.com/repos/ggloat/tookyo/statuses/{sha}', 'subscribers_url': 'https://api.github.com/repos/ggloat/tookyo/subscribers', 'subscription_url': 'https://api.github.com/repos/ggloat/tookyo/subscription', 'tags_url': 'https://api.github.com/repos/ggloat/tookyo/tags', 'trees_url': 'https://api.github.com/repos/ggloat/tookyo/git/trees{/sha}', 'teams_url': 'https://api.github.com/repos/ggloat/tookyo/teams', 'visibility': 'private'}, 'user': {'login': 'ggloat', 'id': 125278208, 'node_id': 'O_kgDOB3eYAA', 'avatar_url': 'https://avatars.githubusercontent.com/u/125278208?v=4', 'html_url': 'https://github.com/ggloat', 'gravatar_id': '', 'type': 'Organization', 'site_admin': False, 'url': 'https://api.github.com/users/ggloat', 'events_url': 'https://api.github.com/users/ggloat/events{/privacy}', 'following_url': 'https://api.github.com/users/ggloat/following{/other_user}', 'followers_url': 'https://api.github.com/users/ggloat/followers', 'gists_url': 'https://api.github.com/users/ggloat/gists{/gist_id}', 'organizations_url': 'https://api.github.com/users/ggloat/orgs', 'received_events_url': 'https://api.github.com/users/ggloat/received_events', 'repos_url': 'https://api.github.com/users/ggloat/repos', 'starred_url': 'https://api.github.com/users/ggloat/starred{/owner}{/repo}', 'subscriptions_url': 'https://api.github.com/users/ggloat/subscriptions'}}}
"""

# another thing - summarize last commits (from prev release) from each repo

@agent.tool_plain
async def list_prs_and_statuses_with_build_status(repo: str, by_status: str | None = None) -> str:
    """
    List all open pull requests in the specified repository that are broken (i.e., have failed checks).
    If you're not interested in the build status, better call the other variant that doesn't return statuses.

    @param repo: The name of the repository to check (e.g., 'tookyo').
    @param by_status: If specified, filter the pull requests by this status (can be 'success' or 'failure').
    """

    # note that PR *runs* are "success" or "error" whereas entire PRs are "success" vs "failure".

    response = await github.call_tool('list_pull_requests', {
        'owner': GITHUB_ORG,
        'repo': repo,
        'state': 'open',
    })
    if not response:
        return f"No broken pull requests found in {repo}."

    pr_statuses = await asyncio.gather(
        *[github.call_tool('get_pull_request_status', {
            'owner': GITHUB_ORG,
            'repo': repo,
            'pullNumber': pr['number'],
        }) for pr in response]
    )

    if by_status:
        by_state: dict[str, tuple[dict, dict]] = {}
        for pr, pr_status in zip(response, pr_statuses):
            by_state.setdefault(pr_status['state'], []).append((pr, pr_status))
        filtered_prs: list[tuple[dict, dict]] = by_state.get(by_status, [])
    else:
        filtered_prs = list(zip(response, pr_statuses))

    if not filtered_prs:
        return f"No pull requests found in {repo} with status '{by_status}' (total open PRs - {len(response)})"

    for pr, pr_status in filtered_prs:
        pr.pop('repo', None)

    """
    Example of a PR status:
    {'state': 'failure', 'sha': '0d78fce3bce1fd49889f933789c1627d7c0c5ec8', 'total_count': 2, 'statuses': [{'id': 36830737871, 'node_id': 'SC_kwDONptWyc8AAAAIk0h1zw', 'url': 'https://api.github.com/repos/ggloat/tookyo/statuses/0d78fce3bce1fd49889f933789c1627d7c0c5ec8', 'state': 'success', 'target_url': 'https://jenkins.gloat-local.com/job/tookyo-github/job/amichai%252FINNER-40590-job-edit-return-error/1/display/redirect', 'description': 'This commit looks good', 'context': 'continuous-integration/jenkins/branch', 'avatar_url': 'https://avatars.githubusercontent.com/u/187622752?v=4', 'created_at': '2025-06-09T12:02:11Z', 'updated_at': '2025-06-09T12:02:11Z'}, {'id': 36831022537, 'node_id': 'SC_kwDONptWyc8AAAAIk0zNyQ', 'url': 'https://api.github.com/repos/ggloat/tookyo/statuses/0d78fce3bce1fd49889f933789c1627d7c0c5ec8', 'state': 'error', 'target_url': 'https://jenkins.gloat-local.com/job/tookyo-github-PR/job/PR-463/1/display/redirect', 'description': 'This commit cannot be built', 'context': 'continuous-integration/jenkins/pr-head', 'avatar_url': 'https://avatars.githubusercontent.com/u/187622752?v=4', 'created_at': '2025-06-09T12:16:40Z', 'updated_at': '2025-06-09T12:16:40Z'}], 'commit_url': 'https://api.github.com/repos/ggloat/tookyo/commits/0d78fce3bce1fd49889f933789c1627d7c0c5ec8'}
    """
    ret = f"""Total open PRs: {len(response)}, total filtered: {len(filtered_prs)}
Details:
pr_id, pr_title, pr_url, pr_state, pr_status, pr_commit_sha, pr_run_url, run_number, pr_commit_url"""
    for pr, pr_status in filtered_prs:
        statuses = pr_status.get('statuses', [])
        interesting_status = ([status for status in statuses
                               if status['state'] == 'error'] or statuses)[0]
        run_number = int(re.match(r'.*/(\d+)/display/redirect' ,interesting_status.get('target_url', '')).group(1))
        ret += f"\n{pr['number']}, {pr['title']}, {pr['html_url']}, {pr_status['state']}, {interesting_status['state'] if interesting_status else 'unknown'}, {pr_status['sha']}, {run_number}, {interesting_status.get('target_url', 'unknown')}, {pr_status['commit_url']}"
    return ret


async def get_from_jenkins(url: str) -> str:
    if not JENKINS_TOKEN:
        raise ValueError("Need JENKINS_API_KEY environment variable set to your Jenkins API token.")
    #auth_encoded = base64.b64encode(f"amichai@gloat.com:{JENKINS_TOKEN}".encode()).decode()
    async with aiohttp.ClientSession() as session:
        async with session.get(url, auth=aiohttp.BasicAuth('amichai@gloat.com', JENKINS_TOKEN)) as resp:
            status_code = resp.status
            if status_code != 200:
                raise Exception(f"Failed to fetch from Jenkins: {status_code} - {await resp.text()}")
            return await resp.text()


@agent.tool_plain
async def get_tookyo_pr_jenkins_logs(repo: str,
                                     pr_number: int,
                                     run_number: int = 1,
                                     return_detailed: bool = False,
                                     only_tail: bool = True) -> dict:
    """
    For Tookyo PRs whose status is 'failure', return the Jenkins logs for the failed build.

    @param repo: The name of the repository (e.g., 'tookyo').
    @param pr_number: The pull request number (e.g., 463).
    @param run_number: The run number of the Jenkins build (default is 1).
    @param return_detailed: If True, also return the detailed log from the test run. Stick to False because it can be lengthy.
    @param only_tail: If True, return only the tail of the log (from the first failure or error). Stick to True because it can be lengthy.

    Returns a json with keys:
    * 'test_run_log': Content of the test log
    * 'detailed_log': Content of the detailed log
    """

    """
    For Tookyo, if the failed URL is https://jenkins.gloat-local.com/job/tookyo-github-PR/job/PR-463/1/display/redirect
    then the test-run log is going to be at https://jenkins.gloat-local.com/blue/rest/organizations/jenkins/pipelines/tookyo-github-PR/branches/PR-463/runs/1/log/?start=0&download=true
    and the detailed log from the test run at https://jenkins.gloat-local.com/job/tookyo-github-PR/job/PR-463/1/artifact/test.json.be.log/
    """
    test_run_log_url = f"https://jenkins.gloat-local.com/blue/rest/organizations/jenkins/pipelines/{repo}-github-PR/branches/PR-{pr_number}/runs/{run_number}/log/?start=0&download=true"
    detailed_log_url = f"https://jenkins.gloat-local.com/job/{repo}-github-PR/job/PR-{pr_number}/{run_number}/artifact/test.json.be.log/"
    try:
        test_run_log = await get_from_jenkins(test_run_log_url)
        if return_detailed:
            detailed_log = await get_from_jenkins(detailed_log_url)
        else:
            detailed_log = None
    except Exception as e:
        logger.exception(f"Error fetching logs for PR {pr_number} in {repo}: {e}")
        return {
            'error': f"Could not fetch logs for PR {pr_number} in {repo}: {str(e)}"
        }
    if only_tail:
        end_indicator = test_run_log.rfind("Total run took")
        first_failure = test_run_log.find("\nFAIL: test")
        first_error = test_run_log.find("\nERROR: test")
        print(f"Truncating jenkins log from {test_run_log_url}: end indicator {end_indicator}, first failure: {first_failure}, first error: {first_error}")
        start_from = min(first_failure, first_error) if first_failure != -1 and first_error != -1 else max(first_failure, first_error)
        print(f"Will truncate from {start_from} to {end_indicator}")
        test_run_log = test_run_log[start_from:end_indicator] if start_from != -1 else test_run_log[:end_indicator]

    if len(test_run_log) > 2000:
        test_run_log = test_run_log[-2000:]
    if detailed_log and len(detailed_log) > 2000:
        detailed_log = detailed_log[-2000:]

    print(f"Returning only_tail={only_tail} test_run_log of length {len(test_run_log)} and detailed_log of length {len(detailed_log) if detailed_log else 'N/A'}")

    return {
        'test_run_log': test_run_log,
        'detailed_log': detailed_log,
    }


def get_cluster_name(account: OurAccounts, region: OurRegions) -> str:
    """
    Returns the EKS cluster name based on the account and region.
    """
    return f"{account.value}-eks-{region.value}-01"


@agent.tool_plain
async def branching_guidelines_and_release_status() -> str:
    """
    Returns the branching & fix guidelines for Tookyo.
    """
    return """Branching Guidelines for Tookyo:
1. Use the format `amichai/INNER-<issue_number>-<description>` for branch names.
2. By default, the branch should be based on the `development` branch, but sometimes you want to refer to other versions e.g. release-25.06.
3. You should only create "release-xx" branches if we're defining a new version (explicitly). This is never part of an issue fix, but part of a release process.
4. Ask the user for explicit confirmation before creating any new branch.
"""


@agent.tool_plain
async def instructions_for_pr_deepdive() -> str:
    return """
1. Verify the status of the PR.
2. If it's successful and approved, then it just needs to be merged. (This should be done manually).
2. If it's successful but not approved, then ask the user to approve it.
3. If it's failed, then:
    a. Check the Jenkins logs for the PR.
    b. If the failure is due to a test failure, tell the user which tests failed and why.
    c. Look at the commits between this PR and the base branch, and consider whether they are relevant to the failure.
    d. If the failure seems related to the changes, tell this to the user, and if you can, suggest a fix (as a patch to the code).
"""


@agent.tool_plain
def get_values_yaml_path(account: OurAccounts, app_name: str, region: OurRegions, namespace: str) -> str:
    account_and_region = get_cluster_name(account, region)
    path = f"apps/{app_name}/{account_and_region}/{namespace}"
    return f'{path}/values.yaml'


async def get_yaml_from_kubectl(account: OurAccounts, deployment: str, region: OurRegions, namespace: str) -> dict:
    """
    Fetches the values.yaml file for a specific app from the Kubernetes cluster using kubectl.
    """

    conf_name = {
        OurAccounts.stg: 'gloat-staging',
        OurAccounts.prod: 'gloat-prod',
        OurAccounts.dev: 'gloat-dev',
    }
    # this is the prod cmdline: bash -c 'export AWS_PROFILE=gloat-staging && aws eks update-kubeconfig --name stg-eks-euc1-01 --region eu-central-1 && kubectl get deployment -n test-telstra tookyo'
    cmdline = f"export AWS_PROFILE={conf_name[account]} && " \
              f"aws eks update-kubeconfig --name {get_cluster_name(account, region)} --region {VERBOSE_REGION_NAME[region.value]} > /dev/null && " \
              f"kubectl get deployment -n {namespace} {deployment} -o yaml"
    proc = await asyncio.create_subprocess_exec('bash', '-c', cmdline, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return {"error": "Failed to fetch values.yaml from kubectl:" + stderr.decode().splitlines()[-1]}
    try:
        file_content = yaml.safe_load(stdout.decode('utf-8'))
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse YAML for {deployment} in {region} and namespace {namespace}: {e} (beginning of content: {stdout.decode('utf-8')[:50]})")
    if not isinstance(file_content, dict):
        raise ValueError(f"Expected a dictionary in values.yaml for {deployment} in {region} and namespace {namespace}, "
                         f"but got {type(file_content)}")
    return file_content


async def get_yaml_from_git(account: OurAccounts, app_name: str, region: OurRegions, namespace: str) -> dict:
    filename = get_values_yaml_path(account, app_name, region, namespace)
    try:
        file_content = await github.call_tool('get_file_contents', {
            'owner': 'ggloat',
            'repo': 'k8s-apps',
            'path': filename,
        })
    except mcp.shared.exceptions.McpError as e:
        if'404 Not Found' in str(e):
            raise FileNotFoundError(f"File {filename} not found in repository k8s-apps for {app_name} in region {region} and namespace {namespace}")
        raise e
    if file_content["encoding"] != "base64":
        raise ValueError(f"Expected base64 encoding for {app_name} in region {region} and namespace {namespace}, but got {file_content['encoding']}")
    file_content = yaml.safe_load(base64.b64decode(file_content["content"]).decode('utf-8'))
    if not isinstance(file_content, dict):
        raise ValueError(f"Expected a dictionary in {filename}, but got {type(file_content)}")
    return file_content


async def get_version_from_git(account: OurAccounts, app_name: str, region: OurRegions, namespace: str) -> str:
    file_content = await get_yaml_from_git(account, app_name, region, namespace)
    return get_version_from_yaml(file_content)


def get_version_from_yaml(file_content: dict) -> str:
    try:
        return file_content['deployment']['image']['tag']
    except KeyError:
        to_print = file_content
        for key in ['deployment', 'image', 'tag']:
            if key in to_print:
                to_print = to_print[key]
            else:
                raise ValueError(f"Not found: {key} in {to_print.keys()}")
        else:
            raise ValueError("WTF")


@agent.tool_plain
async def get_tookyo_deployment(account: OurAccounts, company_slug: str, region: OurRegions | None = None) -> str:
    helm = None
    if region is None:
        errors = []
        for region in OurRegions:
            logger.debug("Trying to get Tookyo version for company slug %s in region %s", company_slug, region)
            helm = await get_yaml_from_kubectl(account, 'tookyo', region, company_slug)
            if "error" not in helm:
                break
            # probably not the right region
            errors.append(helm)
        else:
            return {"error": errors}
    else:
        helm = await get_yaml_from_kubectl(account, 'tookyo', region, company_slug)
        if "error" in helm:
            return {"error": helm}
    return helm


@agent.tool_plain
async def get_deployed_version_of_tookyo(account: OurAccounts, company_slug: str, region: OurRegions | None = None) -> str:
    helm = await get_tookyo_deployment(account, company_slug, region)
    if isinstance(helm, dict) and 'error' in helm:
        return "Error trying to read helm chart for tookyo: " + str(helm['error'])

    for container in helm.get('spec', {}).get('template', {}).get('spec', {}).get('containers', []):
        if container.get('name') == 'tookyo':
            return container.get('image', '').split(':')[-1]
    return "did not find any tookyo containers in the deployment yaml"


@agent.tool_plain
async def get_target_version_of_tookyo(account: OurAccounts, company_slug: str, region: OurRegions | None = None) -> str:
    helm = None
    if region is None:
        for region in OurRegions:
            try:
                helm = await get_yaml_from_git(account, 'gloat', region, company_slug)
                break
            except FileNotFoundError:
                continue
        else:
            raise FileNotFoundError(f"Could not find Tookyo version for any region with slug {company_slug}")
    else:
        helm = await get_yaml_from_git(account, 'gloat', region, company_slug)
    return helm['tookyo']['image']['tag']


@agent.tool_plain
async def get_target_version_of_service(account: OurAccounts, region: OurRegions, service: str):
    return await get_version_from_git(account, service, region, service)


@agent.tool_plain
async def trigger_release_tookyo_version(account: OurAccounts, company_slugs: list[str],
                                         target_version: Annotated[str, "format: <year>.<month>.<minor>, e.g. 24.03.2"],
                                 skip_tests: bool = False) -> str:
    if account == OurAccounts.prod:
        raise ValueError("Production releases are currently not supported")
    if not re.match(r"^2\d\.\d{2}\.\d+$", target_version):
        raise ValueError(f"Invalid version format: {target_version}. Expected format: <year>.<month>.<minor>, e.g. 24.03.2")
    ret = await jenkins.call_tool('build_job', {
        'fullname': 'release-env-k8s',
        'parameters': {
            'k8s_env': ' '.join(company_slugs) + f' {VERBOSE_ACCOUNT_NAME[account.value]}',
            'config_version': '',
            'tookyo_version': target_version,
            'skip_tests': skip_tests,
        },
    })
    logger.info("Called release_tookyo_version with parameters: "
               f"{company_slugs}, {target_version}, {skip_tests}; returned: {ret}")
    return ret


@agent.tool_plain
async def list_prs(head: str | None = None, state: str | None = None, owner: str = GITHUB_ORG, repo: str = 'tookyo') -> list[dict]:
    """
    Fetches pull requests from the specified repository and owner. Better to use this tool rather than list_pull_requests, because it's less verbose.
    If you're interesting in build status (tests, etc), use list_prs_and_statuses_with_build_status instead.
    @param head: The branch to filter pull requests by (optional).
    @param state: The state of the pull requests to filter by (e.g., 'open', 'closed', 'all').
    @param owner: The owner of the repository (default is GITHUB_ORG).
    @param repo: The name of the repository (default is 'tookyo').
    @return: A list of pull requests.
    """
    params = {'owner': owner, 'repo': repo}
    if head:
        params['head'] = head
    if state:
        params['state'] = state
    ret = await github.call_tool('list_pull_requests', params)
    for pr in ret:
        pr.pop('repo', None)
        pr.pop('user')
    return ret


async def main(prompt):
    stop_event = asyncio.Event()

    event_handler.init_db(db_path)

    def handle_exit(*args):
        print("Exiting gracefully...")
        stop_event.set()
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    async with agent.run_mcp_servers():
        await asyncio.gather(
            agent_loop(agent, prompt, stop_event),
            event_handler.event_handler_loop(stop_event),
        )


if __name__ == '__main__':
    import asyncio, sys
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
