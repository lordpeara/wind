"""

    wind.web.app
    ~~~~~~~~~~~~

    Web application serving http requests.

"""

import json
import types
import hashlib
import traceback
from wind.web.codec import encode, to_str
from wind.log import wind_logger, LogType
from wind.web.httpmodels import (
    HTTPRequest, HTTPResponse, HTTPMethod,
    HTTPStatusCode, HTTPResponseHeader)
from wind.datastructures import FlexibleDeque
from wind.exceptions import ApplicationError, HTTPError


def path(handler, route, methods):
    """Api method for providing intuition to url binding."""
    # TODO: Validate parameters
    return Path(handler, route=route, methods=methods)


class WindApp(object):
    """Wind web application
    We expect that our app usage code will be like this, and it works now.
    This usage interface is made for the purpose of testing `performance`.
    It may be revised later.

    `hello wind!` Example::

        from wind.web.httpserver import HTTPServer
        from wind.web.app import WindApp, path, Resource

        def hello_wind(request):
            return 'hello wind!'

        class HelloResource(Resource):
            def handle_get(self):
                self.write('hello wind!')
                self.finish()

        app = WindApp([
            path(hello_wind, route='/', methods=['get']),
            path(HelloResource, route='/resource', methods=['get'])
        ])
        server = HTTPServer(app=app)
        server.run_simple('127.0.0.1', 9000)

    """

    def __init__(self, urls):
        self._dispatcher = PathDispatcher(urls)

    def react(self, conn, request):
        if not isinstance(request, HTTPRequest):
            raise ApplicationError('Can only react to `HTTPRequest`')

        path = self._dispatcher.lookup(request.path)
        if path is None:
            # No registered path. We don't need to handle this request.
            # XXX: Should expose various error states in `react`.
            # (Not only returning False stupidly)

            # Let's make a path to error.
            path = Path(self._error_handler)

        # Synchronously run handling method. (Temporarily)
        path.follow(conn, request)

    def _error_handler(self, request):
        raise HTTPError(HTTPStatusCode.NOT_FOUND)


class PathDispatcher(object):
    def __init__(self, urls):
        try:
            self._paths = []
            self._paths.extend(urls)
        except TypeError:
            raise ApplicationError('PathDispatcher wants `list` of `Path`')

    def lookup(self, url):
        for path in self._paths:
            if url == path.route:
                return path


class Path(object):
    """Contains information needed for handling HTTP request."""

    def __init__(
            self, handler, route=None, methods=None, **kwargs):
        """Initialize path.
        @param handler:
            Method or Class inherits from `Resource`.
        @param route:
            URI path when serving HTTP request.
            If it's None, this path is considered as `error path`.
        @param methods:
            Allowed HTTP methods. `List` of string indicating method.

        """
        # if handler is not method binding, delay handler creation time
        # to time when actually serving request.
        if isinstance(handler, (types.FunctionType, types.MethodType)):
            handler = self._wrap_handler(handler)
        self._handler = handler
        self._error_path = route is None
        if not self._error_path:
            self._route = self._process_route(route)
            self._methods = \
                [self._validate_method(method.lower()) for method in methods]

    @property
    def route(self):
        return self._route

    @property
    def methods(self):
        return self._methods

    @property
    def error_path(self):
        return self._error_path

    def allowed(self, method):
        """Assume param `method` has already converted to lowercase"""
        if hasattr(self, '_methods'):
            return method in self._methods

    def follow(self, conn, request):
        """Go after the path!
        When this method is called from app, `Resource` in path will
        react to HTTP request.

        """
        if isinstance(self._handler, type):
            # Actual handler creation for user-defined `Resource`.
            self._handler(path=self).react(conn, request)
        else:
            self._handler.react(conn, request)

    def _validate_method(self, method):
        if method not in HTTPMethod.all():
            raise ApplicationError("Unsupported HTTP method '%s'" % method)
        return method

    def _wrap_handler(self, handler):
        """If handler is method, wraps handler with `Resource` initialized with
        this path. Return newly created `Resource` object.

        """
        if not hasattr(handler, '__call__'):
            raise ApplicationError(
                'Request handler registered to app should be callable')

        resource = Resource(path=self)
        resource.inject(method=handler)
        return resource

    def _process_route(self, route):
        """Process with regex in route"""
        # XXX: Not implemented yet
        return route


class Resource(object):
    """Class for HTTP web resource.
    May inherit this class to implement `comet` or asynchronously
    handle HTTP request.

    Methods for the caller:

    - __init__(path=None)
    - react(conn, request)
    - inject(method=None)
    - add_response_header(key, value)
    - remove_response_header(key)
    - send_response(status_code=HTTPStatusCode.OK)
    - write(chunk, left=False)
    - finish()

    Methods may be overrided:

    - initialize()
    - handle_get(request)
    - handle_post(request)
    - handle_put(request)
    - handle_delete(request)
    - handle_head(request)
    - _error_message()

    """
    def __init__(self, path=None):
        self._path = path
        self._synchronous_handler = None
        self._conn = None
        self._request = None
        self._response = None
        self._status_code = None
        self._processing = False
        self._write_buffer = FlexibleDeque()
        self._write_buffer_bytes = 0
        self._response_header = HTTPResponseHeader()
        self._asynchronous = True
        self.initialize()

    def initialize(self):
        """Constructor hook"""
        pass

    def handle_get(self):
        self._raise_not_allowed()

    def handle_post(self):
        self._raise_not_allowed()

    def handle_put(self):
        self._raise_not_allowed()

    def handle_delete(self):
        self._raise_not_allowed()

    def handle_head(self):
        self._raise_not_allowed()

    def _raise_not_allowed(self):
        raise HTTPError(HTTPStatusCode.METHOD_NOT_ALLOWED)

    def inject(self, method=None):
        if hasattr(method, '__call__') and path is not None:
            self._synchronous_handler = method

    def react(self, conn, request):
        self._processing = True
        self._conn = conn
        self._request = request

        try:
            if not self._path.allowed(request.method) \
                    and not self._path.error_path:
                self._raise_not_allowed()

            if self._synchronous_handler is not None:
                # Simply run synchronous handler for test!
                # NOTE that there's no etag support to this kind of handler.
                chunk = self._synchronous_handler(request)
                self.write(chunk)
                self.finish()
            else:
                # Execute request handler
                getattr(self, 'handle_' + request.method)()
                if not self._asynchronous:
                    self.finish()
        except HTTPError as e:
            http_errors = \
                [
                    HTTPStatusCode.NOT_FOUND,
                    HTTPStatusCode.METHOD_NOT_ALLOWED,
                    HTTPStatusCode.NOT_MODIFIED
                ]
            if e.args[0] in http_errors:
                self.send_response(status_code=e.args[0])
            else:
                # XXX: Grab this.
                pass
        except Exception:
            wind_logger.log(traceback.format_exc(), LogType.ACCESS)
            self.send_response(
                status_code=HTTPStatusCode.INTERNAL_SERVER_ERROR)

    def write(self, chunk, left=False):
        if isinstance(chunk, dict):
            chunk = json.dumps(chunk)
            self._response_header.to_json_content()

        if chunk:
            chunk = encode(chunk)
            if left:
                self._write_buffer.appendleft(chunk)
            else:
                self._write_buffer.append(chunk)
            self._write_buffer_bytes += len(chunk)

    def add_response_header(self, key, value):
        self._response_header.add(key, value)

    def remove_response_header(self, key):
        self._response_header.remove(key)

    def set_status_code(self, status_code):
        """Set HTTP response status code.
        @param status_code: status code string (see httpmodels.HTTPStatusCode)

        """
        self._status_code = status_code

    def finish(self):
        """This method finishes current connection by sending response
        with written chunk in self._write_buffer.

        """
        if self._etag_available():
            etag = self._generate_etag()
            request_etag = self._get_etag()
            if request_etag == etag:
                raise HTTPError(HTTPStatusCode.NOT_MODIFIED)
            else:
                self._set_etag(etag)

        if self._write_buffer:
            self._response_header. \
                add_content_length(self._write_buffer_bytes)

        self.set_status_code(HTTPStatusCode.OK)
        self._generate_response()
        self.write(self._response.raw(), left=True)
        self._write_buffer.gather(self._write_buffer_bytes)
        self._conn.stream.write(self._write_buffer.popleft(), self._clear)

    def send_response(self, status_code=HTTPStatusCode.OK):
        """This method finishes current connection by sending response which
        is typically error.
        NOTE that it will write only response headers regardless of chunks
        in self._write_buffer.

        """
        self._flush_buffer()
        self.set_status_code(status_code)
        self.write(self._error_message())
        self._generate_response()
        self.write(self._response.raw(), left=True)
        self._write_buffer.gather(self._write_buffer_bytes)
        self._conn.stream.write(self._write_buffer.popleft(), self._clear)

    def _error_message(self):
        """This method can be overrided to make custom error message
        By default, this method returns status_code as string.

        """
        return self._status_code

    def _generate_response(self):
        """Generate response header.
        Calling this method publically is not recommended.
        NOTE that if response_header has changed after this method is called,
        you should call this method again to generate response.

        """
        self._response = HTTPResponse(
            request=self._request, headers=self._response_header.to_dict(),
            status_code=self._status_code)

    def _clear(self):
        self._conn.close()
        self._log_access()
        self._processing = False
        self._conn = self._request = None
        self._flush_buffer()
        self._response_header.clear()

    def _flush_buffer(self):
        self._write_buffer = FlexibleDeque()
        self._write_buffer_bytes = 0

    def _log_access(self):
        if self._request is not None and self._response is not None:
            msg = '%s %s %s' % \
                (self._request.method.upper(), self._request.url,
                    self._response.status_code)
            wind_logger.log(msg, LogType.ACCESS)

    def _get_etag(self):
        """Get `Etag` from `If-None-Match` in HTTP request headers"""
        return self._request.headers.if_none_match

    def _set_etag(self, etag):
        """Add `Etag` header to response header"""
        self._response_header.add_etag(etag)

    def _generate_etag(self):
        """Generate etag value for chunk in self._write_buffer.
        This method use md5 hashing to generate etag.
        The MD5 hash assures that the actual etag is only 32 characters long,
        while assuring that they are highly unlikely to collide.

        """
        md5 = hashlib.md5()
        for chunk in self._write_buffer:
            md5.update(chunk)
        return to_str(md5.hexdigest())

    def _etag_available(self):
        """Checks if `Etag` can be used. This method is needed because
        there is no `Etag` in HTTP 1.0 (RFC 1945).
        This method can be overrided to disable `Etag` cache validation.
        If this method always returns `False`, this handler don't use
        `Etag` any more.

        """
        return float(self._request.version[-3:]) > 1.0
