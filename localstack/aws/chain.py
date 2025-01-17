"""
The core concepts of the HandlerChain.
"""
import logging
from typing import Any, Callable, List, Optional

from localstack.http import Response

from .api import RequestContext

LOG = logging.getLogger(__name__)

Handler = Callable[["HandlerChain", RequestContext, Response], None]
ExceptionHandler = Callable[["HandlerChain", Exception, RequestContext, Response], None]


class HandlerChain:
    # handlers
    request_handlers: List[Handler]
    response_handlers: List[Handler]
    exception_handlers: List[ExceptionHandler]

    # behavior configuration
    stop_on_error: bool = True
    raise_on_error: bool = False

    # state
    stopped: bool
    terminated: bool
    error: Optional[Exception]
    response: Optional[Response]
    context: Optional[RequestContext]

    def __init__(
        self,
        request_handlers: List[Handler] = None,
        response_handlers: List[Handler] = None,
        exception_handlers: List[ExceptionHandler] = None,
    ) -> None:
        super().__init__()
        self.request_handlers = request_handlers or list()
        self.response_handlers = response_handlers or list()
        self.exception_handlers = exception_handlers or list()

        self.stopped = False
        self.terminated = False
        self.error = None
        self.response = None
        self.context = None

    def handle(self, context: RequestContext, response: Response):
        self.context = context
        self.response = response

        for handler in self.request_handlers:
            try:
                handler(self, self.context, response)
            except Exception as e:
                # prepare the continuation behavior, but exception handlers could overwrite it
                if self.raise_on_error:
                    self.error = e
                if self.stop_on_error:
                    self.stopped = True

                # call exception handlers safely
                self._call_exception_handlers(e, response)

            # decide next step
            if self.error:
                raise self.error
            if self.terminated:
                return
            if self.stopped:
                break

        # call response filters
        self._call_response_handlers(response)

    def respond(self, status_code: int = 200, payload: Any = None):
        """
        Convenience method for handlers to stop the chain and set the given status and payload to the current response
        object.
        :param status_code: the HTTP status code
        :param payload: the payload of the response
        """
        self.response.status_code = status_code
        if isinstance(payload, (list, dict)):
            self.response.set_json(payload)
        elif isinstance(payload, (str, bytes, bytearray)):
            self.response.data = payload
        elif payload is None and not self.response.response:
            self.response.response = []
        else:
            self.response.response = payload
        self.stop()

    def stop(self) -> None:
        """
        Stop the processing of the request handlers and proceed with response handlers.
        """
        self.stopped = True

    def terminate(self) -> None:
        """
        Terminate the handler chain, which skips response handlers.
        """
        self.terminated = True

    def throw(self, error: Exception) -> None:
        """
        Raises the given exception after the current request handler is done. This has no effect in response handlers.
        :param error: the exception to raise
        """
        self.error = error

    def _call_response_handlers(self, response):
        for handler in self.response_handlers:
            if self.terminated:
                return

            try:
                handler(self, self.context, response)
            except Exception as e:
                msg = "exception while running response handler"
                if LOG.isEnabledFor(logging.DEBUG):
                    LOG.exception(msg)
                else:
                    LOG.warning(msg + ": %s", e)

    def _call_exception_handlers(self, e, response):
        for exception_handler in self.exception_handlers:
            try:
                exception_handler(self, e, self.context, response)
            except Exception as nested:
                # make sure we run all exception handlers
                msg = "exception while running exception handler"
                if LOG.isEnabledFor(logging.DEBUG):
                    LOG.exception(msg)
                else:
                    LOG.warning(msg + ": %s", nested)
