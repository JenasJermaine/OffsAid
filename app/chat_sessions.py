"""One OffsAid chat (app/conversation.py) per WhatsApp number, shared by the Twilio and Meta webhooks.

Kept in memory, like the web demo: a restart (or a sleeping free Render instance) forgets where a player
was, and their next message simply gets the menu again. One worker only. Idle chats are dropped after
IDLE_SECONDS, and at most MAX_CHATS are kept (the least recently used goes first).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Optional

from app.conversation import Conversation

MAX_CHATS = 1000
IDLE_SECONDS = 24 * 3600

_chats: "OrderedDict[str, tuple[Conversation, float]]" = OrderedDict()
_lock = threading.Lock()


def get_conversation(sender: str, contact: Optional[str]) -> Conversation:
    """The chat for this sender (e.g. "whatsapp:+2547..."), opened if new or idle too long.
    contact: the player's number in E.164, passed to KEFWA if the case is referred (the welcome says so)."""
    now = time.monotonic()
    with _lock:
        entry = _chats.pop(sender, None)
        conv = entry[0] if entry and now - entry[1] < IDLE_SECONDS else Conversation(sender=sender, contact=contact)
        _chats[sender] = (conv, now)
        while len(_chats) > MAX_CHATS:
            _chats.popitem(last=False)
        return conv


def reset() -> None:
    """Forget every chat (tests)."""
    with _lock:
        _chats.clear()
