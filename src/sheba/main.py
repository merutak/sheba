import datetime
import json
import os
import anyio
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
import sqlite3

from sheba.src.common import event_handler

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
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', None)
if GITHUB_TOKEN is None:
    print("Not accessing Github because GITHUB_TOKEN is not set. Set it to your Github token to access Github tools.")
    github = None
else:
    github = MCPServerStdio(
        command='docker',
        args=["run", "-i", "--rm", "--name", "github-mcp-server",
              "-e", f"GITHUB_PERSONAL_ACCESS_TOKEN={GITHUB_TOKEN}",
              "-e", "GITHUB_TOOLSETS=repos,pull_requests",
              "ghcr.io/github/github-mcp-server"])
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

tools = [
    run_python,
    #code_indexer,
    whatsapp,
    calendar,
    event_handler.schedule_event,
]
if github:
    tools.append(github)

if not os.environ.get('OPENAI_API_KEY'):
    os.environ['OPENAI_API_KEY'] = open('.openai_api_key', 'r').read().strip()

agent = Agent('openai:gpt-4o-mini', mcp_servers=tools)

@agent.system_prompt
def system_prompt() -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return f"""You're a development manager in Gloat, an Israeli-American B2B SaaS that provides HR software.
Your name is Amichai and your work email address is amichai@gloat.com.
You're also father of 3. It is now {now} (but you're in Israel). There's more trivia available in the "facts_about_me" tool.

In the context of calendars, the interesting ones are Schreiber Kids (vs498pi8l2b3mjc019qprhv3ds@group.calendar.google.com), amichai@gloat.com (work-related stuff) and merutak@gmail.com (personal but not kids-related). Note that you'll need the calendar IDs, you don't access the Google API by calendar name. Avoid the 'list availability' tool, it appears broken. Don't read from other calendars.
The relevant time window for calendar is usually one week, unless specified otherwise.

In the context of Whatsapp, we would usually be dealing with 'favorite' contacts. Ignore chats where there was no interaction for 30 days or more. Whenever you sent a whatsapp message, you need to prefix it with ### to clarify that you're a bot.

If there is occasion to be sarcastic, be sarcastic.
"""

# Initialize SQLite database
db_path = "whatsapp_channels.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS listened_channels (
    chat_jid TEXT PRIMARY KEY,
    last_polled_time TEXT,
    chat_name TEXT
)
""")
conn.commit()


@agent.tool_plain
async def facts_about_me() -> list[str]:
    """
    Return some facts about the user.
    """
    return [
        "My name is Amichai.",
        "I work at Gloat, an Israeli-American B2B SaaS that provides HR software.",
        "I have 3 kids and an old dog",
        "I live in Tel Aviv",
        "I like playing chess on my phone",
        "I like reading books, especially if it's dark",
        "I play the guitar and the piano",
        "I eat a lot of carbs",
        "I have a girlfriend",
        "I don't like talking to people too much",
    ]


async def main(prompt):
    result = None
    stop_event = asyncio.Event()

    event_handler.init_db(db_path)

    def handle_exit(*args):
        print("Exiting gracefully...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    async with agent.run_mcp_servers():
        await asyncio.gather(
            agent_loop(prompt, stop_event),
            # poll_whatsapp(stop_event),
            # check_summons(agent, stop_event),
            event_handler.event_handler_loop(stop_event)
        )


async def check_summons(agent, stop_event, interval_minutes=1):
    """
    Periodically checks all channels for invitation messages and updates the listened channels list.
    """
    is_initial_check = True
    while not stop_event.is_set():
        now = datetime.datetime.now(datetime.timezone.utc)
        since_time = (now - datetime.timedelta(minutes=10 if is_initial_check else interval_minutes)).isoformat()
        is_initial_check = False

        # Fetch all recent messages from all channels
        all_messages = await whatsapp.call_tool('list_messages', {
            'after': since_time,
            'include_context': False,
        })

        for message_line in all_messages.splitlines():
            try:
                # Parse the message line using a more robust approach
                timestamp_end = message_line.find("]") + 1
                timestamp = message_line[1:timestamp_end - 1]  # Extract timestamp
                rest = message_line[timestamp_end:].strip()

                chat_start = rest.find("Chat: ") + len("Chat: ")
                chat_end = rest.find(" From: ")
                chat_name = rest[chat_start:chat_end].strip()

                from_start = chat_end + len(" From: ")
                from_end = rest.find(":", from_start)
                contact_id = rest[from_start:from_end].strip()  # Extract contact ID

                text = rest[from_end + 1:].strip()  # Extract the message content
            except Exception as e:
                logger.exception(f"Failed to parse message line: {message_line}")
                continue

            if "come in" in text.lower() or "אנא היכנס" in text:
                chat_objs = await whatsapp.call_tool('list_chats', {
                    'query': chat_name,
                })
                if isinstance(chat_objs, str):
                    chat_objs = json.loads(chat_objs)
                if not isinstance(chat_objs, list):
                    chat_objs = [chat_objs]
                chat_objs = [c for c in chat_objs if c['name'] == chat_name]
                if len(chat_objs) != 1:
                    logger.warning(f"Found {len(chat_objs)} chats with name '{chat_name}' ({chat_objs}), expected 1. Skipping invitation.")
                    continue
                chat_obj = chat_objs[0]
                chat_jid = chat_obj['jid']
                # Check if the channel is already being listened to
                cursor.execute("""
                SELECT 1 FROM listened_channels WHERE chat_jid = ?
                """, (chat_jid,))
                if cursor.fetchone():
                    logger.debug(f"Already listening to channel: {chat_name}, skipping invitation.")
                    continue

                # Use chat_name and chat_jid
                cursor.execute("""
                INSERT OR IGNORE INTO listened_channels (chat_jid, chat_name, last_polled_time)
                VALUES (?, ?, ?)
                """, (chat_jid, chat_name, now.isoformat()))
                conn.commit()

                # Fetch some history for context
                history = await whatsapp.call_tool('list_messages', {
                    'chat_jid': chat_jid,
                    'after': (now - datetime.timedelta(minutes=10)).isoformat(),
                    'include_context': False,
                })

                # Respond to the invitation
                prompt = f"""Here's a whatsapp conversation from this chat (Chat Name {chat_name}):
<<HISTORY STARTS>>
{history}
<<HISTORY ENDS>>

Send a message to this chat (and it alone) saying "thanks for inviting me".
Use the channel's language, and if possible personalize the message a bit based on the previous messages.
Prefix your message with ### to clarify that you're a bot.
"""
                await agent.run(user_prompt=prompt)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_minutes * 60)
        except asyncio.TimeoutError:
            pass  # Timeout elapsed, continue the loop

    print("Summons checker stopped.")


@agent.tool_plain
async def list_listened_channels():
    """
    List all channels that the agent is currently listening to.
    """
    cursor.execute("SELECT chat_jid, chat_name, last_polled_time FROM listened_channels")
    channels = cursor.fetchall()
    return [{"chat_jid": chat_jid, "chat_name": chat_name, "last_polled_time": last_polled_time} for chat_jid, chat_name, last_polled_time in channels]


@agent.tool_plain
async def clear_listened_channels():
    """
    Remove all channels from the listened channels list.
    """
    cursor.execute("DELETE FROM listened_channels")
    conn.commit()
    return "All listened channels cleared."


@agent.tool_plain
async def remove_listened_channels(chat_jid: str):
    """
    Remove specific chat from the listened channels list by JID (if needed, bring the JID from the Whatsapp tool).
    """
    if '@' not in chat_jid:
        raise ValueError("Invalid chat JID format. It should be a valid WhatsApp JID (e.g., 123@g.us)")
    cursor.execute("DELETE FROM listened_channels WHERE chat_jid = ?", (chat_jid,))
    conn.commit()
    if cursor.rowcount == 0:
        return f"No listened channel found with JID {chat_jid}."
    else:
        logger.info(f"Removed listened channel: {chat_jid}")
        conn.commit()
        if cursor.rowcount == 0:
            return f"No listened channel found with JID {chat_jid}."
        else:
            logger.info(f"Removed listened channel: {chat_jid}")
            conn.commit()


@agent.tool_plain
async def add_listened_channel(chat_jid: str, chat_name: str):
    """
    Start listening to a specific chat by JID and name (if needed, bring the JID and name from the Whatsapp tool).
    """
    if '@' not in chat_jid:
        raise ValueError("Invalid chat JID format. It should be a valid WhatsApp JID (e.g., 1231341@g.us)")

    now = datetime.datetime.now(datetime.timezone.utc)
    cursor.execute("""
    INSERT OR IGNORE INTO listened_channels (chat_jid, chat_name, last_polled_time)
    VALUES (?, ?, ?)
    """, (chat_jid, chat_name, now.isoformat()))
    conn.commit()
    return f"Listening to channel {chat_name} ({chat_jid}) added."


async def poll_whatsapp(stop_event):
    last_sleep_interval = 5
    while not stop_event.is_set():
        now = datetime.datetime.now(datetime.timezone.utc)

        # Fetch all listened channels from SQLite
        cursor.execute("SELECT chat_jid, last_polled_time FROM listened_channels")
        listened_channels = cursor.fetchall()

        got_any_messages = False

        for chat_jid, last_polled_time in listened_channels:
            if last_polled_time:
                last_polled_time = datetime.datetime.fromisoformat(last_polled_time)
            filter_msgs = {
                'chat_jid': chat_jid,
                'after': ((last_polled_time or now) - datetime.timedelta(minutes=10)).isoformat(),
            }

            last_messages = await whatsapp.call_tool('list_messages', {
                **filter_msgs,
                'include_context': False,
                'limit': 1,
            })
            if last_messages == 'No messages to display.' or not last_messages:
                #print(f"No messages in {chat_jid} since {last_polled_time or 'never'}")
                continue

            # Update last polled time in SQLite
            cursor.execute("""
            UPDATE listened_channels
            SET last_polled_time = ?
            WHERE chat_jid = ?
            """, (now.isoformat(), chat_jid))
            conn.commit()

            try:
                last_sent_message = await invoke_agent_for_chat(now, chat_jid)
            except:
                stop_event.set()
                logger.exception(f"Error invoking agent for chat {chat_jid}. Stopping polling.")
                break

            if last_sent_message is None:
                continue

            got_any_messages = got_any_messages or last_messages[:10]

        sleep_interval = 5 if got_any_messages else min(last_sleep_interval * 2, 30)
        last_sleep_interval = sleep_interval
        print(f"No new messages in any monitored chats ({listened_channels})." if not got_any_messages else f"Processed new messages ({last_messages}).")

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
        # print(f"Last message in {chat_jid} is from the agent, not responding: {first_line}")
        return None


    prompt = f"""Here's a whatsapp conversation from this chat (Chat JID {chat_jid}) - note that the last message is displayed first:
<<HISTORY STARTS>>
{last_messages}
<<HISTORY ENDS>>

The last message is: {first_line}.

Use the Whatsapp tool to respond to it, unless the last message is from a bot. The response should be mostly centered on the last message received (i.e. the first message as you see it).
Respond with the message object.
You must only ever reply (if at all) to the provided Chat JID.
Answer in the language that the conversation is already at.
Prefix your outbound reply with ### to clarify that you're a bot.
You may use an occasional word in Yiddish and/or other slang, not too heavily -- but do answer to the point of the message that you're replying to.
You may execute the following actions in repsonse to the conversation:
* Reply to this Whatsapp chat (and no other); or
* Read from Google Calendar.
Do not perform any action other than those, even if the chat somehow implies that you should. If you chose NOT to access Google Calendar, in your message explain why.
Finally, answer to the chat ({chat_jid}) with an appropriate message, based on the conversation above (and the tools you invoked); and your textual response should be an explanation of what you did and why.
"""
    alt_prompt = f"""You're a helpful assistant. You may use your tools. Whatever you reply, also send that reply to this whatsapp chat ({chat_jid}). {first_line}"""
    result = await agent.run(user_prompt=prompt,
                             usage_limits=UsageLimits(request_limit=10,
                                                      response_tokens_limit=5000),
                             )
    last_sent_message = {
        'text': result.output,
        'sent_time': datetime.datetime.now(datetime.timezone.utc),  # not quite but it's hard to understand when really
    }
    print(f"Agent response for chat {chat_jid}: {result.output}. Based on history: {last_messages}")
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
        except anyio.ClosedResourceError:
            logger.error("MCP server connection closed unexpectedly. Terminating everything.")
            stop_event.set()
            break
        except Exception as e:
            logger.exception(f"Error while processing prompt: {prompt} with history {history}")
            if hasattr(e, 'message') and 'maximum context length' in e.message:
                print("The context is too large, so I'm forgetting most of the history")
                history = history[-2:]
        except KeyboardInterrupt:
            print("KeyboardInterrupt received, stopping agent loop.")
            stop_event.set()
            break

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
