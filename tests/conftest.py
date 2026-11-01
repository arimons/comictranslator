import pytest


@pytest.fixture(autouse=True)
def _reset_lama_provider():
    """create_app()은 서버의 LaMa 호출을 작업 프로세스로 돌린다. 그 등록이 다음 시험으로 새지 않게 되돌린다."""
    yield
    from pipeline import dialogue_extractor

    dialogue_extractor.set_lama_provider(None)
