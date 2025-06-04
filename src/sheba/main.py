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
import sqlite3

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

agent = Agent('openai:gpt-4o-mini', mcp_servers=[
    run_python,
    #code_indexer,
    whatsapp,
    calendar,
    ])

@agent.system_prompt
def system_prompt() -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return f"""You're a development manager in Gloat, an Israeli-American B2B SaaS that provides HR software.
You're also father of 3. It is now {now} (but you're in Israel).

In the context of calendars, the interesting ones are Schreiber Kids (vs498pi8l2b3mjc019qprhv3ds@group.calendar.google.com), amichai@gloat.com and merutak@gmail.com (note that you'll need the calendar IDs, you don't access the Google API by calendar name). Avoid the 'list availability' tool, it appears broken. Don't read from other calendars.
The relevant time window for calendar is usually one week, unless specified otherwise.

In the context of Whatsapp, we would usually be dealing with 'favorite' contacts. Ignore chats where there was no interaction for 30 days or more.
"""

# Initialize SQLite database
db_path = "whatsapp_channels.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS listened_channels (
    chat_jid TEXT PRIMARY KEY,
    last_polled_time TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS scheduled_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    time TEXT NOT NULL
)
""")
conn.commit()

@agent.tool_plain
async def schedule_event(title: str, prompt: str,
                         absolute_target_time: None | datetime.datetime = None,
                         time_from_now: None | datetime.timedelta = None):
    """
    Schedule an event to be executed at a specific time. Either absolute time or a relative time from now must be provided.
    """
    if absolute_target_time is None:
        if time_from_now is None:
            raise ValueError("Either absolute_target_time or time_from_now must be provided.")
        absolute_target_time = datetime.datetime.now(datetime.timezone.utc) + time_from_now

    if absolute_target_time < datetime.datetime.now(datetime.timezone.utc):
        raise ValueError(f"Scheduled time must be in the future (got {absolute_target_time}).")

    cursor.execute("""
    INSERT INTO scheduled_events (title, prompt, time)
    VALUES (?, ?, ?)
    """, (title, prompt, absolute_target_time.isoformat()))
    conn.commit()

    logger.info(f"Scheduled event: {title} at {absolute_target_time.isoformat()}")
    return f"Event '{title}' scheduled for {absolute_target_time.isoformat()}."


async def event_handler(stop_event):
    """
    Continuously checks and executes scheduled events.
    """
    while not stop_event.is_set():
        now = datetime.datetime.now(datetime.timezone.utc)
        cursor.execute("""
        SELECT id, title, prompt, time FROM scheduled_events
        WHERE time <= ?
        """, (now.isoformat(),))
        events = cursor.fetchall()

        for event_id, title, prompt, time in events:
            try:
                logger.info(f"Executing scheduled event: {title}")
                await agent.run(user_prompt=prompt)
            except Exception as e:
                logger.exception(f"Error executing scheduled event '{title}': {e}")
            finally:
                cursor.execute("""
                DELETE FROM scheduled_events WHERE id = ?
                """, (event_id,))
                conn.commit()

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
            check_summons(agent, stop_event),
            event_handler(stop_event)
        )


async def check_summons(agent, stop_event, interval_minutes=1):
    """
    Periodically checks all channels for invitation messages and updates the listened channels list.
    """
    while not stop_event.is_set():
        now = datetime.datetime.now(datetime.timezone.utc)
        since_time = (now - datetime.timedelta(minutes=interval_minutes)).isoformat()

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
                # Check if the channel is already being listened to
                cursor.execute("""
                SELECT 1 FROM listened_channels WHERE chat_jid = ?
                """, (chat_name,))
                if cursor.fetchone():
                    logger.info(f"Already listening to channel: {chat_name}, skipping invitation.")
                    continue

                # Use chat_name instead of chat_jid
                cursor.execute("""
                INSERT OR IGNORE INTO listened_channels (chat_jid, last_polled_time)
                VALUES (?, ?)
                """, (chat_name, now.isoformat()))
                conn.commit()

                # Fetch some history for context
                history = await whatsapp.call_tool('list_messages', {
                    'chat_jid': chat_name,
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


async def poll_whatsapp(stop_event):
    while not stop_event.is_set():
        now = datetime.datetime.now(datetime.timezone.utc)

        # Fetch all listened channels from SQLite
        cursor.execute("SELECT chat_jid, last_polled_time FROM listened_channels")
        listened_channels = cursor.fetchall()

        got_any_messages = False

        for chat_jid, last_polled_time in listened_channels:
            filter_msgs = {
                'chat_jid': chat_jid,
                'after': last_polled_time or (now - datetime.timedelta(minutes=10)).isoformat(),
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

            last_sent_message = await invoke_agent_for_chat(now, chat_jid)
            if last_sent_message is None:
                continue

            got_any_messages = got_any_messages or last_messages[:10]

        sleep_interval = 5 if got_any_messages else 30
        print("No new messages in any monitored chats." if not got_any_messages else f"Processed new messages ({last_messages}).")

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
