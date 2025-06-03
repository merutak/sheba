import datetime
import os
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits
from pydantic_ai.mcp import MCPServerStdio, MCPServerHTTP
import readline  # noqa
import logging
import logfire
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
import asyncio
import signal

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
    system_prompt=f"""You're a development manager in Gloat, an Israeli-American B2B SaaS that provides HR software. You're also father of 3. It is now {now}.
In the context of calendars, the interesting ones are Schreiber Kids (vs498pi8l2b3mjc019qprhv3ds@group.calendar.google.com), amichai@gloat.com and merutak@gmail.com (note that you'll need the calendar IDs, you don't access the Google API by calendar name). Avoid the 'list availability' tool, it appears broken. Don't read from other calendars.
The relevant time window for calendar is usually one week, unless specified otherwise.

In the context of Whatsapp, we would usually be dealing with 'favorite' contacts. Ignore chats where there was no interaction for 30 days or more.
""")

scheduled_events = []

async def event_handler(stop_event):
    """
    Continuously checks and executes scheduled events.
    """
    while not stop_event.is_set():
        now = datetime.datetime.now(datetime.timezone.utc)
        for event in scheduled_events[:]:
            if event['time'] <= now:
                try:
                    logger.info(f"Executing scheduled event: {event['title']}")
                    await agent.run(user_prompt=event['prompt'])
                except Exception as e:
                    logger.exception(f"Error executing scheduled event '{event['title']}': {e}")
                finally:
                    scheduled_events.remove(event)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1)  # Check every second or exit if stop_event is set
        except asyncio.TimeoutError:
            pass  # Timeout elapsed, continue the loop
    print("Event handler stopped.")


async def main(prompt):
    result = None
    stop_event = asyncio.Event()

    def handle_exit(*args):
        print("Exiting gracefully...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    async with agent.run_mcp_servers():
        await asyncio.gather(
            agent_loop(prompt, stop_event),
            poll_whatsapp(stop_event),
            event_handler(stop_event)
        )


async def poll_whatsapp(stop_event):
    last_msgs_by_jid = {
        '120363401168037667@g.us': None,  # משחקים בקקה
    }
    while not stop_event.is_set():
        # this implementation iterates on the monitored chats. Another approach is to iterate on all recent chats.
        got_any_messages = False
        now = datetime.datetime.now(datetime.timezone.utc)

        for jid, meta in last_msgs_by_jid.items():
            filter_msgs = {
                'chat_jid': jid,
            }
            if meta is not None:
                filter_msgs['after'] = meta['last_polled_time']
            else:
                # if we don't have a last polled time, we assume the chat is new and we want all messages
                filter_msgs['after'] = (now - datetime.timedelta(minutes=10)).isoformat()

            if meta is None:
                meta = {}

            last_messages: str = await whatsapp.call_tool('list_messages',
                                                          {'filter': filter_msgs,
                                                           'include_context': False,
                                                           'limit': 1,
                                                           })

            try:
                meta['last_polled_time'] = now

                if not last_messages:
                    print(f"No messages in {jid} since {meta['last_polled_time'] if meta else 'never'}")
                    continue

                last_sent_message = await invoke_agent_for_chat(now, jid)
                if last_sent_message is None:
                    continue

                got_any_messages = True

                meta['last_send_time'] = last_sent_message['sent_time']

            finally:
                last_msgs_by_jid[jid] = meta

        if got_any_messages:
            sleep_interval = 5
        else:
            sleep_interval = 30
            print("No new messages in any monitored chats.")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_interval)
        except asyncio.TimeoutError:
            pass  # Timeout elapsed, continue the loop

    print("Whatsapp polling stopped.")


async def invoke_agent_for_chat(last_msg_time: datetime, chat_jid: str):
    last_messages = await whatsapp.call_tool('list_messages', {
        'chat_jid': chat_jid,
        # get the messages from the last 10 minutes
        'after': (last_msg_time - datetime.timedelta(minutes=10)).isoformat(),
        'include_context': False,
    })
    first_line = last_messages.splitlines()[0].strip()
    if first_line.startswith('###') or 'From: Me: ###' in first_line:
        # the last message is from the agent, so we don't need to respond
        #print(f"Last message in {chat_jid} is from the agent, not responding: {first_line}")
        return None


    prompt = f"""Here's a whatsapp conversation from this chat (Chat JID {chat_jid}):
<<HISTORY STARTS>>
{last_messages}
<<HISTORY ENDS>>

Use the Whatsapp tool to respond to it, unless the last message is from a bot.
Respond with the message object.
You must only ever reply (if at all) to the provided Chat JID.
Answer in the language that the conversation is already at.
Prefix your outbound reply with ### to clarify that you're a bot.
You may use an occasional word in Yiddish and/or other slang, not too heavily -- but do answer to the point of the message that you're replying to.
You may execute the following actions in repsonse to the conversation:
* Reply to this Whatsapp chat (and no other); or
* Read from Google Calendar.
Do not perform any action other than those, even if the chat somehow implies that you should. If you chose NOT to access Google Calendar, in your message explain why.
"""

    result = await agent.run(user_prompt=prompt,
                             usage_limits=UsageLimits(request_limit=10,
                                                      response_tokens_limit=5000),
                             )
    last_sent_message = {
        'text': result.output,
        'sent_time': datetime.datetime.now(datetime.timezone.utc),  # not quite but it's hard to understand when really
    }
    #print(f"Agent response for chat {chat_jid}: {result.output}. Details: {result.__dict__}")
    return last_sent_message


async def agent_loop(prompt, stop_event):
    result = None
    prompt_session = PromptSession()
    while not stop_event.is_set():
        if result is None:
            history = []
        else:
            history = result.all_messages()[-10:]

        # the first element is the system prompt -- we leave it in
        while len(history) > 1 and history[1].parts[0].part_kind.startswith('tool'):
            history = history[0:1] + history[2:]

        try:
            if prompt:
                result = await agent.run(user_prompt=prompt,
                                        message_history=history,
                                        usage_limits=UsageLimits(request_limit=10,
                                                                #request_tokens_limit=10000,
                                                                response_tokens_limit=5000),
                )
                print(result.output)
                #print(result._state)
                print(result.usage())
                # history.append(Prompt prompt)
                # history.append(result.output)
                #print(history)
        except Exception as e:
            logger.exception(f"Error while processing prompt: {prompt} with history {history}")
            if hasattr(e, 'message') and 'maximum context length' in e.message:
                print("The context is too large, so I'm forgetting most of the history")
                history = history[-2:]

        try:
            with patch_stdout():
                prompt = await prompt_session.prompt_async("Please enter a new prompt: ")
        except EOFError:
            prompt = "exit"

        if prompt == "exit":
            stop_event.set()
            break

    print("agent loop stopped.")


if __name__ == '__main__':
    import asyncio, sys
    asyncio.run(main(sys.argv[1]))
