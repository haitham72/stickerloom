import os
os.environ["STICKERLOOM_NO_REAL_CLI"] = "1"       # no test may reach the real Higgsfield CLI (it spends credits)
os.environ["STICKERLOOM_ACTIVITY"] = "0"            # the server's activity lines (runtime/activity.py) stay out of the test output
# no test may reach a language model either: not a cloud API (it bills), not a local server (slow, different on every machine). A test that needs an answer
# passes a fake (`Brain(complete=...)`, `mock.patch.object(llm, "complete", ...)`) or sets a provider for itself and puts it back.
os.environ["STICKERLOOM_LLM_PROVIDER"] = "none"
os.environ["STICKERLOOM_AGENT_PROVIDER"] = "none"
os.environ["STICKERLOOM_VISION_PROVIDER"] = "none"
# a developer's .env may enable the Postgres write-through, which would send every test's idempotency keys, tasks and jobs to a real database (and make a key from one
# run answer the next): off for the suite; a test that needs the write-through turns it on for itself (tests/test_jobqueue.py).
os.environ["STICKERLOOM_DB_WRITE"] = "0"
