# RAG Quality v2 conflict fixture

This fixture contains deliberately contradictory evidence used only by the
deterministic evaluation tests. The JSON trace is read offline by the evaluator;
normal baseline API runs do not seed Redis from this directory.

If a future test needs to materialize the fixture in Redis, all of the following
conditions are mandatory before any write:

1. `RAG_EVAL_ISOLATED_STORE=1`.
2. Redis host is `127.0.0.1` or `localhost`.
3. Redis port is not the production default `6379`.
4. Redis database is `15` and every key begins with `rag-eval-v2:`.

The fixture must fail closed when any guard condition is not met. Never copy
these contradictory values into a shared or production knowledge store.
