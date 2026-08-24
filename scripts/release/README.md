# Release utilities

- `build_v0_2_composable_expansion.py` is the current v0.2.0 build recipe. It preserves all 60,078 v0.1.1 rows, merges 162 composable-expansion rows, deterministically rewrites active release documentation, validates the 28-column task schema across all 60,240 rows, checks track coverage and Provider isolation, scans private paths, and emits a new immutable release directory and ZIP.
- `build_paper_release_v0_1_1.py` is the frozen v0.1.1 paper-package recipe. It remains for historical reproduction and is not the current release builder.
- `repack_release_zip.py` performs a packaging-only rebuild from an already validated package directory. It removes cache and OS metadata entries, regenerates the internal manifest and SHA256SUMS, and writes ZIP hash/CRC sidecars without changing the source package.
- `validate_release_zip.py` verifies ZIP hygiene, CRC, internal manifest membership, every payload hash, embedded validation status, and the release-declared number of unique task IDs. It supports both the historical 60,078-row v0.1.1 package and the current 60,240-row v0.2.0 package.

No release utility may overwrite an existing release destination. A rebuilt archive becomes locally authoritative only after final validation passes and the project pointers are updated. That local promotion does not by itself authorize external/public distribution.
