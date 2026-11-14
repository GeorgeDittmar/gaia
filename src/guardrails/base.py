from dataclasses import dataclass
from typing import Optional

from sklearn.neighbors import KNeighborsClassifier, KNeighborsTransformer
from sentence_transformers import SentenceTransformer


@dataclass
class Safe:
    prediction: bool
    confidence: float
    reasoning: Optional[str]


class AgentGuard:
    """
    Class to make sure any tool use or actions by an agent are not inherently dangerous, or out of the norm of what the
    agent is allowed or tasked with doing.
    """

    def __init__(
        self,
        conf_path="~.gaia/"
    ):
        pass

    def command_check(self, command):
        pass
