from unittest.mock import AsyncMock, MagicMock, patch

import litellm
import pytest

import pr_agent.algo.ai_handlers.litellm_ai_handler as litellm_handler
from pr_agent.algo.ai_handlers.litellm_ai_handler import LiteLLMAIHandler


def _make_settings():
    openai_key = "test-openai-key"
    anthropic_key = "test-anthropic-key"
    openai_api_base = "https://gateway.example/openai/v1"
    anthropic_api_base = "https://gateway.example/anthropic/v1"
    values = {
        "OPENAI.KEY": openai_key,
        "OPENAI.API_BASE": openai_api_base,
        "ANTHROPIC.KEY": anthropic_key,
        "ANTHROPIC.API_BASE": anthropic_api_base,
    }
    return type("Settings", (), {
        "config": type("Config", (), {
            "reasoning_effort": None,
            "ai_timeout": 30,
            "custom_reasoning_model": False,
            "verbosity_level": 0,
            "seed": -1,
            "get": lambda self, key, default=None: default,
        })(),
        "litellm": type("LiteLLM", (), {
            "get": lambda self, key, default=None: default,
        })(),
        "openai": type("OpenAI", (), {
            "key": openai_key,
            "api_base": openai_api_base,
        })(),
        "anthropic": type("Anthropic", (), {
            "key": anthropic_key,
            "api_base": anthropic_api_base,
        })(),
        "get": lambda self, key, default=None: values.get(key, default),
    })()


def _mock_response():
    response = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    mock = MagicMock()
    mock.__getitem__.side_effect = response.__getitem__
    mock.dict.return_value = response
    return mock


@pytest.mark.asyncio
async def test_provider_specific_api_bases(monkeypatch):
    monkeypatch.setattr(litellm_handler, "get_settings", _make_settings)
    monkeypatch.setattr(litellm, "api_key", None)
    monkeypatch.setattr(litellm, "api_base", None)
    monkeypatch.setattr(litellm, "openai_key", None)
    monkeypatch.setattr(litellm, "anthropic_key", None)

    with patch(
        "pr_agent.algo.ai_handlers.litellm_ai_handler.acompletion",
        new_callable=AsyncMock,
    ) as mock_completion:
        mock_completion.return_value = _mock_response()
        handler = LiteLLMAIHandler()

        await handler.chat_completion(
            model="anthropic/claude-sonnet-5",
            system="system",
            user="user",
        )
        assert mock_completion.call_args.kwargs["api_base"] == "https://gateway.example/anthropic/v1"

        await handler.chat_completion(
            model="gpt-4o",
            system="system",
            user="user",
        )
        assert mock_completion.call_args.kwargs["api_base"] == "https://gateway.example/openai/v1"
