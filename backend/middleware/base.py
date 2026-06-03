from abc import ABC, abstractmethod
from typing import Any, Callable

from langchain_core.messages import BaseMessage


class AgentMiddleware(ABC):
    """Abstract middleware with 6 lifecycle hooks for the marketing agent.

    Hook execution order:
      1. before_agent    — validate request, load user data
      2. before_model    — trim context, inject RAG, modify prompt
      3. wrap_model_call — dynamic model selection, request interception
      4. wrap_tool_call  — authorisation, error handling, audit log
      5. after_model     — output validation, content filtering
      6. after_agent     — save analytics, cleanup, persist
    """

    @abstractmethod
    def before_agent(self, state: dict, config: dict) -> dict:
        """Hook 1 — Before agent starts running.

        Responsibilities:
          - Validate request and user permissions
          - Load user profile and brand rules from LTM
          - Hydrate state with long-term memory context
          - Initialise analytics timer
        """

    @abstractmethod
    def before_model(
        self,
        messages: list[BaseMessage],
        state: dict,
    ) -> list[BaseMessage]:
        """Hook 2 — Before each LLM call.

        Responsibilities:
          - Adaptive compaction (check token pressure, trigger if needed)
          - Inject RAG context (product catalog, memes) into system prompt
          - Inject relevant LTM memories into system prompt
          - Trim message history to fit context window
        """

    @abstractmethod
    def wrap_model_call(
        self,
        invoke_func: Callable[[list[BaseMessage]], Any],
        messages: list[BaseMessage],
        state: dict,
    ) -> Any:
        """Hook 3 — Around LLM call.

        Responsibilities:
          - Dynamic model selection per task (research / copywriter / publish)
          - Log request/response metadata (tokens, latency)
          - Retry with fallback model on failure
          - Capture usage metadata
        """

    @abstractmethod
    def wrap_tool_call(
        self,
        tool_func: Callable[..., Any],
        tool_name: str,
        args: dict,
        state: dict,
    ) -> Any:
        """Hook 4 — Around tool execution.

        Responsibilities:
          - Authorisation check (can this user call this tool?)
          - Input/output audit logging
          - Error handling and graceful degradation
          - Usage quota checks
        """

    @abstractmethod
    def after_model(self, response: Any, state: dict) -> Any:
        """Hook 5 — After LLM response received.

        Responsibilities:
          - Validate output language (must contain German: äöüß)
          - Validate minimum hashtags (≥2)
          - Validate length constraints (≤2200 chars for Instagram)
          - Content filtering (toxicity, brand safety)
        """

    @abstractmethod
    def after_agent(self, state: dict, config: dict) -> dict:
        """Hook 6 — After agent completes (approved + published or rejected).

        Responsibilities:
          - Pre-compaction memory flush (extract facts before cleanup)
          - Save analytics events to LTM
          - Rebuild BM25 index if new memories added
          - Cleanup temporary state fields
          - Return sanitised state for API response
        """
