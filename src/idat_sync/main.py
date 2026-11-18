"""Entrypoint module for the idat_sync GUI application."""

from platformdirs import user_data_path

from idat_sync.bootstrap import configure_logging
from idat_sync.config import load_config
from idat_sync.ui.app import MainApp


def main() -> None:
    """Load configuration, initialize logging, and run the desktop app."""
    config = load_config(user_data_path("idat-sync", appauthor=False))
    configure_logging(config.log_file)
    app = MainApp(config=config)
    app.run()


if __name__ == "__main__":
    main()
