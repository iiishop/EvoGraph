from pathlib import Path
from evograph.application.api import Application
app=Application(Path('.evograph/review-v2'))
app.settings.save('openai_compatible', {'base_url':'http://127.0.0.1:9878/v1','model':'fixture'})
