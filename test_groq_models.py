from groq import Groq
from config import config

client = Groq(api_key=config.GROQ_API_KEY)

candidate_models = [
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it"
]

print("Testing Groq models with user API key...")
working_model = None

for m in candidate_models:
    try:
        res = client.chat.completions.create(
            model=m,
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=10
        )
        print(f"✅ MODEL WORKS: {m}")
        if not working_model:
            working_model = m
    except Exception as e:
        print(f"❌ MODEL FAILED: {m} -> {e}")

print(f"\nRecommended working model: {working_model}")
