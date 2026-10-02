"""
Bidirectional Voice Agent - Nova Sonic Voice Chat Server

This module provides a FastAPI WebSocket server for real-time bidirectional
voice conversations through AWS Bedrock AgentCore.

=== Architecture ===

Browser (Web Audio API)
    | WebSocket (wss://bedrock-agentcore.../ws)
AWS Bedrock AgentCore (WebSocket Proxy)
    | WebSocket (ws://container:8080/ws)
This Container (bidi-agent)
    | Strands SDK BidiModel
Voice Model (Amazon Nova 2 Sonic on Bedrock, IAM role, no API key)

=== AgentCore WebSocket Constraints ===

AWS Bedrock AgentCore WebSocket proxy has important limitations:

1. Message Frame Size Limit: 32KB (32,768 bytes)
   - Messages exceeding this limit cause immediate connection termination
   - Audio data increases ~33% when base64 encoded, so be careful

2. Message Frame Rate Limit: 250 frames per second
   - Be cautious with high-speed audio streaming

3. Idle Session Timeout: Default 900 seconds (15 minutes)
   - Configurable via LifecycleConfiguration (60s ~ 28800s)

=== Audio Chunking Strategy ===

Nova Sonic sends small audio chunks. As a safety net, any chunk over 24KB is
split, since a frame over the AgentCore 32KB limit drops the connection:
- 24KB audio + JSON overhead (~52 bytes) = ~24KB < 32KB limit
- Base64-encoded audio is already a string, no additional encoding needed

Reference: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-websocket.html
"""

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

import boto3
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from strands.experimental.bidi.models.model import BidiModelTimeoutError
from strands.experimental.bidi.types.events import (
    BidiAudioInputEvent,
    BidiAudioStreamEvent,
    BidiConnectionStartEvent,
    BidiErrorEvent,
    BidiInterruptionEvent,
    BidiResponseCompleteEvent,
    BidiResponseStartEvent,
    BidiTextInputEvent,
    BidiTranscriptStreamEvent,
    ToolUseStreamEvent,
)
from strands.session import S3SessionManager
from strands.types._events import ToolResultEvent
from strands.types.content import ContentBlock, Message
from strands.types.session import SessionMessage
from websockets.exceptions import ConnectionClosedError

from agents import get_mcp_client, get_tools
from agents.bidi_agent import execute_builtin_tool
from config import NOVA_SONIC_VOICES, BidiModelType, create_bidi_model, get_config, resolve_voice

# Configure logging to stdout for CloudWatch
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)

# Startup verification log
print("=" * 50, flush=True)
print("[BIDI-AGENT] Module loaded - logging initialized", flush=True)
print(f"[BIDI-AGENT] Python buffering: PYTHONUNBUFFERED={os.environ.get('PYTHONUNBUFFERED', 'NOT SET')}", flush=True)
print("=" * 50, flush=True)

logger = logging.getLogger(__name__)

# Global MCP client and tools (initialized at startup)
mcp_client = None
mcp_tools = []
# Gateway tool names that accept auto-injected user_id/project_id (e.g. search, qa).
# Tools without those parameters (e.g. WebSearch) are excluded so injection is skipped.
mcp_injectable_names = set()


def convert_mcp_tool_to_bidi_format(tool) -> dict:
    """Convert MCPAgentTool to BidiModel-compatible format."""
    # MCPAgentTool has tool_name and tool_spec attributes
    tool_spec = tool.tool_spec if hasattr(tool, "tool_spec") else {}
    tool_name = tool.tool_name if hasattr(tool, "tool_name") else tool_spec.get("name", "")

    raw_input_schema = tool_spec.get("inputSchema", {})

    # Handle nested {'json': {...}} structure from MCP
    if "json" in raw_input_schema:
        input_schema = raw_input_schema["json"].copy() if isinstance(raw_input_schema["json"], dict) else {}
    else:
        input_schema = raw_input_schema.copy() if isinstance(raw_input_schema, dict) else {}

    logger.info(f"Original inputSchema for {tool_name}: {input_schema}")

    # Remove user_id and project_id from schema - they are auto-injected
    if "properties" in input_schema:
        input_schema["properties"] = {
            k: v for k, v in input_schema["properties"].items()
            if k not in ("user_id", "project_id")
        }
    if "required" in input_schema:
        input_schema["required"] = [
            r for r in input_schema["required"]
            if r not in ("user_id", "project_id")
        ]

    logger.info(f"Converted inputSchema for {tool_name}: {input_schema}")

    return {
        "name": tool_name,
        "description": tool_spec.get("description", ""),
        "inputSchema": {"json": input_schema},
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load MCP tools from the AgentCore Gateway at startup."""
    global mcp_client, mcp_tools, mcp_injectable_names

    # Initialize AgentCore MCP client. The gateway exposes document tools
    # (search, qa) and the built-in WebSearch tool.
    config = get_config()
    if config.mcp_gateway_url:
        logger.info(f"Connecting to MCP Gateway: {config.mcp_gateway_url}")
        mcp_client = get_mcp_client()
        if mcp_client:
            try:
                mcp_client.__enter__()
                raw_tools = mcp_client.list_tools_sync()
                for t in raw_tools:
                    try:
                        raw_schema = t.tool_spec.get("inputSchema", {}) if hasattr(t, "tool_spec") else {}
                        raw_props = raw_schema.get("json", raw_schema).get("properties", {})
                        converted = convert_mcp_tool_to_bidi_format(t)
                        if "user_id" in raw_props or "project_id" in raw_props:
                            mcp_injectable_names.add(converted["name"])
                        schema_keys = list(converted.get("inputSchema", {}).get("json", {}).keys())
                        logger.info(f"MCP tool converted: {converted['name']} -> inputSchema keys: {schema_keys}")
                        mcp_tools.append(converted)
                    except Exception as e:
                        logger.error(f"Failed to convert MCP tool: {e}")
                logger.info(f"Loaded {len(mcp_tools)} MCP tools: {[t['name'] for t in mcp_tools]}")
            except Exception as e:
                logger.error(f"Failed to load MCP tools: {e}")
                mcp_client = None
    else:
        logger.warning("MCP_GATEWAY_URL not set, running without MCP tools")

    yield

    if mcp_client:
        try:
            mcp_client.__exit__(None, None, None)
            logger.info("MCP client closed")
        except Exception as e:
            logger.error(f"Error closing MCP client: {e}")


class TranscriptSaver:
    """Save voice transcripts to S3 using Strands SDK S3SessionManager."""

    def __init__(
        self,
        bucket: str,
        user_id: str,
        project_id: str,
        session_id: str,
        model_type: str = "nova_sonic",
    ):
        self.session_id = session_id
        self.message_index = 0
        self.enabled = bool(bucket and user_id and project_id and session_id)
        # Store agent_id with model type for distinguishing voice sessions
        # e.g., "voice_nova_sonic"
        self.agent_id = f"voice_{model_type}"

        if self.enabled:
            prefix = f"sessions/{user_id}/{project_id}"
            self.session_manager = S3SessionManager(
                session_id=session_id,
                bucket=bucket,
                prefix=prefix,
            )
        else:
            self.session_manager = None

    def save_transcript(self, role: str, text: str) -> None:
        """Save a transcript message to S3 using SDK."""
        if not self.enabled or not self.session_manager:
            return

        now = datetime.now(UTC).isoformat()

        try:
            message = Message(
                role=role,
                content=[ContentBlock(text=text)],
            )
            session_message = SessionMessage(
                message=message,
                message_id=self.message_index,
                created_at=now,
                updated_at=now,
            )
            self.session_manager.create_message(
                session_id=self.session_id,
                agent_id=self.agent_id,
                session_message=session_message,
            )
            logger.debug(f"Saved transcript message_{self.message_index}")
            self.message_index += 1
        except Exception as e:
            logger.error(f"Failed to save transcript: {e}")

    def save_tool_result(self, tool_name: str, tool_use_id: str, status: str) -> None:
        """Save a tool result as a proper toolResult content block."""
        if not self.enabled or not self.session_manager:
            return

        now = datetime.now(UTC).isoformat()

        try:
            message = Message(
                role="assistant",
                content=[ContentBlock(toolResult={
                    "toolUseId": tool_use_id,
                    "status": status,
                    "content": [{"text": tool_name}],
                })],
            )
            session_message = SessionMessage(
                message=message,
                message_id=self.message_index,
                created_at=now,
                updated_at=now,
            )
            self.session_manager.create_message(
                session_id=self.session_id,
                agent_id=self.agent_id,
                session_message=session_message,
            )
            logger.debug(f"Saved tool_result message_{self.message_index}: {tool_name}")
            self.message_index += 1
        except Exception as e:
            logger.error(f"Failed to save tool_result: {e}")


# Default language by browser time zone, limited to languages Nova 2 Sonic speaks
# (English, French, Italian, German, Spanish, Portuguese, Hindi).
TIMEZONE_TO_LANGUAGE: dict[str, str] = {
    "Asia/Kolkata": "Hindi",
    "Asia/Calcutta": "Hindi",
    "Europe/Paris": "French",
    "Europe/Berlin": "German",
    "Europe/Rome": "Italian",
    "Europe/Madrid": "Spanish",
    "America/Sao_Paulo": "Portuguese",
    "America/Mexico_City": "Spanish",
}

BASE_SYSTEM_PROMPT = """You are a warm, professional, and helpful AI voice assistant. \
Your primary purpose is to have natural, conversational voice interactions with users in their preferred language.

Core Principles:
- Natural Conversation: Speak like a helpful friend, not a lecture. Be direct and human.
- Brevity: Keep responses concise (3-5 sentences). Start with the answer, then expand only if needed.
- Active Listening: Pay close attention to what the user says, including context from earlier in the conversation.

Response Style:
- Start by directly answering the user's question in 1-2 sentences
- Use conversational language appropriate for spoken dialogue
- Short sentences work better for voice"""

# Appended after the base prompt (which may come from S3), so it overrides older
# "do not mix languages" rules there: Hinglish is welcome.
LANGUAGE_MIRROR_PROMPT = """
LANGUAGE RULES (these replace any earlier language instructions):
- Reply in the language the user speaks. If the user talks in English, reply in English.
- Mixing languages is fine. If the user mixes Hindi and English (Hinglish), reply in the same natural mix \
and keep the English words they use, such as loan, EMI, PAN or KYC.
- Ask any question or suggestion in the language the user is talking in."""

# Hindi verbs carry the speaker's gender, so match the selected voice.
VOICE_GENDER_PROMPT = {
    "female": '\nYour voice is female. In Hindi, use feminine forms for yourself, e.g. "main batati hoon".',
    "male": '\nYour voice is male. In Hindi, use masculine forms for yourself, e.g. "main batata hoon".',
}

MCP_TOOL_PROMPT = """
## Tool Parameter Notice
When using MCP tools, `user_id` and `project_id` parameters are automatically injected by the system.
You MUST NOT specify these parameters in tool calls - they will be overwritten by the system for security."""

WEB_SEARCH_PROMPT = """
## Web Search Guidelines
Use the WebSearch tool to find current information that is not in the user's documents:
1. Keep queries concise (under 200 characters) for the best results
2. Synthesize information from multiple results before responding
3. Always cite the sources you used with their URLs
4. Note the publication date of a source when available
5. If the results are insufficient, say so rather than guessing"""


def fetch_voice_system_prompt() -> str | None:
    """Fetch voice system prompt from S3."""
    config = get_config()
    if not config.agent_storage_bucket_name:
        return None

    s3 = boto3.client("s3")
    key = "__prompts/voice/system_prompt.txt"

    try:
        response = s3.get_object(
            Bucket=config.agent_storage_bucket_name,
            Key=key,
        )
        return response["Body"].read().decode("utf-8")
    except Exception as e:
        logger.warning(f"Failed to fetch voice system prompt: {e}")
        return None


def build_system_prompt(
    timezone: str, has_mcp_tools: bool = False, has_web_search: bool = False, voice: str | None = None
) -> str:
    base_prompt = fetch_voice_system_prompt() or BASE_SYSTEM_PROMPT

    language = TIMEZONE_TO_LANGUAGE.get(timezone)
    if language:
        prompt = (
            f"{base_prompt}\n\n"
            f"The user's timezone is {timezone}. "
            f"Default to {language} unless the user speaks a different language.\n"
            f"{LANGUAGE_MIRROR_PROMPT}"
        )
    else:
        prompt = f"{base_prompt}\n{LANGUAGE_MIRROR_PROMPT}"

    if voice in NOVA_SONIC_VOICES:
        prompt += VOICE_GENDER_PROMPT[NOVA_SONIC_VOICES[voice]]

    if has_mcp_tools:
        prompt += f"\n{MCP_TOOL_PROMPT}"

    if has_web_search:
        prompt += f"\n{WEB_SEARCH_PROMPT}"

    return prompt


async def execute_tool(tool_use: dict, context: dict) -> dict:
    """Execute a tool (builtin or MCP) and return the result.

    Tool inputs and results can hold customer data (PAN, account numbers), so
    only the tool name, status and duration are logged.
    """
    tool_name = tool_use.get("name", "")
    started = time.monotonic()
    status = "error"
    try:
        result = await _run_tool(tool_use, context)
        status = result.get("status", "success")
        return result
    finally:
        logger.info(f"Tool {tool_name}: status={status}, duration_ms={(time.monotonic() - started) * 1000:.0f}")


async def _run_tool(tool_use: dict, context: dict) -> dict:
    tool_name = tool_use.get("name", "")
    tool_input = tool_use.get("input", {}) or {}
    tool_use_id = tool_use.get("toolUseId", "")

    # Try builtin tool first
    builtin_result = await execute_builtin_tool(tool_name, tool_input, context)
    if builtin_result is not None:
        return {
            "toolUseId": tool_use_id,
            "status": "success",
            "content": [{"text": json.dumps(builtin_result)}],
        }

    # Try MCP tool
    if mcp_client:
        # Inject user_id and project_id only for tools that accept them
        # (e.g. document search/qa). Built-in tools like WebSearch are skipped.
        if tool_name in mcp_injectable_names:
            if context.get("user_id"):
                tool_input["user_id"] = context["user_id"]
            if context.get("project_id"):
                tool_input["project_id"] = context["project_id"]

        try:
            result = mcp_client.call_tool_sync(name=tool_name, arguments=tool_input, tool_use_id=tool_use_id)

            # If result is already a dict with expected format, return it directly
            if isinstance(result, dict) and "toolUseId" in result:
                return result

            # MCP returns a CallToolResult with content attribute
            content = []
            if hasattr(result, "content"):
                for block in result.content:
                    if hasattr(block, "text"):
                        content.append({"text": block.text})
                    else:
                        content.append({"text": str(block)})
            else:
                content.append({"text": str(result)})

            return {
                "toolUseId": tool_use_id,
                "status": "success",
                "content": content,
            }
        except Exception as e:
            # Type only: the message or traceback can echo the tool input.
            logger.error(f"MCP tool execution failed: {tool_name} ({type(e).__name__})")
            return {
                "toolUseId": tool_use_id,
                "status": "error",
                "content": [{"text": f"Tool execution failed: {str(e)}"}],
            }

    return {
        "toolUseId": tool_use_id,
        "status": "error",
        "content": [{"text": f"Unknown tool: {tool_name}"}],
    }


app = FastAPI(lifespan=lifespan)


@app.get("/ping")
async def ping():
    return {"status": "healthy"}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket connection accepted")
    config = get_config()

    try:
        config_msg = await websocket.receive_json()
        logger.info(
            f"Session started: voice={config_msg.get('voice')}, "
            f"timezone={config_msg.get('browser_time_zone')}, "
            f"project_id={config_msg.get('project_id')}, "
            f"user_id={config_msg.get('user_id')}"
        )
    except (WebSocketDisconnect, json.JSONDecodeError) as e:
        logger.warning(f"Failed to receive config: {e}")
        return

    # Nova Sonic is the only voice model; create_bidi_model rejects any other type
    model_type = config_msg.get("model_type") or BidiModelType.NOVA_SONIC.value
    user_timezone = config_msg.get("browser_time_zone", "UTC")
    voice = resolve_voice(config_msg.get("voice"), TIMEZONE_TO_LANGUAGE.get(user_timezone))

    try:
        model = create_bidi_model(model_type=model_type, voice=voice)
        logger.info(f"Created {model_type} model")
    except ValueError as e:
        logger.error(f"Failed to create model: {e}")
        await websocket.close(code=1011, reason=str(e))
        return

    # Create transcript saver for persisting voice messages
    # Includes model_type in agent_id to distinguish sessions (e.g., voice_nova_sonic)
    transcript_saver = TranscriptSaver(
        bucket=config.session_storage_bucket_name,
        user_id=config_msg.get("user_id", ""),
        project_id=config_msg.get("project_id", ""),
        session_id=config_msg.get("session_id", ""),
        model_type=model_type,
    )
    if transcript_saver.enabled:
        logger.info(
            f"Transcript saving enabled for session {config_msg.get('session_id')} ({transcript_saver.agent_id})"
        )

    # Combine builtin tools with gateway MCP tools (search, qa, WebSearch)
    all_tools = get_tools() + mcp_tools
    has_mcp = len(mcp_tools) > 0

    try:
        custom_prompt = config_msg.get("system_prompt")
        has_web_search = any(t["name"] == "WebSearch" for t in mcp_tools)
        system_prompt = custom_prompt or build_system_prompt(
            user_timezone, has_mcp_tools=has_mcp, has_web_search=has_web_search, voice=voice
        )
        logger.info(f"Starting model with {len(all_tools)} tools: {[t['name'] for t in all_tools]}")
        await model.start(system_prompt=system_prompt, tools=all_tools)
        logger.info("voice model started successfully")
    except Exception:
        logger.exception("Failed to start voice model")
        await websocket.close(code=1011, reason="Failed to start model")
        return

    # Tool context for parameter injection
    tool_context = {
        "timezone": user_timezone,
        "project_id": config_msg.get("project_id"),
        "session_id": config_msg.get("session_id"),
        "user_id": config_msg.get("user_id"),
    }

    async def browser_to_bedrock():
        """Forward messages from browser WebSocket to voice model."""
        msg_count = 0
        try:
            async for msg in websocket.iter_json():
                msg_count += 1
                msg_type = msg.get("type")
                if msg_type == "text":
                    logger.info(f"[b2b] Text message #{msg_count}")
                    transcript_saver.save_transcript("user", msg["text"])
                    await model.send(BidiTextInputEvent(text=msg["text"]))
                elif msg_type == "audio":
                    if msg_count <= 5 or msg_count % 100 == 0:
                        logger.debug(f"[b2b] Audio chunk #{msg_count}")
                    await model.send(
                        BidiAudioInputEvent(
                            audio=msg["audio"],
                            format="pcm",
                            sample_rate=16000,
                            channels=1,
                        )
                    )
                elif msg_type == "ping":
                    # Keep-alive ping from browser, respond with pong
                    logger.debug(f"[b2b] Ping received, sending pong (msg #{msg_count})")
                    await websocket.send_json({"type": "pong"})
                elif msg_type == "stop":
                    logger.info(f"[b2b] Stop received after {msg_count} messages")
                    break
            logger.info(f"[b2b] Loop ended after {msg_count} messages")
        except WebSocketDisconnect:
            logger.info(f"[b2b] WebSocket disconnected after {msg_count} messages")

    async def bedrock_to_browser():
        """Forward events from voice model to browser WebSocket."""
        processed_tool_use_ids: set[str] = set()
        event_count = 0

        try:
            logger.info("[b2b2] Starting model.receive() loop")
            async for event in model.receive():
                event_count += 1
                if event_count <= 10 or event_count % 50 == 0:
                    logger.info(f"Event #{event_count}: {type(event).__name__}")
                if isinstance(event, BidiAudioStreamEvent):
                    # =============================================================
                    # Audio Chunking for AgentCore Compatibility
                    # =============================================================
                    #
                    # Problem:
                    # AWS Bedrock AgentCore WebSocket proxy has a 32KB message frame limit.
                    # Exceeding the 32KB limit causes AgentCore to immediately terminate
                    # the WebSocket connection.
                    #
                    # Solution (safety net, Nova Sonic chunks are normally small):
                    # Split large audio data into smaller chunks under 24KB.
                    #
                    # Why 24KB?
                    # - AgentCore limit: 32KB (32,768 bytes)
                    # - JSON overhead: {"type":"audio","audio":"...","sample_rate":24000}
                    #   adds approximately 50-100 bytes
                    # - Safety margin: 24KB + JSON overhead = 24-25KB << 32KB limit
                    # - Audio data is already base64-encoded string from the model
                    #
                    # Notes:
                    # - Nova Sonic is Bedrock-native and optimized for small chunks
                    # - Browser-side AudioPlayback automatically queues and plays
                    #   sequential chunks seamlessly
                    # =============================================================
                    MAX_AUDIO_CHUNK_SIZE = 24000  # 24KB (considering AgentCore 32KB limit)
                    audio_data = event.audio or ""
                    sample_rate = event.sample_rate

                    if len(audio_data) <= MAX_AUDIO_CHUNK_SIZE:
                        # Small enough to send as-is
                        await websocket.send_json({
                            "type": "audio",
                            "audio": audio_data,
                            "sample_rate": sample_rate,
                        })
                    else:
                        # Split large audio into chunks
                        # Browser's AudioPlayback queues and plays them sequentially
                        for i in range(0, len(audio_data), MAX_AUDIO_CHUNK_SIZE):
                            chunk = audio_data[i:i + MAX_AUDIO_CHUNK_SIZE]
                            await websocket.send_json({
                                "type": "audio",
                                "audio": chunk,
                                "sample_rate": sample_rate,
                            })
                elif isinstance(event, BidiTranscriptStreamEvent):
                    # Length only: speech can contain PAN or account numbers
                    logger.info(
                        f"Transcript: role={event.role}, is_final={event.is_final}, chars={len(event.text or '')}"
                    )
                    await websocket.send_json(
                        {
                            "type": "transcript",
                            "text": event.text,
                            "role": event.role,
                            "is_final": event.is_final,
                        }
                    )
                    # Nova Sonic: save is_final=false only (mirrors frontend display logic)
                    if event.text.strip() and not event.is_final:
                        transcript_saver.save_transcript(event.role, event.text)
                elif isinstance(event, ToolUseStreamEvent):
                    # Handle tool use requests from the model
                    tool_use = (
                        getattr(event, "current_tool_use", None)
                        or getattr(event, "tool_use", None)
                        or (event.get("current_tool_use") if hasattr(event, "get") else None)
                    )
                    if tool_use:
                        tool_use_id = tool_use.get("toolUseId")
                        tool_input = tool_use.get("input")
                        if (
                            tool_use_id
                            and tool_use_id not in processed_tool_use_ids
                            and tool_input is not None
                        ):
                            processed_tool_use_ids.add(tool_use_id)
                            tool_name = tool_use.get("name")
                            logger.info(f"Tool use: {tool_name} (id: {tool_use_id})")

                            await websocket.send_json(
                                {
                                    "type": "tool_use",
                                    "tool_name": tool_name,
                                    "tool_use_id": tool_use_id,
                                }
                            )

                            try:
                                tool_result = await execute_tool(tool_use, tool_context)
                                await model.send(ToolResultEvent(tool_result))

                                await websocket.send_json(
                                    {
                                        "type": "tool_result",
                                        "tool_name": tool_name,
                                        "tool_use_id": tool_use_id,
                                        "status": tool_result.get("status"),
                                    }
                                )
                                transcript_saver.save_tool_result(
                                    tool_name, tool_use_id, tool_result.get("status", "success"),
                                )
                            except Exception as e:
                                # Type only: the message or traceback can echo tool data.
                                logger.error(f"Tool execution error: {tool_name} ({type(e).__name__})")
                                error_result = {
                                    "toolUseId": tool_use_id,
                                    "status": "error",
                                    "content": [{"text": f"Tool execution error: {str(e)}"}],
                                }
                                try:
                                    await model.send(ToolResultEvent(error_result))
                                except Exception:
                                    logger.exception("Failed to send error result")
                                await websocket.send_json(
                                    {
                                        "type": "tool_result",
                                        "tool_name": tool_name,
                                        "tool_use_id": tool_use_id,
                                        "status": "error",
                                        "error": str(e),
                                    }
                                )
                                transcript_saver.save_tool_result(
                                    tool_name, tool_use_id, "error",
                                )
                elif isinstance(event, BidiConnectionStartEvent):
                    await websocket.send_json(
                        {
                            "type": "connection_start",
                            "connection_id": event.connection_id,
                        }
                    )
                elif isinstance(event, BidiResponseStartEvent):
                    await websocket.send_json({"type": "response_start"})
                elif isinstance(event, BidiResponseCompleteEvent):
                    await websocket.send_json({"type": "response_complete"})
                elif isinstance(event, BidiInterruptionEvent):
                    await websocket.send_json(
                        {
                            "type": "interruption",
                            "reason": event.reason,
                        }
                    )
                elif isinstance(event, BidiErrorEvent):
                    error_msg = getattr(event, 'message', None) or getattr(event, 'error', None) or str(event)
                    logger.error(f"BidiErrorEvent received: {error_msg}")
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": str(error_msg),
                        }
                    )
                else:
                    # Log unknown event types for debugging
                    logger.debug(f"Unknown event type: {type(event).__name__}: {event}")
            logger.info(f"[b2b2] model.receive() loop ended NORMALLY after {event_count} events")
        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected after {event_count} events")
        except BidiModelTimeoutError:
            logger.info("Voice chat session timed out due to inactivity")
            with suppress(Exception):
                await websocket.send_json({
                    "type": "timeout",
                    "reason": "Session timed out due to inactivity",
                })
        except ConnectionClosedError as e:
            logger.warning(f"Model WebSocket closed unexpectedly after {event_count} events: {e}")
            with suppress(Exception):
                await websocket.send_json({
                    "type": "error",
                    "message": f"Model connection closed: {e}",
                })
        except Exception as e:
            error_str = str(e)
            # Nova Sonic input timeout (user silent too long)
            if "Timed out waiting for input events" in error_str:
                logger.info(f"Model input timeout after {event_count} events: {e}")
                with suppress(Exception):
                    await websocket.send_json({
                        "type": "timeout",
                        "reason": "Session timed out due to inactivity",
                    })
            # Handle "websocket.send after close" errors gracefully
            elif isinstance(e, RuntimeError) and ("websocket" in error_str.lower() or "closed" in error_str.lower()):
                logger.info(f"WebSocket closed while sending (after {event_count} events): {e}")
            else:
                logger.exception(f"Error in bedrock_to_browser after {event_count} events")

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(browser_to_bedrock())
            tg.create_task(bedrock_to_browser())
    except* WebSocketDisconnect:
        logger.info("TaskGroup: WebSocketDisconnect")
    except* Exception as eg:
        for exc in eg.exceptions:
            logger.error(f"TaskGroup exception: {type(exc).__name__}: {exc}")
    finally:
        logger.info("Stopping model...")
        try:
            await model.stop()
            logger.info("Model stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping model: {e}")
        logger.info("Session ended")
