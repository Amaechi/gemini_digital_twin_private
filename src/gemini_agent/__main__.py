"""Allow ``python -m gemini_agent`` as an alias for ``python -m gemini_agent.cli``."""

from gemini_agent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
