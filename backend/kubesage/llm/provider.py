"""Chat model factory. The Copilot only uses `bind_tools(...).invoke` and `with_structured_output(...).invoke`,
so any LangChain chat model (or the scripted model in `replay.py`) can stand in."""

from kubesage.config import Settings


class LlmNotConfigured(RuntimeError):
    pass


def create_chat_model(settings: Settings):
    provider = settings.llm_provider
    if provider == "google_genai":
        if settings.google_api_key is None:
            raise LlmNotConfigured("GOOGLE_API_KEY is not set (add it to .env)")
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=settings.llm_model,
            google_api_key=settings.google_api_key,
            temperature=0,
            thinking_budget=settings.llm_thinking_budget,
            max_retries=settings.llm_max_retries,
            timeout=settings.llm_timeout_s,
        )
    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:
            raise LlmNotConfigured("pip install langchain-anthropic to use the anthropic provider") from exc
        return ChatAnthropic(model=settings.llm_model, temperature=0, max_retries=settings.llm_max_retries)
    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise LlmNotConfigured("pip install langchain-openai to use the openai provider") from exc
        return ChatOpenAI(model=settings.llm_model, temperature=0, max_retries=settings.llm_max_retries)
    raise LlmNotConfigured(f"unknown LLM provider {provider!r}")
