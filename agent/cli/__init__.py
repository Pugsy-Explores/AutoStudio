"""CLI entry points."""


def run_agent_main() -> None:
    """Run the agent CLI. Lazy import avoids RuntimeWarning when executing agent.cli.run_agent as __main__."""
    from agent.cli.run_agent import main
    main()


__all__ = ["run_agent_main"]
