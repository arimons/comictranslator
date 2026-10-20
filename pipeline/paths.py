"""저장소와 작업 데이터 위치의 단일 정의. 디렉터리는 사용하는 시점에 생성한다."""
from dataclasses import dataclass
import os
from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")
WEB_DIR = REPO_ROOT / "web"
FONTS_DIR = REPO_ROOT / "Fonts"

@dataclass(frozen=True)
class WorkspacePaths:
    home: Path

    @property
    def projects(self): return self.home / "projects"
    @property
    def input_cache(self): return self.home / "cache/input"
    @property
    def cv_cache(self): return self.home / "cache/cv"
    @property
    def analysis_cache(self): return self.home / "cache/analysis"
    @property
    def glossaries(self): return self.home / "glossaries"
    @property
    def image_generation_inbox(self): return self.home / "inbox/image_generation"
    @property
    def exports(self): return self.home / "exports"
    @property
    def legacy(self): return self.home / "legacy"


def for_repo(root=REPO_ROOT):
    root = Path(root).resolve()
    default = Path.home() / "ComicTranslator" if root == REPO_ROOT else root / "data"
    home = os.environ.get("COMIC_TRANSLATOR_HOME", default) if root == REPO_ROOT else default
    return WorkspacePaths(Path(home).expanduser().absolute())

PATHS = for_repo()
DATA_HOME = PATHS.home
PROJECTS_DIR = PATHS.projects
INPUT_CACHE_DIR = PATHS.input_cache
CV_CACHE_DIR = PATHS.cv_cache
ANALYSIS_CACHE_DIR = PATHS.analysis_cache
GLOSSARY_DIR = PATHS.glossaries
IMAGE_GENERATION_INBOX = PATHS.image_generation_inbox
EXPORTS_DIR = PATHS.exports
LEGACY_DIR = PATHS.legacy


def export_directory(home=None):
    """config.json의 사용자 지정 내보내기 위치. 상대 경로는 데이터 홈 기준."""
    import json
    base = Path(home or DATA_HOME)
    try:
        configured = json.loads((base / "config.json").read_text()).get("exports_dir", "exports")
    except (OSError, ValueError, AttributeError):
        configured = "exports"
    path = Path(configured).expanduser()
    return path if path.is_absolute() else base / path
