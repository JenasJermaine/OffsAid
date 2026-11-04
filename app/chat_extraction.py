"""Read a chat screenshot into messages with a sender.

This is a separate vision call from document-field extraction, so the document
check's schema and the people who already consume it stay as they are. A vision
model transcribes each bubble. Alignment decides who sent it: right-hand bubbles
are the player, left-hand bubbles are the other party. Colour is not enough,
because dark mode changes it.

Message text is not written to the application log.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from app.config import settings
from app.document_extraction import load_document
from app.llm import DATA_NOT_INSTRUCTIONS, parse_structured

ChatApp = Literal["whatsapp", "telegram", "sms", "other"]
Sender = Literal["agent", "player", "unknown"]
BubbleType = Literal["text", "voice", "image", "document", "system", "deleted"]

# Voice notes, pictures, files and deleted bubbles are not wording. Empty text
# makes the message filter drop them. Stickers are transcribed as images.
_NON_TEXT = {"voice", "image", "document", "deleted", "system"}

CHAT_SYSTEM = """You transcribe a screenshot of a chat the player forwarded (WhatsApp, \
Telegram, SMS or similar) and return only the structured messages.

Rules:
- In a WhatsApp or Telegram screenshot, bubbles on the right (usually green or blue) \
are the player, the person who took the screenshot. Bubbles on the left (white or grey) \
are the other party, the agent. Decide by alignment, not by colour alone, because dark \
mode changes the colours. If you cannot tell who sent a bubble, use sender "unknown".
- Transcribe verbatim. Keep Swahili, Sheng, typos, emoji, phone numbers and amounts \
exactly. Do not translate, correct or summarise.
- If a bubble contains a reply-quote (a block showing an earlier message), put that \
block in quoted_reply and do not put it in text. The player's words must not be \
attributed to the agent.
- Skip date separators, encryption banners, read ticks and delivery ticks. Put a \
timestamp in time (HH:MM) and do not put it in text.
- Voice notes, images, documents, stickers and deleted messages: add an entry with \
the matching type and an empty text. Use type "image" for stickers.
- Messages that continue over several bubbles from the same sender stay separate \
entries, in order. A single bubble that wraps across lines is one entry.
- Group chats with several named senders are out of scope. If you see one, set \
group_chat to true and mark every non-player sender as "agent".
- Set app to whatsapp, telegram, sms, or other.
"""


class ChatMessage(BaseModel):
    sender: Sender
    text: str = Field(description="Verbatim bubble text, excluding any quoted reply. Empty when there is no text.")
    type: BubbleType
    quoted_reply: str = Field(description="Text of the quoted block inside the bubble, or an empty string.")
    time: str = Field(description="HH:MM, or an empty string.")


class ChatShot(BaseModel):
    app: ChatApp
    group_chat: bool = Field(description="True when several named people other than the player wrote in the chat.")
    messages: List[ChatMessage]


@dataclass
class ChatRead:
    shot: Optional[ChatShot]
    unavailable: bool


def extract_chat(data: bytes, mime_type: str) -> ChatRead:
    """Transcribe one screenshot. unavailable=True when the vision model did not run."""
    doc = load_document(data, mime_type)
    if not settings.llm_enabled:
        return ChatRead(None, True)
    encoded = base64.b64encode(doc.data).decode("ascii")
    if doc.mime_type == "application/pdf":
        block = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": encoded},
        }
    else:
        block = {
            "type": "image",
            "source": {"type": "base64", "media_type": doc.mime_type, "data": encoded},
        }
    shot = parse_structured(
        ChatShot,
        system=CHAT_SYSTEM + "\n" + DATA_NOT_INSTRUCTIONS,
        content=[block, {"type": "text", "text": "Transcribe this chat screenshot."}],
        max_tokens=8000,
    )
    if shot is None:
        return ChatRead(None, True)
    return ChatRead(shot, False)


def to_checker_input(shot: ChatShot) -> dict:
    """Map the transcript into the dict check_chat already accepts.

    Non-text bubbles keep an empty text so they are not classified. Quoted replies
    stay out of text, so the player's earlier words are not scored as the agent's.
    """
    messages = []
    for message in shot.messages:
        text = "" if message.type in _NON_TEXT else message.text.strip()
        messages.append({"sender": message.sender, "text": text, "type": message.type})
    return {"app": shot.app, "group_chat": shot.group_chat, "messages": messages}
