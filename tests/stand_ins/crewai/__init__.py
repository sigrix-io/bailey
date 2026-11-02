"""A stand-in for CrewAI, for the tests: what ``sigrix_runtime`` builds a crew from, and no more.

The real runner runs it: ``sigrix_runtime.loader`` builds the crew from the app's
``config/``, the execution layer kicks it off and reports each finished task as a
Postern ``step``, and the runner streams those to the page. Only CrewAI is not here,
because it brings 148 packages; each task goes to the provider the runner's environment
names, with the key found there, as CrewAI's own clients do. ``tests/test_apps_real.py``
runs the real CrewAI where PyPI can be reached.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any


class Process:
    sequential = "sequential"
    hierarchical = "hierarchical"


class LLM:
    """The model the crew runs on, read from the environment as CrewAI reads it."""

    def __init__(self) -> None:
        self.model = os.environ.get("MODEL") or os.environ.get("OPENAI_MODEL_NAME") or "gpt-4o-mini"


class Agent:
    def __init__(self, role: str, goal: str, backstory: str, **_: Any) -> None:
        self.role, self.goal, self.backstory = role, goal, backstory
        self.llm = LLM()
        self.prompt_tokens = 0
        self.completion_tokens = 0


class Task:
    def __init__(
        self,
        name: str = "",
        description: str = "",
        expected_output: str = "",
        agent: Agent | None = None,
        context: list[Task] | None = None,
        **_: Any,
    ) -> None:
        self.name, self.description, self.expected_output = name, description, expected_output
        self.agent = agent
        self.context = list(context or [])


class TaskOutput:
    def __init__(self, name: str, description: str, raw: str) -> None:
        self.name, self.description, self.raw = name, description, raw


class UsageMetrics:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens, self.completion_tokens = prompt_tokens, completion_tokens


class CrewOutput:
    def __init__(self, raw: str, token_usage: UsageMetrics) -> None:
        self.raw, self.token_usage = raw, token_usage

    def __str__(self) -> str:
        return self.raw


class Crew:
    def __init__(self, agents: list[Agent], tasks: list[Task], **_: Any) -> None:
        self.agents, self.tasks = list(agents), list(tasks)
        self.task_callback: Any = None

    def calculate_usage_metrics(self) -> UsageMetrics:
        return UsageMetrics(
            sum(agent.prompt_tokens for agent in self.agents), sum(agent.completion_tokens for agent in self.agents)
        )

    def kickoff(self, inputs: dict[str, Any]) -> CrewOutput:
        done: dict[str, str] = {}
        last = ""
        for task in self.tasks:
            agent = task.agent or self.agents[0]
            description = task.description.replace("{prompt}", str(inputs.get("prompt", ""))).replace(
                "{configuration}", str(inputs.get("configuration", ""))
            )
            context = "\n\n".join(done[earlier.name] for earlier in task.context if earlier.name in done)
            time.sleep(float(os.environ.get("STAND_IN_TASK_SECONDS") or 0))
            last = _complete(agent, description, task.expected_output, context)
            done[task.name] = last
            if self.task_callback is not None:
                self.task_callback(TaskOutput(task.name, task.description, last))
        return CrewOutput(last, self.calculate_usage_metrics())


def _post(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310 - the provider the environment names
        url, data=json.dumps(body).encode(), headers={**headers, "content-type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        answer: dict[str, Any] = json.loads(response.read())
    return answer


def _complete(agent: Agent, description: str, expected: str, context: str) -> str:
    system = f"You are {agent.role}. {agent.backstory}\nYour personal goal is: {agent.goal}"
    user = f"Current Task: {description}\n\nThis is the expected criteria for your final answer: {expected}"
    if context:
        user += f"\n\nThis is the context you're working with:\n{context}"
    model = agent.llm.model
    if model.startswith("anthropic/"):
        base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
        reply = _post(
            f"{base}/v1/messages",
            {
                "model": model.split("/", 1)[1],
                "max_tokens": 1024,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            {"x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01"},
        )
        text = "".join(block.get("text", "") for block in reply.get("content", []) if block.get("type") == "text")
        used = reply.get("usage") or {}
        agent.prompt_tokens += int(used.get("input_tokens") or 0)
        agent.completion_tokens += int(used.get("output_tokens") or 0)
        return text
    base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    reply = _post(
        f"{base}/chat/completions",
        {
            "model": model.split("/", 1)[1] if model.startswith("openai/") else model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        {"authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"},
    )
    used = reply.get("usage") or {}
    agent.prompt_tokens += int(used.get("prompt_tokens") or 0)
    agent.completion_tokens += int(used.get("completion_tokens") or 0)
    return str(reply["choices"][0]["message"].get("content") or "")
