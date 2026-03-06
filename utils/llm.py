import os
from crewai import LLM
from dotenv import load_dotenv

load_dotenv(override=True)

def get_groq_llm():
    """
    Returns a configured Groq LLM instance (Llama 3) for zero-cost agent reasoning,
    using CrewAI's native LLM wrapper.
    """
    api_key = os.getenv("GROQ_API_KEY")
    base_url = os.getenv("GROQ_BASE_URL")
    model_name = os.getenv("GROQ_MODEL", "groq/llama3-70b-8192")

    if not api_key or api_key == "your_groq_api_key_here":
        raise ValueError("GROQ_API_KEY is missing or invalid in .env")

    return LLM(
        model=model_name,
        temperature=0.7,
        api_key=api_key,
        base_url=base_url
    )
