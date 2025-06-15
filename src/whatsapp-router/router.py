import asyncio
import datetime
import os
import sqlite3
import logging
import sys
from mcp.server.fastmcp import FastMCP
from pydantic_ai.mcp import MCPServerStdio, MCPServerHTTP
from pydantic_ai import Agent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)

MCP_BASEDIR = os.path.sep + os.path.join(*os.getcwd().split(os.path.sep)[:-3], 'mcps')

# Initialize SQLite database
db_path = "whatsapp_routes.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS listened_channels (
    chat_jid TEXT PRIMARY KEY,
    last_polled_time TEXT,
    chat_name TEXT,
    agent_designation TEXT NOT NULL
)
""")

hagai = MCPServerStdio(
    command='python',
    args=['hagai/main.py'],
)
ilana_sse = MCPServerHTTP(
    url='http://127.0.0.1:3010'
)
# sheba = MCPServerStdio(
#     command='python',
#     args=['sheba/main.py'],
# )


async def run_hagai(chat_jid, chat_name, messages: list[str]):
    """
    Process messages using the Hagai agent.
    """
    logger.info(f"Running Hagai agent for chat {chat_jid} with messages: {messages}")
    response = await hagai.call_tool('patient_speaks', {
        'patient_name': chat_jid,
        'messages': '\n'.join(messages),
    })
    return response


async def run_sheba(chat_jid, chat_name, messages):
    raise NotImplementedError("Sheba agent is not implemented yet.")


# Map chat designations to agents
agent_map = {
    'hagai': run_hagai,
    'general': run_sheba,
}


async def route_messages():
    """
    Infinite loop to listen to WhatsApp messages and route them to the appropriate agent.
    """
    whatsapp = MCPServerStdio(
        command='uv',
        args=['--directory', os.path.join(MCP_BASEDIR, 'whatsapp-mcp', 'whatsapp-mcp-server'), 'run', 'main.py'],
    )
    mcps = [whatsapp, hagai,
             # sheba,  # Uncomment when Sheba is implemented
    ]
    dummy_agent = Agent('openai:gpt-4o-mini', mcp_servers=mcps)  # we don't really need an agent, just MCP foolishness

    async with dummy_agent.run_mcp_servers():
        while True:
            # Fetch all listened channels from SQLite
            cursor.execute("SELECT chat_jid, chat_name, agent_designation FROM listened_channels")
            listened_channels = cursor.fetchall()

            for chat_jid, chat_name, agent_designation in listened_channels:
                # Fetch recent messages from the chat
                messages = await whatsapp.call_tool('list_messages', {
                    'chat_jid': chat_jid,
                    'after': (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)).isoformat(),
                    'include_context': False,
                })

                if not messages or messages == 'No messages to display.':
                    #logger.debug(f"no new messages for chat {chat_jid}, skipping")
                    continue

                # these messages are sorted recent-to-oldest -- throw away ones starting with "###" (and anything after them)
                # since we've already processed those (or wrote them ourselves)
                messages = messages.splitlines()  # this isn't quite right, some messages are multiline
                first_bot_msg = min([ind for ind, msg in enumerate(messages) if "###" in msg] or [999])

                if first_bot_msg == 0:
                    #logger.debug(f"No new (non-bot) messages to process for chat {chat_jid}: {messages}")
                    continue

                messages = messages[:first_bot_msg]

                agent: callable = agent_map.get(agent_designation)
                if not agent:
                    logger.error(f"Invalid agent designation '{agent_designation}' for chat {chat_jid}. Skipping.")
                    continue

                logger.info("Received messages for chat %s: %s", chat_jid, messages)

                try:
                    response = await agent(chat_jid, chat_name, messages)
                    if response:
                        # Send the agent's response back to the chat
                        await whatsapp.call_tool('send_message', {
                            'recipient': chat_jid,
                            'message': f"### {response}",
                        })
                        logger.info(f"Sent response to chat {chat_jid}: {response}")
                except Exception as e:
                    logger.exception(f"Error handling chat {chat_jid}: {e}")

            # Wait before polling again
            await asyncio.sleep(5)


async def main():
    server = FastMCP('Whatsapp Router', host='127.0.0.1', port=3005)
    await asyncio.gather(
        server.run_sse_async(),
        route_messages(),
    )



if __name__ == "__main__":
    # List all currently listened chats
    cursor.execute("SELECT chat_jid, chat_name, agent_designation FROM listened_channels")
    listened_channels = cursor.fetchall()

    if listened_channels:
        print("Currently listened chats:")
        for chat_jid, chat_name, agent_designation in listened_channels:
            print(f"Chat JID: {chat_jid}, Chat Name: {chat_name or 'N/A'}, Agent: {agent_designation}")
    else:
        print("No chats are currently being listened to.")

    if len(sys.argv) > 1:
        if sys.argv[1] == "map":
            if len(sys.argv) != 5:
                print("Usage: python router.py map <chat_jid> <chat_name> <agent>")
                sys.exit(1)

            chat_jid = sys.argv[2]
            chat_name = sys.argv[3]
            agent_designation = sys.argv[4]

            if agent_designation not in agent_map:
                print(f"Error: Invalid agent designation '{agent_designation}'. Valid options are: {', '.join(agent_map.keys())}")
                sys.exit(1)

            try:
                cursor.execute("""
                    INSERT INTO listened_channels (chat_jid, chat_name, agent_designation)
                    VALUES (?, ?, ?)
                    ON CONFLICT(chat_jid) DO UPDATE SET
                        chat_name=excluded.chat_name,
                        agent_designation=excluded.agent_designation
                """, (chat_jid, chat_name, agent_designation))  # Assuming chat_name is optional or can be null
                conn.commit()
                print(f"Mapped chat_jid '{chat_jid}' to agent '{agent_designation}'.")
            except sqlite3.Error as e:
                print(f"Error: Could not map chat_jid '{chat_jid}' to agent '{agent_designation}': {e}")
            sys.exit(0)

        print(f"unrecognized command {sys.argv[1]}, exiting")
        sys.exit(1)

    asyncio.run(main())
