from desktop_agent.common.dpi import ensure_dpi_aware

# Must run before UIA / screenshot / window APIs touch the desktop.
ensure_dpi_aware()

from desktop_agent.cli.main import app

if __name__ == "__main__":
    app()
