from chrome_tab_organizer.config import Settings


def test_settings_loads_bedrock_env(monkeypatch) -> None:
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "test-bedrock-token")

    settings = Settings.load()

    assert settings.provider == "bedrock"
    assert settings.aws_region == "us-west-2"
    assert settings.aws_bearer_token_bedrock == "test-bedrock-token"
    assert settings.bedrock_model_id == "anthropic.claude-sonnet-4-6"
