import re

import google.generativeai as genai
from app.core.config import settings


MISSING_KEY_MESSAGE = "LLM_API_KEY is not configured in .env"
FENCED_BLOCK = re.compile(r"```[\w-]*[ \t]*\n?(.*?)```", re.DOTALL)


class LLMClient:
    def __init__(self):
        self.enabled = bool(settings.LLM_API_KEY)
        self._models = {}
        if self.enabled:
            genai.configure(api_key=settings.LLM_API_KEY)

    def _model(self, name: str):
        if not self.enabled:
            raise Exception(MISSING_KEY_MESSAGE)
        if name not in self._models:
            self._models[name] = genai.GenerativeModel(name)
        return self._models[name]

    def _complete(self, model_name: str, prompt: str) -> str:
        response = self._model(model_name).generate_content(prompt)
        return response.text.strip()

    def _strip_fences(self, raw_text: str) -> str:
        match = FENCED_BLOCK.search(raw_text)
        return (match.group(1) if match else raw_text).strip()

    def generate_sql(self, natural_query: str, schema_json_str: str, db_type: str) -> str:
        prompt = f"""
You are an expert SQL developer. Your task is to generate a valid {db_type} SQL query based on the user's natural language request.

Here is the database schema in JSON format (keys are table names, values are lists of columns):
{schema_json_str}

User Request:
{natural_query}

CRITICAL: Return ONLY the raw SQL query. Do not include markdown formatting like ```sql or ```. Do not include any explanations.
"""
        return self.generate(prompt)

    def generate(self, prompt: str) -> str:
        return self._strip_fences(self._complete(settings.MODEL_NAME, prompt))

    def summarize(self, prompt: str) -> str:
        return self._complete(settings.SUMMARY_MODEL_NAME, prompt)

    def extract(self, prompt: str) -> str:
        return self._complete(settings.EXTRACTION_MODEL_NAME, prompt)


llm = LLMClient()
