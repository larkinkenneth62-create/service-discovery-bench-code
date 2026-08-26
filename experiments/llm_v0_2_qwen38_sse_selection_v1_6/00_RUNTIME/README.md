# Private runtime namespace

The private executor generates `SELECTION_TOKEN_BUDGET_FREEZE_V1_6.json` here after hash-binding the authorized manifests. V1.6 uses the registered conservative `UTF8_BYTE_UPPER_BOUND_V1` counter so token-budget construction requires no unregistered tokenizer download and never reads Gold. Runtime JSON, requests, responses, logs, predictions, and results are ignored and must never be committed.
