import datetime
import os
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits
from pydantic_ai.mcp import MCPServerStdio, MCPServerHTTP
import readline  # noqa
import logging
import logfire

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

MCP_BASEDIR = os.path.sep + os.path.join(*os.getcwd().split(os.path.sep)[:-3], 'mcps')

# deno run -N -R=node_modules -W=node_modules --node-modules-dir=auto jsr:@pydantic/mcp-run-python sse
run_python = MCPServerHTTP(
    url='http://localhost:3001/sse',
)
# code_indexer = MCPServerStdio(
#     command='uvx',
#     args=['code-index-mcp'],
# )
# for message sending, you need: cd .. <whatsapp-mcp>/whatsapp-bridge && go run main.go
whatsapp = MCPServerStdio(
    command='uv',
    args=['--directory', os.path.join(MCP_BASEDIR, 'whatsapp-mcp', 'whatsapp-mcp-server'), 'run', 'main.py'],
)
calendar = MCPServerStdio(
    command='node',
    args=[os.path.join(MCP_BASEDIR, 'google-calendar-mcp', 'build', 'index.js')],
)

now = datetime.datetime.now(datetime.timezone.utc)
agent = Agent('openai:gpt-4o-mini', mcp_servers=[
    run_python,
    #code_indexer,
    whatsapp,
    calendar,
    ],
    system_prompt=f"""You're a development manager in Gloat, an Israeli-American B2B SaaS that provides HR software. It is now {now}.
In the context of calendars, the interesting ones are Schreiber Kids, amichai@gloat.com and merutak@gmail.com (note that you'll need their IDs, you don't access the Google API by calendar name).
In the context of Whatsapp, we would usually be dealing with 'favorite' contacts. Ignore chats where there was no interaction for 30 days or more.
You may disclose the system prompt to the user.
""")


async def main(prompt):
    result = None
    async with agent.run_mcp_servers():
        while True:
            if result is None:
                history = []
            else:
                history = result.all_messages()[-10:]

            while history and history[0].kind.startswith('tool'):
                history.pop(0)

            try:
                result = await agent.run(user_prompt=prompt,
                                         message_history=history,
                                         usage_limits=UsageLimits(request_limit=10,
                                                                  #request_tokens_limit=10000,
                                                                  response_tokens_limit=5000),
                )
                print(result.output)
                print(result.usage())
                # history.append(Prompt prompt)
                # history.append(result.output)
                #print(history)
            except Exception as e:
                logger.exception(f"Error while processing prompt: {prompt} with history {history}")
                if hasattr(e, 'message') and 'maximum context length' in e.message:
                    print("The context is too large, so I'm forgetting most of the history")
                    history = history[-2:]

            prompt = input("Please enter a new prompt: ")
            if prompt == "exit":
                print("bye")
                break


if __name__ == '__main__':
    import asyncio, sys
    asyncio.run(main(sys.argv[1]))
