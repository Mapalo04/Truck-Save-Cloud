# Security policy

## Status
This is a prototype, **not audited** or validated for production use. Do not depend on it as the only copy of your ETS2 progress. Test restore with disposable profiles first.

## Reporting vulnerabilities
Please report security problems privately to the repository maintainer using GitHub's **Report a vulnerability** / private vulnerability reporting feature if enabled. Do not publish credentials, access tokens, private save files, or exploitation details in public issues. Maintainers should configure a private reporting channel before public release.

## Credentials and permissions
- Use Google OAuth Desktop Application authorization, never ask users for their Google password.
- Use the narrow Google Drive `drive.file` scope; don't expand permissions without a documented reason.
- `credentials.json` contains an OAuth client configuration; desktop clients cannot keep client secrets confidential. Do not put user tokens or API keys into source control.
- The prototype stores OAuth user tokens via the OS keyring. Review logs and crash reports for sensitive data before sharing.
- Revoke access in your Google Account if a computer is compromised.

## Backups and restores
- ZIP backups are compressed, **not encrypted**. Google Drive and anyone with access to the backup can read their contents.
- Avoid extracting archives from untrusted sources. Restore only known backups and keep the original profile copy.
- Restore only while ETS2 and Steam synchronization are stopped; verify restored saves before deleting safety copies.
- Cloud deletion is destructive. Test retention on a disposable account/profile and retain an independent backup.
- Archive SHA-256 checksums detect corruption; they are not proof of origin or authenticity.

## Maintainer checklist
- Add automated tests for malicious ZIP paths, symlinks, oversized archives, and interrupted writes.
- Validate OAuth token refresh and account disconnect, upload idempotency, cross-device reconciliation, and delete semantics.
- Add code review, dependency auditing, and release signing before distributing an installer.
- Never commit `credentials.json`, user save archives, databases, or access tokens.
