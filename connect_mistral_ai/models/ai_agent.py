from odoo import models, api
from odoo.exceptions import UserError
from odoo.addons.connect_mistral_ai.utils.llm_providers import (
    CUSTOM_PROVIDER,
    get_provider_for_embedding_model,
    get_provider,
    get_mistral_api_token,
    MistralApi,
)
from odoo.addons.ai.utils.llm_providers import PROVIDERS, EMBEDDING_MODELS_SELECTION

TEMPERATURE_MAP = {
    "analytical": 0.2,
    "balanced": 0.5,
    "creative": 0.8,
}


class AIAgent(models.Model):
    _inherit = "ai.agent"

    @api.model
    def _get_llm_model_selection(self):
        selection = super()._get_llm_model_selection()
        selection.extend(CUSTOM_PROVIDER.llms)
        return selection

    def _get_provider(self):
        self.ensure_one()
        try:
            return super()._get_provider()
        except UserError:
            try:
                return get_provider(self.env, self.llm_model)
            except UserError:
                raise UserError(self.env._("No provider found for the selected model"))

    def _get_embedding_model(self):
        self.ensure_one()
        try:
            return super()._get_embedding_model()
        except UserError:
            try:
                provider = get_provider(self.env, self.llm_model)
                if provider == "mistral":
                    return CUSTOM_PROVIDER.embedding_model
            except UserError:
                pass
            raise UserError(self.env._("No embedding model found for the selected provider"))

    def _generate_response(self, prompt, chat_history=None, extra_system_context=""):
        self.ensure_one()
        mistral_models = [m[0] for m in CUSTOM_PROVIDER.llms]

        if self.llm_model in mistral_models:

            # Build agent system messages
            system_messages = self._build_system_context(extra_system_context)
            rag_ctx = self._build_rag_context(prompt)
            if rag_ctx:
                system_messages.extend(rag_ctx)

            # Request to Mistral
            response_id = MistralApi(self.env).request_llm(
                self.llm_model,
                system_messages,
                [],
                inputs=(chat_history or []) + [{"role": "user", "content": prompt}],
                tools=self.topic_ids.tool_ids._get_ai_tools(),
                temperature=TEMPERATURE_MAP[self.response_style],
            )
            return response_id


        # fallback to other providers
        return super()._generate_response(prompt, chat_history, extra_system_context)


def _extend_llm_providers():

    if CUSTOM_PROVIDER not in PROVIDERS:
        PROVIDERS.append(CUSTOM_PROVIDER)

    mistral_embedding = (
        CUSTOM_PROVIDER.embedding_model,
        CUSTOM_PROVIDER.display_name,
    )
    if mistral_embedding not in EMBEDDING_MODELS_SELECTION:
        EMBEDDING_MODELS_SELECTION.append(mistral_embedding)


_extend_llm_providers()
