from harness.runtime.hooks import SdkDiagnosticTail, redact_sdk_stderr


def test_sdk_diagnostic_tail_is_bounded_and_redacts_credentials() -> None:
    tail = SdkDiagnosticTail()
    tail("provider stream closed unexpectedly")
    tail("Authorization: Bearer private-value")

    assert tail.summary() == "provider stream closed unexpectedly | [redacted sdk diagnostic]"
    assert "private-value" not in tail.summary()
    assert redact_sdk_stderr("request token=private-value") == "[redacted sdk diagnostic]"
