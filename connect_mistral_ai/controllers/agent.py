import logging

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.ai.controllers.agent import AgentController

from ..utils.llm_providers import MistralApi, get_mistral_api_token

_logger = logging.getLogger(__name__)


class AgentController(AgentController):

    @http.route(["/ai/transcription/session"], methods=["POST"], type="jsonrpc", auth="user")
    def get_session_token(self, language=None, prompt=None):
        get_mistral_api_token(request.env)
        return {"session_id": "", "value": "", "expires_at": 0, "session": {}}

    @http.route(
        ["/ai/transcription/audio"],
        methods=["POST"],
        type="http",
        auth="user",
        csrf=False,
    )
    def transcription_audio(self, language=None, **kw):
        data = request.httprequest.get_data(cache=False, as_text=False)
        if not data:
            return request.make_json_response({"error": "empty audio"}, status=400)

        mime_type = request.httprequest.content_type or "audio/webm"

        try:
            result = MistralApi(request.env).transcribe_audio(
                audio_bytes=data,
                mime_type=mime_type,
                language=language or None,
            )
        except UserError as exc:
            return request.make_json_response({"error": str(exc)}, status=502)
        except Exception as exc:  # noqa: BLE001 - boundary
            _logger.exception("Mistral transcription failed: %s", exc)
            return request.make_json_response({"error": str(exc)}, status=502)

        return request.make_json_response({
            "text": (result.get("text") or "").strip(),
            "language": result.get("language"),
        })