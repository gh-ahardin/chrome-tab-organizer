from chrome_tab_organizer.config import Settings


def test_settings_loads_bedrock_env(monkeypatch) -> None:
    monkeypatch.setenv("CTO_PROVIDER", "bedrock")
    monkeypatch.setenv("CTO_AWS_REGION", "us-west-2")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "test-bedrock-token")
    monkeypatch.setenv("CTO_BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0")

    settings = Settings.load()

    assert settings.provider == "bedrock"
    assert settings.aws_region == "us-west-2"
    assert settings.aws_bearer_token_bedrock == "test-bedrock-token"
    assert settings.bedrock_model_id == "anthropic.claude-3-5-sonnet-20241022-v2:0"
