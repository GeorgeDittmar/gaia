from openai import AsyncOpenAI
from pydantic_ai.agent import Agent
from pydantic_ai_harness import Coder
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

class Gaia:
    """Harness for Gaia agent a general purpose personal assistant"""
    def __init__(self, model_name: str = ""):
        # todo use a model router of some sort
        # Create an OpenAI-compatible client pointing to llama-server

        # Use OpenAIModel with your alias name
        model = OpenAIChatModel('local-model', provider=OpenAIProvider(
        base_url='http://localhost:8080/v1',
        api_key='not-needed',  # llama.cpp does not require a real API key
    ),)

        self.__core_agent = Agent(model)

    async def ainteract(self, prompt: str ):
        """Single turn interaction with user or system"""
            # Run the agent in stream mode using an async context manager
        async with self.__core_agent.run_stream(prompt) as result:
            # Iterate over text chunks as they arrive from the model
            async for chunk in result.stream_text(delta=True):
                yield chunk
