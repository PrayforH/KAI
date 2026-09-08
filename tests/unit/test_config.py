import pytest

from harness.config import Settings


def test_local_defaults_disable_external_model_and_otel() -> None:
    settings = Settings()

    assert settings.environment == "local"
    assert settings.runtime == "fake"
    assert settings.sandbox_execution_mode == "remote_cli"
    assert settings.worker_deferred_max_active_runs == 2
    assert settings.otel_enabled is False
    assert settings.otel_content_capture == "off"
    assert settings.otel_content_max_chars == 12_000
    assert settings.cc_switch_settings_path == "~/.claude/settings.json"
    assert settings.codex_cli_path == "/usr/local/bin/codex"
    assert settings.codex_approval_policy == "untrusted"
    assert settings.codex_tool_output_token_limit == 32_000
    assert settings.new_api_compatibility == "full"
    assert settings.new_api_capabilities == "streaming,tool_use"
    assert settings.daytona_delete_on_destroy is True
    assert settings.daytona_auto_stop_interval_minutes == 15
    assert settings.daytona_auto_delete_interval_minutes == 60
    assert settings.daytona_session_reuse_enabled is True
    assert settings.daytona_session_idle_timeout_seconds == 600
    assert settings.daytona_warm_pool_max_sessions == 3
    assert settings.e2b_template == "base"
    assert settings.e2b_timeout_seconds == 3600
    assert settings.e2b_allow_internet_access is True
    assert settings.run_reservation_ttl_seconds == 86_400
    assert settings.preflight_timeout_seconds == 180
    assert settings.new_api_auth_scheme == "bearer"
    assert settings.glm_5_2_model == "shdata-glm"
    assert settings.glm_5_2_auth_scheme == "bearer"
    assert settings.glm_5_2_capabilities == "streaming,tool_use"


def test_observability_content_capture_rejects_implicit_or_unbounded_modes() -> None:
    with pytest.raises(ValueError):
        Settings(otel_content_capture="raw")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Settings(otel_content_max_chars=128)
    with pytest.raises(ValueError):
        Settings(sandbox_execution_mode="implicit")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Settings(worker_deferred_max_active_runs=0)
    with pytest.raises(ValueError):
        Settings(codex_tool_output_token_limit=999)


def test_daytona_cleanup_intervals_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Settings(daytona_auto_stop_interval_minutes=0)
    with pytest.raises(ValueError):
        Settings(daytona_auto_delete_interval_minutes=0)
    with pytest.raises(ValueError):
        Settings(daytona_session_idle_timeout_seconds=0)
    with pytest.raises(ValueError):
        Settings(daytona_warm_pool_max_sessions=0)
    with pytest.raises(ValueError):
        Settings(e2b_timeout_seconds=0)
