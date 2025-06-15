import asyncio
import datetime
import logging
import sqlite3


logger = logging.getLogger(__name__)

cursor = None
sql_conn = None

def init_db(db_path: str):
    global cursor
    global sql_conn
    if sql_conn is not None:
        raise RuntimeError("Database connection already initialized.")
    sql_conn = sqlite3.connect(db_path)
    cursor = sql_conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS scheduled_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        prompt TEXT NOT NULL,
        time TEXT NOT NULL
    )
    """)
    sql_conn.commit()


async def list_scheduled_events():
    """
    List all scheduled events.
    """
    cursor.execute("""
    SELECT id, title, prompt, time FROM scheduled_events
    """)
    events = cursor.fetchall()
    if not events:
        return "No scheduled events found."

    event_list = []
    for event_id, title, prompt, time in events:
        event_list.append(f"ID: {event_id}, Title: {title}, Prompt: {prompt}, Time: {time}")

    return "\n".join(event_list)


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
    sql_conn.commit()

    logger.info(f"Scheduled event: {title} at {absolute_target_time.isoformat()}")
    return f"Event '{title}' scheduled for {absolute_target_time.isoformat()}."


async def event_handler_loop(stop_event):
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
                sql_conn.commit()

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1)  # Check every second or exit if stop_event is set
        except asyncio.TimeoutError:
            pass  # Timeout elapsed, continue the loop
    print("Event handler stopped.")


all_tools = [list_scheduled_events, schedule_event]
