# Provider boundary utilities

This directory contains the latest validated V9.0.1 provider-boundary implementation consolidated from the historical hotfix package.

- `provider_boundary.py` validates structured provider requests and contains the deterministic no-network mock.
- `provider_adapter_worker.py` exposes the boundary over one-line JSON stdin/stdout.
- `run_provider_validation_v9_0_1.py` preserves the full 9,773-request historical v0.1.1 validation workflow.

The V9.0.1 label is implementation provenance, not a public dataset version. The current locally authoritative dataset is ServiceDiscoveryBench v0.2.0. Its packaged aggregate validates 9,783 requests with zero rejections: 4,798 Native v0.2.0, 4,788 inherited Unified, and 197 inherited Machine Challenge. The added ten requests extend only the Native track; the historical runner itself remains unchanged.
