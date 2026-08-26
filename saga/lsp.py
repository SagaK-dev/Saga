from __future__ import annotations

"""Small dependency-free Language Server Protocol bridge for Saga diagnostics.

The server intentionally starts with the stable part of LSP needed for an
international editor ecosystem: initialization, full document sync, and
publishDiagnostics.  It uses the same compiler and diagnostic catalogue as the
CLI, so IDEs never need to parse human text.
"""

import json
import pathlib
import sys
from urllib.parse import unquote, urlparse

from . import __version__
from .api import compile_source
from .diagnostics import get_spec, localize_message, normalize_language
from .errors import SourceError


def uri_to_filename(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        path = unquote(parsed.path)
        if sys.platform.startswith("win") and len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return str(pathlib.Path(path))
    return uri


def _lsp_character(line_text: str, scalar_index: int, encoding: str) -> int:
    prefix = line_text[:max(0, scalar_index)]
    if encoding == "utf-8":
        return len(prefix.encode("utf-8"))
    if encoding == "utf-32":
        return len(prefix)
    return len(prefix.encode("utf-16-le")) // 2


def diagnostic_for_error(error: SourceError, source: str, language: str = "auto", position_encoding: str = "utf-16") -> dict:
    language = normalize_language(language)
    spec = get_spec(error.diagnostic_id)
    source_lines = source.splitlines()
    line_text = source_lines[error.line - 1] if 1 <= error.line <= len(source_lines) else ""
    start_scalar = max(0, error.column - 1)
    end_scalar = max(start_scalar + 1, (error.end_column or (error.column + 1)) - 1)
    start_col = _lsp_character(line_text, start_scalar, position_encoding)
    end_col = _lsp_character(line_text, end_scalar, position_encoding)
    if end_col <= start_col:
        end_col = start_col + 1
    if error.hint and error.hint.startswith("candidate:"):
        candidate = error.hint.split(":", 1)[1]
        help_text = f"Did you mean `{candidate}`?" if language == "en" else f"`{candidate}` の間違いではありませんか？"
    elif language == "ja":
        help_text = error.hint or (spec.help(language) if spec else None)
    else:
        help_text = (spec.help(language) if spec else None) or error.hint
    title = spec.title(language) if spec else error.message
    detail = localize_message(error.code, error.diagnostic_id, error.message, language, error.detail_data)
    pieces = [title]
    if detail and detail != title:
        pieces.append(detail)
    if help_text:
        pieces.append(("Fix: " if language == "en" else "修正案: ") + help_text)
    return {
        "range": {
            "start": {"line": max(0, error.line - 1), "character": start_col},
            "end": {"line": max(0, error.line - 1), "character": end_col},
        },
        "severity": 1,
        "code": error.diagnostic_id,
        "source": "Saga",
        "message": "\n".join(pieces),
        "data": {"category": error.code, "diagnosticId": error.diagnostic_id},
    }


def diagnostics_for_text(text: str, uri: str, language: str = "auto", position_encoding: str = "utf-16") -> list[dict]:
    try:
        compile_source(text, uri_to_filename(uri))
    except SourceError as error:
        return [diagnostic_for_error(error, text, language, position_encoding)]
    return []


class LspServer:
    def __init__(self, *, language: str = "auto", instream=None, outstream=None) -> None:
        self.language = normalize_language(language)
        self.input = instream or sys.stdin.buffer
        self.output = outstream or sys.stdout.buffer
        self.documents: dict[str, str] = {}
        self.shutdown_requested = False
        self.position_encoding = "utf-16"

    def read_message(self) -> dict | None:
        headers: dict[str, str] = {}
        while True:
            line = self.input.readline()
            if not line:
                return None
            if line in {b"\r\n", b"\n"}:
                break
            try:
                key, value = line.decode("ascii").split(":", 1)
            except (UnicodeDecodeError, ValueError):
                continue
            headers[key.strip().lower()] = value.strip()
        try:
            length = int(headers.get("content-length", "0"))
        except ValueError:
            return None
        if length <= 0:
            return None
        body = self.input.read(length)
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def send(self, message: dict) -> None:
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.output.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
        self.output.write(data)
        self.output.flush()

    def response(self, request_id, result=None, error=None) -> None:
        message = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result
        self.send(message)

    def publish(self, uri: str, text: str) -> None:
        self.send({
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": uri, "diagnostics": diagnostics_for_text(text, uri, self.language, self.position_encoding)},
        })

    def handle(self, message: dict) -> bool:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            offered = (((params.get("capabilities") or {}).get("general") or {}).get("positionEncodings") or [])
            if offered and "utf-16" not in offered:
                self.position_encoding = "utf-8" if "utf-8" in offered else ("utf-32" if "utf-32" in offered else "utf-16")
            else:
                self.position_encoding = "utf-16"
            self.response(request_id, {
                "capabilities": {
                    "positionEncoding": self.position_encoding,
                    "textDocumentSync": {"openClose": True, "change": 1, "save": {"includeText": True}},
                    "hoverProvider": False,
                },
                "serverInfo": {"name": "Saga Language Server", "version": __version__},
            })
        elif method == "initialized":
            pass
        elif method == "shutdown":
            self.shutdown_requested = True
            self.response(request_id, None)
        elif method == "exit":
            return False
        elif method == "textDocument/didOpen":
            doc = params.get("textDocument") or {}
            uri, text = str(doc.get("uri", "")), str(doc.get("text", ""))
            if uri:
                self.documents[uri] = text
                self.publish(uri, text)
        elif method == "textDocument/didChange":
            doc = params.get("textDocument") or {}
            uri = str(doc.get("uri", ""))
            changes = params.get("contentChanges") or []
            if uri and changes:
                text = str(changes[-1].get("text", ""))
                self.documents[uri] = text
                self.publish(uri, text)
        elif method == "textDocument/didSave":
            doc = params.get("textDocument") or {}
            uri = str(doc.get("uri", ""))
            text = params.get("text", self.documents.get(uri, ""))
            if uri:
                self.documents[uri] = str(text)
                self.publish(uri, str(text))
        elif method == "textDocument/didClose":
            doc = params.get("textDocument") or {}
            uri = str(doc.get("uri", ""))
            self.documents.pop(uri, None)
            if uri:
                self.send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {"uri": uri, "diagnostics": []}})
        elif request_id is not None:
            self.response(request_id, error={"code": -32601, "message": f"Method not found: {method}"})
        return True

    def run(self) -> int:
        while True:
            message = self.read_message()
            if message is None:
                return 0 if self.shutdown_requested else 1
            if not self.handle(message):
                return 0 if self.shutdown_requested else 1


def run_lsp(*, language: str = "auto") -> int:
    return LspServer(language=language).run()
