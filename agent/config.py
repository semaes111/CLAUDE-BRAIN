"""
Configuración centralizada de CLAUDE-BRAIN.

Todas las variables de entorno se leen aquí una sola vez.
Los módulos importan `settings` en lugar de llamar a os.getenv() directamente.
"""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    # ── Redis ────────────────────────────────────
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379")

    # ── Supabase ─────────────────────────────────
    supabase_url: str = os.getenv("SUPABASE_URL", "http://supabase-kong:8000")
    supabase_service_key: str = os.getenv("SUPABASE_SERVICE_KEY", "")
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "")

    # ── Claude CLI ───────────────────────────────
    agent_timeout_seconds: int = int(os.getenv("AGENT_TIMEOUT_SECONDS", "300"))
    agent_max_subagents: int = int(os.getenv("AGENT_MAX_SUBAGENTS", "4"))
    claude_config_dir: str = os.getenv(
        "CLAUDE_CONFIG_DIR",
        os.path.join(os.getenv("HOME", "/root"), ".claude"),
    )

    # ── Jupyter ──────────────────────────────────
    jupyter_url: str = os.getenv("JUPYTER_URL", "http://jupyter:8888")
    jupyter_token: str = os.getenv("JUPYTER_TOKEN", "claude-brain-jupyter-token")

    # ── Embeddings ───────────────────────────────
    embedding_url: str = os.getenv("EMBEDDING_URL", "http://embeddings:8080")

    # ── GitHub ───────────────────────────────────
    github_token: str = os.getenv("GITHUB_TOKEN", "")

    # ── Sandbox ──────────────────────────────────
    sandbox_url: str = os.getenv("SANDBOX_URL", "http://sandbox-api:8080")

    # ── Telegram ─────────────────────────────────
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_allowed_ids: str = os.getenv("TELEGRAM_ALLOWED_IDS", "")

    # ── Anthropic (solo para mem0 extractor) ─────
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    # ── Paths ────────────────────────────────────
    workdir: str = "/workspaces"
    components_agents_dir: str = "/app/components/agents"
    components_skills_dir: str = "/app/components/skills"
    components_commands_dir: str = "/app/components/commands"
    project_skills_dir: str = "/app/skills"

    # ── Agent Loop ───────────────────────────────
    default_max_iterations: int = 30
    context_condenser_max_chars: int = 80_000
    context_condenser_keep_steps: int = 4
    stuck_detector_window: int = 6
    kernel_ttl_seconds: int = 1800


settings = Settings()
