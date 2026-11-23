"""Adapter from the Tk ChatView reply-handler contract to Friday's live worker.

ChatView calls this function on its own worker thread. AIWorker and its
Settings-backed provider lookup must be created/started on Friday's Qt thread,
so the adapter marshals that setup through agent_overlay's Qt invoker and waits
for the worker result without blocking either UI thread.
"""
from __future__ import annotations

from queue import Queue

from .model import ReplyError


def _history_context(history: list[dict], prompt: str) -> str:
    """Bounded text transcript; attachment paths are never opened here."""
    items = list(history or [])
    # ChatView includes the just-submitted user message in its history.
    if items and items[-1].get("role") == "user" and items[-1].get("content") == prompt:
        items.pop()
    lines = []
    budget = 12000
    for item in items[-20:]:
        role = "User" if item.get("role") == "user" else "Friday"
        content = str(item.get("content", ""))[:3000]
        attachments = item.get("attachments") or []
        line = f"{role}: {content}"
        if attachments:
            names = ", ".join(str(name)[:200] for name in attachments[:8])
            line += f" [file-name references only: {names}]"
        line = line[:budget]
        if not line:
            continue
        lines.append(line)
        budget -= len(line) + 1
        if budget <= 0:
            break
    if not lines:
        return ""
    return ("\n\nChat panel conversation context (chronological; the current user "
            "message follows separately). File attachments are represented by "
            "names only; their contents have not been read.\n" + "\n".join(lines))


def settings_reply_handler(prompt: str, history: list[dict]) -> str:
    """Run a Chat-panel turn using Settings → Voice, preserving prior turns."""
    import agent_overlay

    if getattr(agent_overlay, "_main_thread_invoker", None) is None:
        raise ReplyError("Friday's model runtime is not ready yet. Please try again shortly.")

    result: Queue = Queue(maxsize=1)

    def start_worker():
        try:
            worker = agent_overlay.AIWorker(
                prompt, chat_history=_history_context(history, prompt),
                chat_surface=True)
            # Retain the parentless QThread until finished, as in Friday's
            # existing IDE chat bridge, then release its Qt wrapper safely.
            pending = agent_overlay._pending_chat_workers
            pending.add(worker)
            worker.succeeded.connect(lambda text: result.put((True, text)))
            worker.failed.connect(lambda text: result.put((False, text)))

            def cleanup():
                pending.discard(worker)
                worker.deleteLater()

            worker.finished.connect(cleanup)
            worker.start()
        except Exception as exc:  # noqa: BLE001
            result.put((False, f"Couldn't start Friday's Chat reply: {exc}"))

    agent_overlay._on_qt_main_thread(start_worker)
    succeeded, text = result.get()
    if not succeeded:
        message = str(text)
        if message.startswith("No runnable model is configured under Settings → Voice"):
            raise ReplyError(message)
        raise ReplyError("Friday couldn't get a reply from the models in Settings → Voice. "
                         "Check the Voice model configuration and try again.")
    return str(text)


settings_reply_handler._friday_settings_backed = True


def build_ide_handoff(title: str, history: list[dict], draft: str = "") -> str:
    """Turn the selected Chat conversation into a bounded IDE development brief."""
    transcript = []
    budget = 18000
    for item in list(history or [])[-36:]:
        role = "User" if item.get("role") == "user" else "Friday"
        content = str(item.get("content", ""))[:3500]
        attachments = item.get("attachments") or []
        line = f"{role}: {content}"
        if attachments:
            names = ", ".join(str(name)[:200] for name in attachments[:8])
            line += f" [attached filename references only: {names}; file contents not transferred]"
        line = line[:budget]
        if line.strip():
            transcript.append(line)
            budget -= len(line) + 1
        if budget <= 0:
            break
    draft = str(draft or "").strip()
    if draft:
        transcript.append("Unsent user draft: " + draft[:3000])
    if not transcript:
        raise ReplyError("This conversation has no messages to move to the IDE yet.")
    brief = "\n".join(transcript)
    title = str(title or "Chat conversation").strip()[:180]
    return (
        "The user selected Continue in IDE. Use this conversation as the "
        "development brief for the active workspace. Inspect the workspace, "
        "identify the concrete development request in the user's messages, "
        "and carry it out in the IDE rather than only summarizing the chat. "
        "Treat Friday's messages as context, and respect later user corrections "
        "over earlier suggestions. Ask a focused question in the IDE chat if "
        "the required goal is still ambiguous. Attached files are filename "
        "references only; their contents were not transferred.\n\n"
        f"Conversation title: {title}\n\nConversation transcript:\n{brief}"
    )


def handoff_to_ide(title: str, history: list[dict], draft: str = "") -> str:
    """Send the selected transcript through Friday's Coding-scoped IDE chat."""
    import agent_tools
    return agent_tools.handoff_chat_to_ide(build_ide_handoff(title, history, draft))


handoff_to_ide._friday_ide_bridge = True
