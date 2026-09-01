# DeepSeek V4 Flash selection prompt contract V2.2

The provider sees only the query, task type, prediction target, ordered candidate documents, the applicable output instruction, the local JSON shape, and the allowed candidate IDs. Gold IDs, Gold cardinality, split labels, and scorer metadata are forbidden.

The final `content` must be exactly one JSON object. Reasoning may arrive only through `reasoning_content`; it is retained as separate audit metadata and never concatenated into or scored as the answer.

`single_api_recommendation` returns both a Top-5 ranking and an independently chosen complete minimal sufficient API set. The selected set may contain more than five APIs and APIs outside the Top-5.
