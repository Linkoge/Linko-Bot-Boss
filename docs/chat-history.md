# Chat history controls

Search in chat follows Settings in the workspace navigation. It searches message contents and archive names for the selected project context (All Projects is the global conversation). Selecting a result opens matching messages, including older messages; Read full chat opens the entire saved conversation. Long conversations and result lists have pagination.

The compact mode selector shares the toolbar with Copy all, Copy five, Archive and Clear. Copy all copies every message in the current conversation, fetching earlier pages beyond the 200 displayed messages. Copy five copies the latest five messages, or all messages when fewer than five exist. Both preserve author labels, line breaks and chronological order, include received streaming text, and show a short success notice. Empty chats disable copying. Clipboard API failures fall back to browser copying and restore the text selection.

Periodic refreshes keep existing message elements and text nodes. Streaming appends to the existing text node. Selecting text or reading older messages suppresses automatic scrolling; chats already at the bottom continue following incoming text.

Archive and Clear both show a confirmation dialog with Cancel focused initially. Archive offers an editable project/date name, preserves all messages and the original conversation/thread metadata, and starts a fresh conversation. Clear removes only the current conversation's messages and starts a fresh conversation. Existing archives, tasks, project memory, execution evidence and audit events remain. Both actions show an eight-second status notification without changing the composer layout. Unsent drafts are preserved.

The schema 6 migration adds chat_archives without rewriting existing rows. The archive transaction moves the prior conversation out of the active context and records its name/date alongside its original ID. A fresh active conversation has no Codex thread or summary. Clear removes the old message/conversation rows and tool-schema setting. Neither action calls Codex or launches workers.

Both endpoints require the owner session, CSRF token, explicit confirmation, current conversation ID and last message ID. Empty chats, changed confirmations and unfinished responses are rejected. A message submission waiting for model routes cannot append into a conversation that was archived or cleared meanwhile. Search/detail APIs enforce the selected context.

The historical validation/deployment report remains in Git history. Current coverage is in `tests/test_chat_history.py` and `scripts/browser_smoke.py`; current deployment evidence is in [CODEBASE_CLEANUP.md](CODEBASE_CLEANUP.md).
