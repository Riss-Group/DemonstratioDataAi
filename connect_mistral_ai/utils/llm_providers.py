from odoo.exceptions import UserError
import requests
import json
from logging import getLogger

_logger = getLogger(__name__)
from odoo.addons.ai.utils.ai_logging import ai_response_logging, api_call_logging, get_ai_logging_session
from typing import Callable, Any
import copy


# The `Provider` NamedTuple of `ai` gains new fields between Odoo releases
# (e.g. `deprecated_models`). Building our provider from a local copy of the
# class would push a tuple with missing fields into `ai`'s PROVIDERS list, and
# every generic loop over PROVIDERS would then crash with AttributeError.
# So build it from the *installed* class and fill unknown fields with empty
# values of their annotated type.
from odoo.addons.ai.utils.llm_providers import Provider

_EMPTY_BY_TYPE = {
    "dict": dict,
    "list": list,
    "set": set,
    "tuple": tuple,
    "frozenset": frozenset,
    "str": str,
}


def _empty_value(annotation):
    """Return a harmless empty value for an unknown `Provider` field."""
    name = getattr(annotation, "__name__", None) or str(annotation)
    name = name.split("[")[0].split(".")[-1].strip("'\"")
    factory = _EMPTY_BY_TYPE.get(name)
    return factory() if factory else None


def _build_custom_provider(**values):
    kwargs = {}
    for field in Provider._fields:
        if field in values:
            kwargs[field] = values[field]
        elif field in Provider._field_defaults:
            continue  # let the NamedTuple default apply
        else:
            annotation = Provider.__annotations__.get(field)
            kwargs[field] = _empty_value(annotation)
            _logger.info(
                "connect_mistral_ai: unknown Provider field %r, defaulting to %r",
                field, kwargs[field],
            )
    return Provider(**kwargs)


# Mistral Provider Definition
CUSTOM_PROVIDER = _build_custom_provider(
    name="mistral",
    display_name="Mistral",
    embedding_model="mistral-embed",
    embedding_config={
        "max_batch_size": 128,
        "max_tokens_per_request": 8192,
    },
    llms=[
        ("mistral-small-latest", "Mistral Small Latest"),
        ("mistral-medium-latest", "Mistral Medium"),
        ("mistral-large-latest", "Mistral Large Latest"),
    ],
)

EMBEDDING_MODELS_SELECTION = [
    (CUSTOM_PROVIDER.embedding_model, CUSTOM_PROVIDER.display_name)
]


def get_provider_for_embedding_model(env, embedding_model):
    if embedding_model == CUSTOM_PROVIDER.embedding_model:
        return CUSTOM_PROVIDER.name
    raise UserError(env._("No provider found for the embedding model"))


def get_provider(env, llm_model):
    if llm_model in [m[0] for m in CUSTOM_PROVIDER.llms]:
        return CUSTOM_PROVIDER.name
    return None


def get_mistral_api_token(env):
    api_key = env["ir.config_parameter"].sudo().get_param("connect_mistral_ai.mistral_key")
    if not api_key:
        raise UserError(env._("No API key set for Mistral"))
    return api_key


class MistralApi:

    def __init__(self, env, provider: str = 'mistral'):
        self.env = env
        self.base_url = "https://api.mistral.ai/v1"
        self.api_key = get_mistral_api_token(env)

    def _request_llm(self, *args, **kwargs):
        response = self._request_llm_mistral(*args, **kwargs)
        return response

    def _request(self, method: str, endpoint: str, headers: dict[str, str], body: dict,
                 data: dict | None = None, files: dict | None = None, params: dict | None = None,
                 base_url: str | None = None, timeout: int = 30) -> dict:

        route = f"{base_url or self.base_url}/{endpoint}"
        try:
            response = requests.request(
                method,
                route,
                params=params,
                headers=headers,
                json=body,
                data=data,
                timeout=timeout,
                files=files
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            error = repr(e)
            if e.response is not None:
                try:
                    response = e.response.json()
                    if isinstance(response, list) and response:
                        # Gemini return error in a list
                        response = response[0]
                    if isinstance(response, dict) and (json_error := response.get('error', {}).get('message')):
                        error = json_error
                    else:
                        error = json.dumps(response, indent=2)
                except ValueError:  # catch JSON decode errors
                    error = e.response.text
                if not error:
                    error = repr(e)

            _logger.warning("LLM API request failed: %s", error)
            raise UserError(error)

    def get_embedding(self, input, model=None, **kwargs):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        body = {
            "model": model or CUSTOM_PROVIDER.embedding_model,
            "input": input,
        }

        try:
            response = requests.post(
                f"{self.base_url}/embeddings",
                headers=headers,
                json=body,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            for item in data.get("data", []):
                embedding = item["embedding"]
                item["embedding"] = embedding + [0.0] * (1536 - len(embedding))

            return data

        except requests.exceptions.RequestException as e:
            raise UserError(f"Mistral Embedding API Error: {e}")

    def _request_llm_mistral(self, llm_model, system_prompts, user_prompts, tools=None,
                             files=None, schema=None, temperature=0.2, inputs=()):

        user_content = []

        if user_prompts:
            for p in user_prompts:
                user_content.append({"type": "text", "text": str(p)})

        if files:
            for idx, file in enumerate(files, start=1):

                if file["mimetype"] == "text/plain":
                    user_content.append({"type": "text", "text": file["value"]})
                    continue

                file_uri = f"data:{file['mimetype']};base64,{file['value']}"

                if file["mimetype"] == "application/pdf":
                    user_content.append({
                        "type": "document",
                        "format": "pdf",
                        "name": f"file_{idx}.pdf",
                        "data": file_uri,
                    })
                    continue

                if file["mimetype"].startswith("image/"):
                    user_content.append({
                        "type": "image",
                        "image_url": file_uri,
                    })

        messages = []

        if system_prompts:
            messages.append({
                "role": "system",
                "content": "\n".join(str(p) for p in system_prompts),
            })

        for msg in inputs:
            if msg.get("role") in ("assistant", "user", "tool"):
                messages.append(msg)

        if user_content:
            if len(user_content) == 1 and user_content[0]["type"] == "text":
                messages.append({"role": "user", "content": user_content[0]["text"]})
            else:
                messages.append({"role": "user", "content": user_content})

        if messages and messages[-1]["role"] == "assistant" and "tool_calls" in messages[-1]:
            pass
        elif messages and messages[-1]["role"] == "assistant":
            messages.pop()

        body = {
            "model": llm_model,
            "messages": messages,
            "temperature": temperature,
        }

        if tools:
            body["tools"] = self._convert_tools(tools)
            body["tool_choice"] = "auto"

        if schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": schema,
                "strict": True,
            }

        with api_call_logging(body["messages"], tools) as record_response:
            response, to_call, next_inputs, token_usage = self._request_llm_mistral_helper(body, tools, inputs)
            if record_response:
                token_usage = {"input_tokens": token_usage.get("prompt_tokens", 0),
                               "cached_tokens": token_usage.get("num_cached_tokens", 0),
                               "output_tokens": token_usage.get("completion_tokens", 0), }

                record_response(to_call, response, token_usage)
            return response, to_call, next_inputs

    def _request_llm_mistral_helper(self, body, tools=None, inputs=()):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        llm_response = self._request(
            endpoint="chat/completions",
            method="post",
            headers=headers,
            body=body,
        )

        to_call = []
        response = []
        next_inputs = list(inputs or ())

        usage = llm_response.get("usage", {})
        choices = llm_response.get("choices", [])
        if not choices:
            return [], [], next_inputs

        message = choices[0].get("message", {})
        content = message.get("content")
        tool_calls = message.get("tool_calls", [])

        if tool_calls:
            next_inputs.append({
                "role": "assistant",
                "tool_calls": tool_calls,
            })

            for call in tool_calls:
                fn = call["function"]
                tool_name = fn["name"]
                arguments = json.loads(fn.get("arguments", "{}"))
                call_id = call["id"]

                to_call.append((tool_name, call_id, arguments))


        elif isinstance(content, str) and content.strip():
            response.append(content)

        return response, to_call, next_inputs, usage

    def request_llm(
            self, llm_model: str, system_prompts: list[str], user_prompts: list[str],
            tools: dict[str, tuple[str, bool, Callable[[dict[str, Any]], Any], dict]] | None = None,
            files: list[dict] | None = None, schema: dict | None = None, temperature: float = 0.2,
            inputs: list[dict] | None = None,
    ) -> list[str]:
        """Same as `_request_llm`, but will call the tools until we are done.

        >>> files = [
        >>>     {'mimetype': 'text/plain', 'value': 'text content', 'file_ref': '<file_#1>'},
        >>>     {'mimetype': 'image/png', 'value': 'aW1hZ2UgY29udGVudA==', 'file_ref': '<file_#2>'},
        >>>     {'mimetype': 'application/pdf', 'value': 'cGRmIGNvbnRlbnQ=', 'file_ref': '<file_#3>'},
        >>> ]

        >>> tools = {
        >>>     "function_1": (
        >>>         "This function compute a sum",
        >>>         lambda arguments: arguments['a'] + arguments['b'],
        >>>         json_schema,
        >>>     ),
        >>>     "function_2": ...
        >>> }
        > https://json-schema.org/
        """
        with ai_response_logging(llm_model):
            return self._request_llm_silent(
                llm_model=llm_model,
                system_prompts=system_prompts,
                user_prompts=user_prompts,
                tools=tools,
                files=files,
                schema=schema,
                temperature=temperature,
                inputs=inputs,
            )

    def _request_llm_silent(
            self, llm_model: str, system_prompts: list[str], user_prompts: list[str],
            tools: dict[str, tuple[str, bool, Callable[[dict[str, Any]], Any], dict]] | None = None,
            files: list[dict] | None = None, schema: dict | None = None, temperature: float = 0.2,
            inputs: list[dict] | None = None,
    ):
        """Wraps the `_request_llm` method to handle multiple calls and tool execution."""
        AI_MAX_SUCCESSIVE_CALLS = int(self.env["ir.config_parameter"].sudo()
                                      .get_param("ai.max_successive_calls", "20"))

        AI_MAX_TOOL_CALLS_PER_CALL = int(self.env["ir.config_parameter"].sudo()
                                         .get_param("ai.max_tool_calls_per_call", "20"))

        if tools:
            tools = copy.deepcopy(tools)
            for __, allow_end_message, __, tool_parameter_schema in tools.values():
                if allow_end_message and "__end_message" not in tool_parameter_schema["properties"]:
                    tool_parameter_schema["properties"]["__end_message"] = {
                        "type": "string",
                        "description": "If you are not waiting a result and you are done, write here your last message (it must follow the instructions). If you will do an action after this one, leave it empty.",
                    }
                if "__end_message" in tool_parameter_schema["properties"] and "__end_message" not in \
                        tool_parameter_schema["required"]:
                    tool_parameter_schema["required"].append("__end_message")

        inputs = inputs or []
        all_responses = []
        for api_call in range(AI_MAX_SUCCESSIVE_CALLS):
            responses, next_actions, inputs = self._request_llm(
                llm_model,
                system_prompts,
                user_prompts,
                files=files,
                inputs=inputs,
                schema=schema,
                tools=tools,
                temperature=temperature,
            )
            all_responses.extend(responses)

            if not next_actions:
                break

            done = False
            session = get_ai_logging_session()

            if session:
                session["tool_calls"] += min(len(next_actions), AI_MAX_TOOL_CALLS_PER_CALL)

            for i, (tool_name, call_id, arguments) in enumerate(next_actions):
                if i >= AI_MAX_TOOL_CALLS_PER_CALL:
                    _logger.warning("AI: Tool call limit reached, stopping further tool calls")
                    inputs.append(self._build_tool_call_response(call_id,
                                                                 "Error: This tool call isn't processed because of tool call limit, try again"))
                    continue

                if tool_name not in tools:
                    _logger.error("AI: Try to call a forbidden action %s", tool_name)
                    inputs.append(self._build_tool_call_response(call_id,
                                                                 f"Error: unknown tool '{tool_name}'. Try again with the correct tool name."))
                    continue

                has_end_message = "__end_message" in arguments
                end_message = arguments.pop("__end_message", None)
                result, error = tools[tool_name][2](arguments=arguments)

                inputs.append(self._build_tool_call_response(call_id, result))

                if has_end_message and error is None:
                    done = True
                    if end_response := end_message and end_message.strip():
                        all_responses.append(end_response)
                        _logger.info("AI: action terminate early: %s", end_response)
                    else:
                        _logger.info("AI: action terminate early with empty message")

            if session and len(next_actions) > 1:  # Batch of tool calls
                _logger.debug("[AI Tool Summary] Batch #%d completed, %d tool calls", session["current_batch_id"],
                              len(next_actions))

            if done:
                break

        _logger.info("AI: API calls %s", api_call + 1)

        if not all_responses:
            error_msg = "Processing loop ended with no response."
            if api_call + 1 >= AI_MAX_SUCCESSIVE_CALLS:
                error_msg = "Number of successive API calls exceeded, please try again with a more precise request."
            raise ValueError(error_msg)

        return all_responses

    def _convert_tools(self, tools):
        final = []
        for tool_name, (description, __, __, schema) in tools.items():
            final.append({
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": description,
                    "parameters": schema,
                }
            })
        return final

    def _build_tool_call_response(self, tool_call_id, return_value):
        """Build the response for the given tool call.

        :param tool_call_id: The identifier of the tool call
        :param return_value: The value the tool returned
        """
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": str(return_value),
        }


