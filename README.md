# Thesis research archive

Source archive for research into interaction contracts for GUI regression testing across IP desk phones. This repository is retained for reading and reference, and is not maintained or supported for execution.

Device credentials and configurations, learned device implementations, phone screenshots, recordings, experiment results, compiled Python files, and local editor/assistant metadata have been excluded. Contact names in scenarios are synthetic placeholders. Local machine paths have been replaced with generic paths.

This is a showcase archive, not a runnable or supported product. Security hardening enables TLS certificate verification, keeps credentials out of request URLs, restricts generated configuration and contract paths, and saves device configuration files with owner-only permissions on Unix. Device passwords are still stored in plaintext locally; do not use real credentials.

External AI calls are disabled by default. Explicitly setting `AUTOTESTER_ENABLE_EXTERNAL_AI=1` enables calls that send phone screenshots, OCR text, and device context to Anthropic, using `ANTHROPIC_API_KEY`. Request logging omits prompt contents, but model responses and other runtime output can still include phone data. Do not run this archive against real devices or confidential data. No dependency manifest is provided and dependencies have not been audited.

Source ownership and permission to publish must be established separately. No license grant is made by this archive.
