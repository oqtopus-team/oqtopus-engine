import pytest

from oqtopus_engine_core.framework.engine import Engine


class DummyPipeline:
    def __init__(self):
        self.started = False

    async def start(self):
        self.started = True


class DummyJobFetcher:
    def __init__(self):
        self.started = False
        self.gctx = None
        self.pipeline = None

    async def start(self):
        self.started = True


class DummyDeviceFetcher:
    def __init__(self):
        self.started = False
        self.gctx = None

    async def start(self):
        self.started = True


class DummyRepository:
    pass


class DummyStorage:
    pass


class DummyDiContainer:
    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, name):
        return self.mapping[name]

class DummyGlobalContext:
    def __init__(self):
        self.job_repository = None
        self.device_repository = None


@pytest.mark.asyncio
async def test_engine_start_initializes_components():
    """
    Engine.start should:

    - inject gctx into job_fetcher and device_fetcher
    - inject pipeline into job_fetcher
    - set job_repository, device_repository and job_storage in gctx
    - start pipeline, job_fetcher, and device_fetcher
    """

    pipeline = DummyPipeline()
    job_fetcher = DummyJobFetcher()
    device_fetcher = DummyDeviceFetcher()

    job_repo = DummyRepository()
    device_repo = DummyRepository()

    job_storage = DummyStorage()

    dicon = DummyDiContainer(
        {
            "job_fetcher": job_fetcher,
            "job_repository": job_repo,
            "device_fetcher": device_fetcher,
            "device_repository": device_repo,
            "job_storage": job_storage,
        }
    )

    engine = Engine.__new__(Engine)

    gctx = DummyGlobalContext()
    gctx.config = {}

    engine._gctx = gctx
    engine._dicon = dicon
    engine._pipeline = pipeline

    await engine.start()

    assert job_fetcher.gctx is gctx
    assert job_fetcher.pipeline is pipeline
    assert device_fetcher.gctx is gctx

    assert gctx.job_repository is job_repo
    assert gctx.device_repository is device_repo

    assert gctx.job_storage is job_storage

    assert pipeline.started
    assert job_fetcher.started
    assert device_fetcher.started


@pytest.mark.asyncio
async def test_run_components_runs_all():
    """
    _run_components should start all components concurrently.
    """

    class Dummy:
        def __init__(self):
            self.started = False

        async def start(self):
            self.started = True

    a = Dummy()
    b = Dummy()

    engine = Engine.__new__(Engine)

    await engine._run_components([a, b])

    assert a.started
    assert b.started
