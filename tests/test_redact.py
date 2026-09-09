"""Redaction unit tests. This is the code-level evidence for the MVP
acceptance criterion: 'a spot-check confirms no un-redacted secret was
transmitted off-fleet' (plan §6)."""
from app.ingest.redact import assert_clean, redact_text

SECRET_SAMPLES = [
    "aws_access_key_id=AKIAIOSFODNN7EXAMPLE",
    "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz0123456789",
    "github token ghp_1234567890abcdefghijklmnopqrstuvwxyz",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----",
    "password: SuperSecretP@ssw0rd123",
    "contact me at someone@example.com",
    "call me at 555-123-4567",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBogus1234567890abcdefghijklmnopqrstuvwx user@host",
]

BENIGN_SAMPLES = [
    "Editing plan.md in VS Code",
    "Slack #general channel open",
    "Terminal: git status shows 3 modified files",
    "Reading a PR titled 'add screen context ingest pipeline'",
]


def test_all_secret_samples_are_redacted():
    for sample in SECRET_SAMPLES:
        result = redact_text(sample)
        assert result.had_redactions, f"secret not redacted: {sample!r}"
        assert "REDACTED" in result.text


def test_benign_text_is_left_readable():
    for sample in BENIGN_SAMPLES:
        result = redact_text(sample)
        assert result.text == sample, f"benign text was altered: {sample!r} -> {result.text!r}"


def test_assert_clean_raises_on_secret():
    import pytest

    with pytest.raises(ValueError):
        assert_clean("api_key: sk_live_abcdefghijklmnopqrstuvwx")


def test_assert_clean_passes_on_redacted_text():
    redacted = redact_text("password: hunter2hunter2hunter2").text
    assert_clean(redacted)  # must not raise


def test_mixed_text_only_secret_span_is_redacted():
    sample = "Meeting notes: discuss roadmap. api_key=AbCdEfGhIjKlMnOpQrSt1234 for staging."
    result = redact_text(sample)
    assert "roadmap" in result.text
    assert "AbCdEfGhIjKlMnOpQrSt1234" not in result.text
