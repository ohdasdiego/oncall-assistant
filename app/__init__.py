import os
import markdown as md
from markupsafe import Markup
from flask import Flask
from .models.database import init_db


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-in-prod")
    app.config["DATABASE_URL"] = os.getenv("DATABASE_URL", "sqlite:///oncall.db")

    @app.template_filter("markdownify")
    def markdownify(text):
        return Markup(md.markdown(text or "", extensions=["nl2br"]))

    init_db()

    from .routes.incidents import incidents_bp
    from .routes.webhooks import webhooks_bp
    from .routes.api import api_bp

    app.register_blueprint(incidents_bp)
    app.register_blueprint(webhooks_bp, url_prefix="/webhooks")
    app.register_blueprint(api_bp, url_prefix="/api")

    # Start auto-resolver background thread
    from .services import auto_resolver
    auto_resolver.start()

    return app
