"""
Dependencias compartidas de la API — instancias singleton.

Todos los routers importan desde aquí para evitar instancias duplicadas.
"""

import os

from agent.config import settings
from agent.core.claude_runner import ClaudeMaxRunner
from agent.core.router import SmartRouter
from agent.core.runtime_executor import RuntimeExecutor
from agent.core.watcher import Watcher
from agent.memory.mem0_manager import Mem0Manager
from agent.orchestrator.multi_agent import MultiAgentOrchestrator
from agent.registry.component_registry import ComponentRegistry

runner       = ClaudeMaxRunner()
registry     = ComponentRegistry()
router_ai    = SmartRouter(runner=runner, registry=registry)
watcher      = Watcher()
memory       = Mem0Manager()
orchestrator = MultiAgentOrchestrator(
    runner=runner,
    max_concurrent=settings.agent_max_subagents,
)
runtime      = RuntimeExecutor(
    base_dir=settings.workdir,
    enable_browser=True,
    runner=runner,
)
