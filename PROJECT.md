# AI Manager

Self-hosted conversational portfolio manager for one owner. Permanent scope and acceptance criteria are in MASTER_SPEC.md; checkpoint status in STATE.md and IMPLEMENTATION_PLAN.md.

Python + FastAPI is selected for asynchronous streaming, typed request validation and straightforward local hosting. SQLite WAL is the authoritative local store; short transactions and revision checks protect concurrent updates. Single manager service owns the scheduler; isolated workers have separate process lifetimes.

No public hosting or paid API provider is authorized. Existing customer-service AI-Manager is a different application and must remain intact.
