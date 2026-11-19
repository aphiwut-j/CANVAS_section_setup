from __future__ import annotations

import os

from app import create_app

application = create_app()

if __name__ == "__main__":
    application.run(
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8010")),
        debug=False,
    )

