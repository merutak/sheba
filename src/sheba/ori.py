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

db_path = "ori.db"


mcps = [
    run_python,
    github,
    jenkins,
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
    return f"""You're a technical support manager in Gloat, an Israeli-American B2B SaaS that provides HR software.
It is now {now} (but you're in Israel). You're helping Amichai Schreiber (amichai@gloat.com) with software development.
We're using Coralogix for logging and JIRA for ticket management.

When you call tools, mention that you did call them, and summarize their response (even if very briefly).
"""



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
