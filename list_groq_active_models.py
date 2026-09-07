from groq import Groq
from config import config

client = Groq(api_key=config.GROQ_API_KEY)
models = client.models.list()
print("Active Groq Models:")
for m in models.data:
    print(f"- {m.id}")
