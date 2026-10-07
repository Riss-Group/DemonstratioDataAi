from odoo import _, models, api
from odoo.exceptions import UserError
from ..utils.llm_providers import (
    CUSTOM_PROVIDER,
    get_provider_for_embedding_model,
    get_provider,
    get_mistral_api_token,
    MistralApi,
)
from odoo.addons.ai.utils.llm_providers import PROVIDERS, EMBEDDING_MODELS_SELECTION
from textwrap import dedent

TEMPERATURE_MAP = {
    "analytical": 0.2,
    "balanced": 0.5,
    "creative": 0.8,
}

PREPROMPTS = {
    'default_system_prompt': "You are a RAG assistant.",
    'tools': dedent("""
        You have access to tools that can perform actions. Only use these tools when:
        1. The user explicitly requests the action.
        2. The action is clearly the most appropriate response to their query.

        If the user asks you to perform an action, retrieve the required information from his prompt and the conversation history, then use the tool.

        If date is needed, Use today's date to make a relative date if the user didn't provide a clear one (e.g. tomorrow, in one week, etc.).
        Rarely suggest the actions in your response.
    """).strip(),
    'restrict_to_sources': dedent("""
        ## INSTRUCTIONS FOR ANSWERING QUERIES

        1. For greetings (hello, hi, how are you), reply with a greeting.

        2. For all other questions, you MUST ONLY use information from the provided context and conversation history.

        3. If the RAG context and history don't contain information to answer the query or it is not provided at all:
           - use the assistant/user messages as context or ask the user to provide more information.
           - DO NOT make up information or use your general knowledge.

        4. When answering based on the context:
           - Synthesize information from multiple sources when appropriate
           - Consider the conversation history for follow-up questions

        5. If a user asks a follow-up question like 'what is this?' or 'tell me more', refer to the conversation history to understand the context and answer accordingly.

        6. If no context is provided at all, respond with: 'No source information has been provided for me to reference.'

        7. Avoid using HTML elements in your response.
    """).strip(),
    'context': dedent("""
        - Use the RAG context to answer the question.
        - Every claim or piece of information provided in the answer **MUST** be immediately followed by an inline citation in the format **[SOURCE:Attachment ID]** to indicate its source from the RAG context (e.g., "The capital of France is Paris [SOURCE:210].").
        - If a claim draws on multiple sources, cite all of them (e.g., "The process requires heat and pressure [SOURCE:210, 211].").
        - If **NO** source chunks were used to answer the question, do not include any citations.

        - Example of the required format for the response with the attachment IDs [SOURCE:210, 211] in its answer:
        - The primary goal of the project is to enhance data security protocols [SOURCE:210]. This enhancement includes a mandatory two-factor authentication system [SOURCE:211].
    """).strip(),
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

    def _build_mistral_rag_context(self, prompt):
        self.ensure_one()
        messages = []
        context = ""
        if self.sources_ids:
            provider = self._get_provider()
            embedding_model = self._get_embedding_model()
            response = MistralApi(env=self.env, provider=provider).get_embedding(
                input=prompt,
                dimensions=self.env['ai.embedding']._get_dimensions(),
                model=embedding_model
            )
            if not response or "data" not in response:
                raise UserError(_("Failed to get embeddings for the prompt."))

            prompt_embedding = response['data'][0]['embedding']
            similar_embeddings = self.env['ai.embedding']._get_similar_chunks(
                query_embedding=prompt_embedding,
                sources=self.sources_ids,
                embedding_model=self._get_embedding_model(),
                top_n=5
            )
            if similar_embeddings:
                embeddings_attachment_checksums = similar_embeddings.mapped('attachment_id.checksum')
                agent_sources = self.env['ai.agent.source'].search([
                    ('attachment_id.checksum', 'in', embeddings_attachment_checksums),
                    ('agent_id', '=', self.id),
                ])
                source_map = {source.attachment_id.checksum: source for source in agent_sources}
                for embedding in similar_embeddings:
                    checksum = embedding.attachment_id.checksum
                    agent_source = source_map[checksum]
                    context += (
                        f"(Source Chunk {agent_source.name})\n"
                        f" (attachment_id: {agent_source.attachment_id.id})\n"
                        f"{embedding.content}\n\n"
                    )

                final_context_message = f"##RAG context information:\n\n{context}"

                messages.append(final_context_message)
                messages.append(PREPROMPTS['context'])
        return messages

    def _generate_response(self, prompt, chat_history=None, extra_system_context=""):
        self.ensure_one()
        mistral_models = [m[0] for m in CUSTOM_PROVIDER.llms]

        if self.llm_model in mistral_models:

            system_messages = self._build_system_context(extra_system_context)
            rag_ctx = self._build_mistral_rag_context(prompt)
            if rag_ctx:
                system_messages.extend(rag_ctx)

            response_id = MistralApi(self.env).request_llm(
                self.llm_model,
                system_messages,
                [],
                inputs=(chat_history or []) + [{"role": "user", "content": prompt}],
                tools=self.topic_ids.tool_ids._get_ai_tools(),
                temperature=TEMPERATURE_MAP[self.response_style],
            )
            return response_id


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
