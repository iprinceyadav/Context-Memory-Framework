import google.generativeai as genai
from app.core.config import settings


class LLMClient:
    def __init__(self):
        if settings.LLM_API_KEY:
            genai.configure(api_key=settings.LLM_API_KEY)
            self.model = genai.GenerativeModel(settings.MODEL_NAME)
        else:
            self.model = None

    def _strip_fences(self, raw_text: str) -> str:
        sql = raw_text.strip()
        if sql.startswith("```sql"):
            sql = sql[6:]
        elif sql.startswith("```"):
            sql = sql[3:]
        if sql.endswith("```"):
            sql = sql[:-3]
        return sql.strip()

    def generate_sql(self, natural_query: str, schema_json_str: str, db_type: str) -> str:
        if not self.model:
            raise Exception("LLM_API_KEY is not configured in .env")

        prompt = f"""
You are an expert SQL developer. Your task is to generate a valid {db_type} SQL query based on the user's natural language request.

Here is the database schema in JSON format (keys are table names, values are lists of columns):
{schema_json_str}

User Request:
{natural_query}

CRITICAL: Return ONLY the raw SQL query. Do not include markdown formatting like ```sql or ```. Do not include any explanations.
"""
        response = self.model.generate_content(prompt)
        return self._strip_fences(response.text)

    def generate(self, prompt: str) -> str:
        if not self.model:
            raise Exception("LLM_API_KEY is not configured in .env")
        response = self.model.generate_content(prompt)
        return self._strip_fences(response.text)

    def summarize(self, prompt: str) -> str:
        if not self.model:
            raise Exception("LLM_API_KEY is not configured in .env")
        response = self.model.generate_content(prompt)
        return response.text.strip()


llm = LLMClient()
