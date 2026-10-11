# Chat history controls

Search in chat follows Settings in the workspace navigation. It searches message contents and archive names for the selected project context (All Projects is the global conversation). Selecting a result opens matching messages, including older messages; Read full chat opens the entire saved conversation. Long conversations and result lists have pagination.

Archive and Clear share the former search field's toolbar space. Both show a confirmation dialog with Cancel focused initially. Archive offers an editable project/date name, preserves all messages and the original conversation/thread metadata, and starts a fresh conversation. Clear removes only the current conversation's messages and starts a fresh conversation. Existing archives, tasks, project memory, execution evidence and audit events remain. Both actions show an eight-second status notification without changing the composer layout. Unsent drafts are preserved.

The schema 6 migration adds chat_archives without rewriting existing rows. The archive transaction moves the prior conversation out of the active context and records its name/date alongside its original ID. A fresh active conversation has no Codex thread or summary. Clear removes the old message/conversation rows and tool-schema setting. Neither action calls Codex or launches workers.

Both endpoints require the owner session, CSRF token, explicit confirmation, current conversation ID and last message ID. Empty chats, changed confirmations and unfinished responses are rejected. A message submission waiting for model routes cannot append into a conversation that was archived or cleared meanwhile. Search/detail APIs enforce the selected context.

The historical validation/deployment report remains in Git history. Current coverage is in `tests/test_chat_history.py` and `scripts/browser_smoke.py`; current deployment evidence is in [CODEBASE_CLEANUP.md](CODEBASE_CLEANUP.md).
