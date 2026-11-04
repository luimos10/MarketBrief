from unittest.mock import MagicMock, patch
from modules.brief_generator import (
    generate_brief,
    generate_brief_groq,
    generate_brief_with_fallback,
)


def test_generate_brief_skips_thinking_for_flash_lite():
    mock_response = MagicMock()
    mock_response.text = "# Institutional Brief\nContenido exitoso"
    mock_response.candidates = []
    mock_response.usage_metadata = None

    mock_client = MagicMock()
    captured_config = []

    def mock_generate_content(*args, **kwargs):
        captured_config.append(kwargs.get("config"))
        return mock_response

    mock_client.models.generate_content.side_effect = mock_generate_content

    with patch("google.genai.Client", return_value=mock_client):
        result = generate_brief(
            system_prompt="system",
            user_prompt="user",
            api_key="fake-key",
            model="gemini-3.5-flash-lite",
            enable_search=False,
        )

    assert "Contenido exitoso" in result
    assert len(captured_config) == 1
    assert not getattr(captured_config[0], "thinking_config", None)


def test_generate_brief_recovers_from_thinking_config_invalid_argument():
    mock_response = MagicMock()
    mock_response.text = "# Institutional Brief\nContenido exitoso"
    mock_response.candidates = []
    mock_response.usage_metadata = None

    mock_client = MagicMock()
    call_count = 0

    def mock_generate_content(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        cfg = kwargs.get("config")
        if call_count == 1:
            assert getattr(cfg, "thinking_config", None) is not None
            raise Exception("400 INVALID_ARGUMENT: Request contains an invalid argument.")
        assert getattr(cfg, "thinking_config", None) is None
        return mock_response

    mock_client.models.generate_content.side_effect = mock_generate_content

    with patch("google.genai.Client", return_value=mock_client):
        result = generate_brief(
            system_prompt="system",
            user_prompt="user",
            api_key="fake-key",
            model="gemini-custom-flash",
            enable_search=False,
        )

    assert "Contenido exitoso" in result
    assert call_count == 2


def test_generate_brief_recovers_from_search_quota():
    mock_response = MagicMock()
    mock_response.text = "# Institutional Brief\nContenido exitoso sin search"
    mock_response.candidates = []
    mock_response.usage_metadata = None

    mock_client = MagicMock()
    call_count = 0
    search_exhausted_called = False

    def on_exhausted():
        nonlocal search_exhausted_called
        search_exhausted_called = True

    def mock_generate_content(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("429 RESOURCE_EXHAUSTED: You exceeded your current quota")
        return mock_response

    mock_client.models.generate_content.side_effect = mock_generate_content

    with patch("google.genai.Client", return_value=mock_client):
        result = generate_brief(
            system_prompt="system",
            user_prompt="user",
            api_key="fake-key",
            model="gemini-2.5-flash",
            enable_search=True,
            on_search_exhausted=on_exhausted,
        )

    assert "Contenido exitoso sin search" in result
    assert search_exhausted_called is True
    assert call_count == 2


def test_generate_brief_with_fallback_progression():
    def mock_generate(system_prompt, user_prompt, api_key, model, **kwargs):
        if model == "gemini-2.5-flash":
            return "ERROR: 503 UNAVAILABLE"
        return f"# OK from {model}"

    with patch("modules.brief_generator.generate_brief", side_effect=mock_generate):
        text, provider, used_model = generate_brief_with_fallback(
            system_prompt="sys",
            user_prompt="user",
            gemini_api_key="fake",
            gemini_model="gemini-2.5-flash",
            gemini_fallback_models=["gemini-3.5-flash"],
        )

    assert provider == "gemini"
    assert used_model == "gemini-3.5-flash"
    assert "OK from gemini-3.5-flash" in text


def test_generate_brief_groq_success():
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "choices": [
            {
                "message": {"content": "# Brief from Groq Llama 3.3"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600},
    }

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = fake_resp

    with patch("httpx.Client", return_value=mock_client):
        res = generate_brief_groq(
            system_prompt="sys",
            user_prompt="user",
            api_key="gsk-fake-key",
            model="llama-3.3-70b-versatile",
        )

    assert "Brief from Groq Llama 3.3" in res


def test_generate_brief_with_groq_multi_provider_fallback():
    # Toda la cadena Gemini falla, Groq toma el relevo automáticamente
    with patch("modules.brief_generator.generate_brief", return_value="ERROR: 503 UNAVAILABLE"):
        with patch("modules.brief_generator.generate_brief_groq", return_value="# Fallback Groq OK"):
            text, provider, used_model = generate_brief_with_fallback(
                system_prompt="sys",
                user_prompt="user",
                gemini_api_key="fake-gemini",
                gemini_model="gemini-2.5-flash",
                gemini_fallback_models=["gemini-3.5-flash"],
                groq_api_key="gsk-fake-groq",
                groq_model="llama-3.3-70b-versatile",
                max_chain_passes=1,
            )

    assert provider == "groq"
    assert used_model == "llama-3.3-70b-versatile"
    assert text == "# Fallback Groq OK"


def test_generate_brief_forced_groq():
    with patch("modules.brief_generator.generate_brief_groq", return_value="# Forced Groq OK"):
        text, provider, used_model = generate_brief_with_fallback(
            system_prompt="sys",
            user_prompt="user",
            groq_api_key="gsk-fake",
            groq_model="llama-3.3-70b-versatile",
            forced_provider="groq",
        )

    assert provider == "groq"
    assert used_model == "llama-3.3-70b-versatile"
    assert text == "# Forced Groq OK"
