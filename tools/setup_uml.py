"""Install a pinned local renderer. No project source is uploaded."""

import hashlib
from pathlib import Path
from urllib.request import urlretrieve

VERSION = "1.2026.2"
BASE = f"https://repo.maven.apache.org/maven2/net/sourceforge/plantuml/plantuml/{VERSION}/plantuml-{VERSION}.jar"
target = Path(__file__).resolve().parents[1] / ".tools" / "plantuml.jar"
target.parent.mkdir(exist_ok=True)
temporary = target.with_suffix(".download")
urlretrieve(BASE, temporary)
checksum = target.with_suffix(".sha256")
urlretrieve(BASE + ".sha256", checksum)
if hashlib.sha256(temporary.read_bytes()).hexdigest() != checksum.read_text().strip():
    raise SystemExit("PlantUML 下载校验失败")
temporary.replace(target)
print(f"PlantUML {VERSION} ready: {target}. Java must be installed.")
