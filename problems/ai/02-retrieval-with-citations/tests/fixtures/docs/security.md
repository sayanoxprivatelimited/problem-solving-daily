# Security and API keys

Every workspace has API keys. An API key is scoped to a single workspace and an
explicit set of permissions.

## Rotating an API key

To rotate an API key, open Settings, go to the API keys tab, and press Rotate. The
old key keeps working for 24 hours so you can deploy the new one without downtime.
Rotate an API key immediately if it was committed to a public repository.

## Revoking an API key

Revocation is instant and cannot be undone. A revoked API key returns HTTP 401 for
every request. Revoke keys that are no longer used, and prefer one API key per
service so revocation is cheap.

## Encryption

Data is encrypted at rest with AES-256 and in transit with TLS 1.3. Encryption keys
are rotated every 90 days by the platform, not by you.
