import asyncio
import logging
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from einvoice_reader import __version__
from einvoice_reader.errors import ReadError, ServiceBusy
from einvoice_reader.isolation import WorkerPool
from einvoice_reader.model import Result
from einvoice_reader.schematron import SchematronRunner
from einvoice_reader.service import read_invoice
from einvoice_reader.settings import Settings
from einvoice_reader.validation import kosit_version

logger = logging.getLogger("einvoice_reader")


UPLOADS_PER_SLOT = 4


class TooLarge(Exception):
    pass


class ReadTimeout(Exception):
    pass


class Unauthorized(Exception):
    pass


class BadRequest(Exception):
    pass


def _error(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}, **extra}
    )


async def _read_body(request: Request, limit: int) -> bytes:
    if int(request.headers.get("content-length") or 0) > limit:
        raise TooLarge
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise TooLarge
    return bytes(body)


async def _file_bytes(request: Request, body: bytes) -> bytes:
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        return body

    async def stream():
        yield body

    try:
        form = await MultiPartParser(request.headers, stream()).parse()
    except MultiPartException as e:
        raise BadRequest(str(e)) from e
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise ReadError("EMPTY_FILE", "No file in form field 'file'")
        return await upload.read()
    finally:
        await form.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runner = SchematronRunner(timeout_s=settings.validation_timeout_s)
        app.state.parser = WorkerPool(
            "Parser",
            settings.parse_workers,
            settings.parse_timeout_s,
            memory_mb=settings.parse_memory_mb,
        )
        app.state.slots = asyncio.Semaphore(settings.max_concurrent_requests)
        app.state.uploads = asyncio.Semaphore(settings.max_concurrent_requests * UPLOADS_PER_SLOT)
        yield
        app.state.parser.close()
        app.state.runner.close()

    docs = settings.docs_enabled
    app = FastAPI(
        title="einvoice-reader",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        logger.info(
            "%s %s %s %.0fms",
            request.method,
            request.url.path,
            response.status_code,
            (time.monotonic() - start) * 1000,
        )
        return response

    @app.exception_handler(ReadError)
    async def read_error(request: Request, exc: ReadError):
        return _error(422, exc.code, exc.message, format=exc.format)

    @app.exception_handler(ServiceBusy)
    async def busy(request: Request, exc: ServiceBusy):
        return _error(503, "SERVICE_BUSY", "Too many documents in the queue, retry later")

    @app.exception_handler(ReadTimeout)
    async def read_timeout(request: Request, exc: ReadTimeout):
        return _error(408, "REQUEST_TIMEOUT", f"Upload took longer than {settings.read_timeout_s}s")

    @app.exception_handler(TooLarge)
    async def too_large(request: Request, exc: TooLarge):
        return _error(413, "FILE_TOO_LARGE", f"File exceeds {settings.max_upload_mb} MB")

    @app.exception_handler(BadRequest)
    async def bad_request(request: Request, exc: BadRequest):
        return _error(400, "BAD_REQUEST", f"Malformed multipart body: {exc}")

    @app.exception_handler(Unauthorized)
    async def unauthorized(request: Request, exc: Unauthorized):
        return _error(401, "UNAUTHORIZED", "Missing or wrong X-API-Key header")

    def require_api_key(request: Request) -> None:
        if settings.api_key is None:
            return
        given = request.headers.get("x-api-key", "")
        if not secrets.compare_digest(given.encode(), settings.api_key.encode()):
            raise Unauthorized

    @app.get("/v1/health")
    def health() -> dict:
        return {"status": "ok"}

    router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])

    @router.post(
        "/invoices/parse",
        response_model=Result,
        response_model_by_alias=True,
        responses={400: {}, 401: {}, 408: {}, 413: {}, 422: {}, 503: {}},
    )
    async def parse(request: Request, validate: bool = True, include_xml: bool = True):
        async with request.app.state.uploads:
            try:
                async with asyncio.timeout(settings.read_timeout_s):
                    body = await _read_body(request, settings.max_upload_mb * 2**20)
            except TimeoutError:
                raise ReadTimeout from None
            data = await _file_bytes(request, body)
            del body
        async with request.app.state.slots:
            return await run_in_threadpool(
                read_invoice,
                data,
                validate=validate,
                include_xml=include_xml,
                runner=request.app.state.runner,
                parser=request.app.state.parser,
            )

    @router.get("/info")
    def info() -> dict:
        return {
            "serviceVersion": __version__,
            "ruleSets": {
                "facturx": "1.09",
                "kosit": kosit_version(),
            },
        }

    app.include_router(router)
    return app


app = create_app()
