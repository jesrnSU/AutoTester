# AutoTester — LLM-assisted GUI testing

A research prototype from my bachelor's thesis, *A Contract-Based Framework for GUI Regression Testing of IP Desk Phones* (Jesper Nilsson, 2026).

The project explores how a large language model (LLM) can help automate testing across phones with different screens and buttons. It uses screenshots and extracted screen text to interpret the interface, choose actions, and check expected outcomes.

The core idea is **interaction contracts**: reusable operations such as placing or transferring a call. The LLM helps discover a device-specific button sequence, which can then be saved and replayed in later tests. This lets the same scenario work across different phone models while reducing repeated LLM usage.

The thesis evaluated the prototype on Poly E100, Poly E350, and Yealink T85 phones, covering calls, hold/resume, transfers, and conferences.

Explore [`scenarios/`](scenarios/) for example tests, [`contracts/`](contracts/) for reusable operations, and [`auto_tester/`](auto_tester/) for the implementation.

## Showcase archive

This source is shared for reading and reference, not supported execution. Private configurations, credentials, screenshots, recordings, and experiment results are excluded. External AI calls are disabled by default; enabling them sends phone data to Anthropic. Local configurations store passwords in plaintext. Do not use real credentials or confidential data.

No license grant is made by this archive.
