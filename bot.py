import logging
import httpx
import re
import os
import json
import datetime
from dotenv import load_dotenv
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from pymongo import MongoClient

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

load_dotenv()

# --- Configuration ---
API_BASE = os.getenv("API_BASE", "https://gptcloud.arc53.com")
API_URL = API_BASE + "/api/answer"
API_KEY = os.getenv("API_KEY")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")

# --- Load Additional Agents ---
ADDITIONAL_AGENTS = {}
for key, value in os.environ.items():
    if key.startswith("API_KEY_") and key != "API_KEY":
        agent_name = key[8:].lower()
        ADDITIONAL_AGENTS[agent_name] = value
logger.info(f"Loaded {len(ADDITIONAL_AGENTS)} additional agents: {list(ADDITIONAL_AGENTS.keys())}")

if not API_KEY and ADDITIONAL_AGENTS:
    # Fallback to the first available agent (sorted alphabetically for determinism)
    first_agent = sorted(ADDITIONAL_AGENTS.keys())[0]
    API_KEY = ADDITIONAL_AGENTS[first_agent]
    logger.warning(f"API_KEY not set. Defaulting to agent '{first_agent}' key.")
API_CONTEXT_MESSAGES_COUNT = 20 # Number of messages (10 pairs) to use for API context

# --- Storage Configuration ---
STORAGE_TYPE = os.getenv("STORAGE_TYPE", "memory")
MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "slack_bot_memory")
MONGODB_COLLECTION_NAME = os.getenv("MONGODB_COLLECTION_NAME", "chat_histories")

# --- Global Storage Variables ---
mongo_client = None
mongo_collection = None
in_memory_storage = {}

# --- Initialize Storage ---
if STORAGE_TYPE.lower() == "mongodb":
    if not MONGODB_URI:
        logger.error("STORAGE_TYPE is 'mongodb' but MONGODB_URI is not set. Exiting.")
        exit(1)
    try:
        logger.info(f"Attempting to connect to MongoDB: {MONGODB_URI[:15]}... DB: {MONGODB_DB_NAME}")
        mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
        mongo_client.admin.command('ismaster')
        db = mongo_client[MONGODB_DB_NAME]
        mongo_collection = db[MONGODB_COLLECTION_NAME]
        logger.info(f"Successfully connected to MongoDB and selected collection '{MONGODB_COLLECTION_NAME}'.")
    except Exception as e:
        logger.error(f"An unexpected error occurred during MongoDB initialization: {e}", exc_info=True)
        logger.warning("Falling back to in-memory storage.")
        STORAGE_TYPE = "memory"
        mongo_client = None
        mongo_collection = None
elif STORAGE_TYPE.lower() == "memory":
    logger.info("Using in-memory storage for chat history (will be lost on restart).")
else:
    logger.warning(f"Unknown STORAGE_TYPE '{STORAGE_TYPE}'. Defaulting to in-memory storage.")
    STORAGE_TYPE = "memory"

# --- Storage Access Functions ---

async def get_conversation_data(context_id: str) -> dict:
    """
    Fetches chat history, conversation ID, and user info from the configured storage.
    Uses context_id (channel_id or thread_ts) as the key.
    """
    default_data = {"history": [], "conversation_id": None, "user_info": None}

    if STORAGE_TYPE == "mongodb" and mongo_collection is not None:
        try:
            doc = mongo_collection.find_one({"_id": context_id})
            if doc:
                history = doc.get("conversation_history", [])
                conv_id = doc.get("conversation_id", None)
                user_info = doc.get("user_info", None)
                return {"history": history, "conversation_id": conv_id, "user_info": user_info}
            else:
                return default_data
        except Exception as e:
            logger.error(f"MongoDB Error fetching data for context_id {context_id}: {e}", exc_info=True)
            return default_data
    else:
        data = in_memory_storage.get(context_id, default_data)
        return {
            "history": data.get("history", []),
            "conversation_id": data.get("conversation_id", None),
            "user_info": data.get("user_info", None)
        }


async def save_conversation_data(context_id: str, history: list, conversation_id: str | None, user_info: dict | None):
    """
    Saves the complete chat history, conversation ID, and user info to the configured storage.
    """
    if STORAGE_TYPE == "mongodb" and mongo_collection is not None:
        try:
            update_data = {
                "conversation_history": history,
                "conversation_id": conversation_id,
            }
            if user_info:
                update_data["user_info"] = user_info

            update_doc = {
                "$set": update_data,
                "$currentDate": {"last_updated": True}
            }
            mongo_collection.update_one(
                {"_id": context_id},
                update_doc,
                upsert=True
            )
            logger.debug(f"Saved full history for context {context_id}")
        except Exception as e:
            logger.error(f"MongoDB Error saving data for context_id {context_id}: {e}", exc_info=True)
    else: # in-memory storage
        if context_id not in in_memory_storage:
             in_memory_storage[context_id] = {}

        in_memory_storage[context_id].update({
            "history": history,
            "conversation_id": conversation_id,
            "last_updated": datetime.datetime.now(datetime.timezone.utc)
        })
        if user_info:
            in_memory_storage[context_id]["user_info"] = user_info
        logger.debug(f"Saved full in-memory history for context {context_id}")

# --- Helper Functions ---

def route_message(text: str) -> tuple[str, str]:
    """
    Determines which API key to use based on the message prefix.
    Returns (api_key, cleaned_text).
    """
    if not text:
        return API_KEY, text

    # Check for #AGENT prefix
    first_space = text.find(' ')
    if first_space != -1:
        tag = text[:first_space]
        content = text[first_space+1:]
    else:
        tag = text
        content = ""

    if tag.startswith('#'):
        agent_name = tag[1:].lower()
        if agent_name in ADDITIONAL_AGENTS:
            return ADDITIONAL_AGENTS[agent_name], content
        # If it starts with # but not a known agent, we treat it as normal text (or unknown agent)
        return None, None
    
    return API_KEY, text

def format_history_for_api(messages: list) -> list:
    """
    Converts internal history format [{'role': 'user', 'content': '...'}, ...]
    to the API required format [{'prompt': '...', 'response': '...'}, ...].
    """
    api_history = []
    i = 0
    while i < len(messages):
        if messages[i].get("role") == "user" and "content" in messages[i]:
            prompt_content = messages[i]["content"]
            response_content = None
            if i + 1 < len(messages) and messages[i+1].get("role") == "assistant" and "content" in messages[i+1]:
                response_content = messages[i+1]["content"]
                api_history.append({"prompt": prompt_content, "response": response_content})
                i += 2
            else:
                i += 1
        else:
            i += 1
    return api_history

def convert_markdown_to_slack(text: str) -> str:
    """
    Converts standard Markdown links [Label](URL) to Slack format <URL|Label>.
    """
    return re.sub(r'\[(.*?)\]\((.*?)\)', r'<\2|\1>', text)

async def generate_answer(question: str, messages: list, conversation_id: str | None, api_key: str) -> dict:
    """
    Generates an answer using the external DocsGPT API.
    """
    if not api_key:
        logger.warning("API_KEY is not set. Cannot call DocsGPT API.")
        return {"answer": "Error: Backend API key is not configured.", "conversation_id": conversation_id}

    context_messages = messages[-API_CONTEXT_MESSAGES_COUNT:]

    try:
        formatted_history = format_history_for_api(context_messages)
        history_json = json.dumps(formatted_history)
    except TypeError as e:
        logger.error(f"Failed to serialize history to JSON: {e}", exc_info=True)
        history_json = json.dumps([])

    payload = {
        "question": question,
        "api_key": api_key,
        "history": history_json,
        "conversation_id": conversation_id
    }
    headers = {
        "Content-Type": "application/json; charset=utf-8"
    }
    timeout = 120.0
    default_error_msg = "Sorry, I couldn't get an answer from the backend service."

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(API_URL, json=payload, headers=headers, timeout=timeout)
            response.raise_for_status()

            data = response.json()
            answer = data.get("answer", default_error_msg)
            returned_conv_id = data.get("conversation_id", conversation_id)
            return {"answer": answer, "conversation_id": returned_conv_id}

    except (httpx.ConnectTimeout, httpx.ReadTimeout):
        logger.error("DocsGPT API timed out.")
        return {"answer": "The brain is currently offline, please try again later", "conversation_id": conversation_id}
    except httpx.HTTPStatusError as exc:
        logger.error(f"HTTP error calling DocsGPT API: {exc}")
        return {"answer": f"{default_error_msg} (Error: {exc.response.status_code})", "conversation_id": conversation_id}
    except Exception as e:
        logger.error(f"Unexpected error in generate_answer: {e}", exc_info=True)
        return {"answer": f"{default_error_msg} (Unexpected Error)", "conversation_id": conversation_id}

# --- Slack App Setup ---
app = AsyncApp(token=SLACK_BOT_TOKEN)

async def process_query(event, say, text):
    """
    Unified processor for both DMs and App Mentions.
    """
    # Determine Context ID (Thread TS if in thread, else Channel ID)
    if "thread_ts" in event:
        context_id = event["thread_ts"]
    else:
        context_id = event["channel"]

    user_id = event["user"]
    logger.info(f"Processing query from user {user_id} in context {context_id}: {text[:50]}...")

    # Check for empty text after cleaning
    if not text:
        await say("Hi! How can I help you?", thread_ts=event.get("ts"))
        return

    # Get History
    chat_data = await get_conversation_data(context_id)
    current_history = chat_data["history"]
    current_conversation_id = chat_data["conversation_id"]

    # Append User Message
    current_history.append({"role": "user", "content": text})

    # Route Message (Agent Check)
    target_api_key, cleaned_question = route_message(text)

    if target_api_key is None:
        await say(f"Error: Unknown agent tag in '{text}'. Available agents: {list(ADDITIONAL_AGENTS.keys())}", thread_ts=event.get("ts"))
        return

    # Update history with cleaned question
    current_history[-1]["content"] = cleaned_question

    # Generate Answer
    response_doc = await generate_answer(cleaned_question, current_history, current_conversation_id, target_api_key)
    answer = response_doc["answer"]
    new_conversation_id = response_doc["conversation_id"]

    logger.info(f"Raw answer from API: '{answer}'")

    if not answer or not str(answer).strip():
        answer = "Sorry, I received an empty response from the brain."
        logger.warning("Received empty or whitespace-only answer from API. Replaced with fallback text.")

    # Format Answer (Markdown -> Slack)
    slack_answer = convert_markdown_to_slack(answer)
    logger.info(f"Formatted Slack answer: '{slack_answer}'")

    # Append Assistant Message
    current_history.append({"role": "assistant", "content": answer})
    
    # Save Data
    user_info = {"id": user_id} 
    await save_conversation_data(context_id, current_history, new_conversation_id, user_info)

    # Send Reply
    # Default to replying in thread for mentions to keep channels tidy.
    
    thread_ts = event.get("thread_ts", event.get("ts")) # Reply to the message's thread or the message itself
    if not slack_answer or not slack_answer.strip():
         slack_answer = "Sorry, an error occurred while formatting the answer."
         logger.error("slack_answer is empty after processing. Using fallback.")

    await say(slack_answer, thread_ts=thread_ts)


@app.event("app_mention")
async def handle_app_mention(event, say):
    text = event.get("text", "")
    # Strip the mention <@BOTID>
    cleaned_text = re.sub(r"^<@.*?>\s*", "", text).strip()
    await process_query(event, say, cleaned_text)

@app.event("message")
async def handle_message(event, say):
    # Check if it's a DM
    if event.get("channel_type") == "im":
        text = event.get("text", "")
        await process_query(event, say, text)
    # Ignore other messages (e.g. in channels where bot is not mentioned)

async def main():
    if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
        logger.critical("SLACK_BOT_TOKEN or SLACK_APP_TOKEN not set!")
        return

    handler = AsyncSocketModeHandler(app, SLACK_APP_TOKEN)
    logger.info("Starting Slack Bot (Socket Mode)...")
    await handler.start_async()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
