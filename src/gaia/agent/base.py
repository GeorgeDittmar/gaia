from pydantic_ai.agent import Agent
from pydantic_ai_harness import Coder,


class Gaia:
    """Harness for Gaia agent a general purpose personal assistant"""
    def __init__(self):
        # todo use a model router of some sort
        self.__core_agent = Agent(model="openai-chat:gpt-5.4-nano")

    def interact(self, inp: str ):
        """Single turn interaction with user or system"""
