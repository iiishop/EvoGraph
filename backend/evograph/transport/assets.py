import mimetypes


def configure_asset_types():
    """Windows registry overrides may incorrectly classify JS as text/plain."""
    mimetypes.init()
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/javascript", ".mjs")
    mimetypes.add_type("text/css", ".css")
