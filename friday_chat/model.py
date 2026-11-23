"""Quiet Chat conversation state and explicitly simulated sample replies."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable
from uuid import uuid4


ReplyHandler = Callable[[str, list[dict]], str]


class ReplyError(RuntimeError):
    """An expected problem obtaining a live reply (for example, no model configured)."""


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class Message:
    role: str
    content: str
    created_at: str = field(default_factory=timestamp)
    attachments: list[str] = field(default_factory=list)

    def wire(self) -> dict:
        """A provider-neutral message. Attachments are filenames, NOT file contents."""
        return {"role": self.role, "content": self.content,
                "attachments": list(self.attachments)}


@dataclass
class Conversation:
    title: str = "New conversation"
    id: str = field(default_factory=lambda: uuid4().hex)
    messages: list[Message] = field(default_factory=list)
    created_at: str = field(default_factory=timestamp)
    updated_at: str = field(default_factory=timestamp)

    def add(self, message: Message) -> None:
        self.messages.append(message)
        self.updated_at = timestamp()
        if message.role == "user" and self.title == "New conversation":
            source = " ".join(message.content.split()) or "A file reference"
            self.title = source if len(source) <= 30 else source[:27].rsplit(" ", 1)[0] + "…"


def encode_state(conversations: list[Conversation], active_id: str) -> dict:
    return {"version": 1, "active_id": active_id,
            "conversations": [asdict(c) for c in conversations]}


def decode_state(data) -> tuple[list[Conversation], str | None]:
    """Validate and reconstruct saved UI state without reading any file."""
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unrecognized conversation file")
    raw_conversations = data.get("conversations")
    if not isinstance(raw_conversations, list):
        raise ValueError("Conversations must be a list")
    conversations = []
    seen = set()
    for raw in raw_conversations:
        if not isinstance(raw, dict):
            raise ValueError("Invalid conversation")
        identifier = raw.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("Invalid or duplicate conversation ID")
        seen.add(identifier)
        if not isinstance(raw.get("title"), str) or not isinstance(raw.get("messages"), list):
            raise ValueError("Invalid conversation fields")
        messages = []
        for item in raw["messages"]:
            if (not isinstance(item, dict) or item.get("role") not in ("user", "assistant")
                    or not isinstance(item.get("content"), str)):
                raise ValueError("Invalid message")
            attachments = item.get("attachments", [])
            if not isinstance(attachments, list) or not all(isinstance(n, str) for n in attachments):
                raise ValueError("Invalid file references")
            created = item.get("created_at", timestamp())
            if not isinstance(created, str):
                raise ValueError("Invalid timestamp")
            messages.append(Message(item["role"], item["content"], created, attachments))
        created, updated = raw.get("created_at", timestamp()), raw.get("updated_at", timestamp())
        if not isinstance(created, str) or not isinstance(updated, str):
            raise ValueError("Invalid timestamp")
        conversations.append(Conversation(raw["title"], identifier, messages, created, updated))
    active = data.get("active_id")
    if not isinstance(active, str) or active not in seen:
        active = conversations[0].id if conversations else None
    return conversations, active


def demo_conversations() -> list[Conversation]:
    first = Conversation(title="A calmer workday")
    first.messages = [
        Message("assistant", "Hey. What’s on your mind?"),
        Message("user", "Help me make my day feel a little less busy."),
        Message("assistant", "## Let’s keep it simple.\n\n"
                "01   Choose one thing that really matters.\n"
                "02   Make space for a proper break.\n"
                "03   Leave a little room for the unexpected.\n\n"
                "You don’t have to do everything to have a good day."),
    ]
    second = Conversation(title="A few words, made simpler", messages=[
        Message("user", "Help me write a clearer introduction."),
        Message("assistant", "Start with what you mean.\n\n"
                "One honest sentence is often better than a complicated paragraph. "
                "What’s the main thing you want your reader to know?"),
    ])
    third = Conversation(title="Something worth making", messages=[
        Message("assistant", "Big ideas can start small.\n\n"
                "What’s something you’d enjoy making, even just for yourself?"),
    ])
    return [first, second, third]


def demo_reply(text: str, history: list[dict]) -> str:
    """Sample responses only. This does not call a model, read files, or use the web."""
    if history and history[-1].get("attachments"):
        return ("The file names are now part of this conversation.\n\n"
                "This local demo doesn’t read or upload files. A custom reply handler "
                "can add file processing when you connect your own backend.")
    lower = text.lower()
    if any(word in lower for word in ("plan", "day", "busy", "focus")):
        return ("## A little less, done well.\n\n"
                "Choose one priority. Give it a small, realistic block of time. "
                "Then leave some breathing room.\n\n"
                "What’s the one thing you’d feel good about finishing today?")
    if any(word in lower for word in ("write", "draft", "words", "introduction")):
        return ("## Start with one clear sentence.\n\n"
                "Write what you mean, as if you were explaining it to a friend. "
                "We can make it shorter and smoother from there.\n\n"
                "Who are you writing for?")
    if any(word in lower for word in ("idea", "create", "make", "explore")):
        return ("## Give the idea a little room.\n\n"
                "What would it help someone do? Start with the smallest version "
                "that could be useful, and build from there.")
    if any(word in lower for word in ("hello", "hey", "hi ")) or lower.strip() == "hi":
        return "Hey. Good to have you here.\n\nWhat would you like to work on?"
    return ("Your message is here in your space.\n\n"
            "These are sample replies, not a live AI model. This panel’s connection "
            "to Friday’s live conversation engine has not been enabled yet.")


def export_text(conversation: Conversation) -> str:
    lines = [conversation.title, "=" * len(conversation.title), ""]
    for message in conversation.messages:
        lines.append("You" if message.role == "user" else "Friday")
        lines.append(message.content)
        if message.attachments:
            lines.append("File references: " + ", ".join(message.attachments))
        lines.append("")
    return "\n".join(lines)
