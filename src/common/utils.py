from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
import asyncio
import logging
from pydantic_ai.usage import UsageLimits

logger = logging.getLogger(__name__)

def truncate_history(last_result, max_length=20) -> list:
    if last_result is None:
        return []

    history = last_result.all_messages()

    # the first element is the system prompt -- we leave it in
    if len(history) > max_length:
        history = history[:1] + history[-10:]

    while len(history) > 1 and history[1].parts[0].part_kind.startswith('tool'):
        history = history[:1] + history[2:]

    return history


async def agent_loop(agent, prompt, stop_event):
    result = None
    prompt_session = PromptSession(history=FileHistory(os.path.expanduser('~/.sheba_history')))
    while not stop_event.is_set():
        history = truncate_history(result)
        try:
            if prompt:
                result = await agent.run(user_prompt=prompt,
                                         message_history=history,
                                         usage_limits=UsageLimits(request_limit=10,
                                                                  response_tokens_limit=5000),
                )
                print(result.output)
                print(result.usage())
        except anyio.ClosedResourceError:
            logger.exception("MCP server connection closed unexpectedly. Terminating everything.")
            stop_event.set()
            break
        except Exception as e:
            logger.exception(f"Error while processing prompt: {prompt} with history {history}")
            if hasattr(e, 'message') and 'maximum context length' in e.message:
                print("The context is too large, so I'm forgetting most of the history")
                history = truncate_history(result, max_length=5)
            else:
                try:
                    result = await agent.run(user_prompt=f"""I just asked you this:

{prompt}

You failed with the error below. Don't make any additional calls right now, wait for further user guidance.
{e.args}. """,
                                   message_history=history,
                                   usage_limits=UsageLimits(request_limit=2,
                                                            response_tokens_limit=5000),
                                   )
                except Exception as e2:
                    import pdb
                    pdb.set_trace()
                print(result.output)
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
