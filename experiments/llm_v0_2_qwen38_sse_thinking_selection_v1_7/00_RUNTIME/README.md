# Private runtime namespace

The private executor generates `SELECTION_TOKEN_BUDGET_FREEZE_V1_7.json` here after hash-binding the authorized manifests. V1.7 uses the registered conservative `UTF8_BYTE_UPPER_BOUND_PLUS_REASONING_4096_V1` counter: the legal answer upper bound plus 64 safety tokens and a fixed 4,096-token reasoning allowance. Construction requires no unregistered tokenizer download and never reads Gold. Runtime JSON, requests, responses, reasoning, logs, predictions, and results are ignored and must never be committed.
