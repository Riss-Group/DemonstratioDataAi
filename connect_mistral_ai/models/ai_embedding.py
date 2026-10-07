from odoo import api, fields, models, _
import logging
from collections import defaultdict

from odoo import api, fields, models, _
from requests.exceptions import RequestException
from odoo.tools import SQL
from odoo.exceptions import UserError
from odoo.addons.ai.utils.llm_api_service import LLMApiService

from odoo.addons.connect_mistral_ai.utils.llm_providers import (EMBEDDING_MODELS_SELECTION,
    get_provider_for_embedding_model, MistralApi)

_logger = logging.getLogger(__name__)



class AIEmbedding(models.Model):
    _inherit = 'ai.embedding'

    embedding_model = fields.Selection(selection_add=EMBEDDING_MODELS_SELECTION)

    @api.model
    def _cron_generate_embedding(self, batch_size=100):
        """
        Generate embeddings for sources, handling multiple embedding models per source.
        """
        # Check for sources that need to be chunked and create embedding chunks
        self._create_embedding_chunks()

        # Check for all the embedding vectors to be generated
        missing_embeddings = self.search([
            ("embedding_vector", "=", False),
            ("has_embedding_generation_failed", "=", False)
        ])

        if not missing_embeddings:
            return False
        missing_embeddings_batch = missing_embeddings[:batch_size]

        # Group by embedding model to batch similar requests
        embeddings_by_model = defaultdict(list)
        for embedding in missing_embeddings_batch:
            model = embedding.embedding_model
            embeddings_by_model[model].append(embedding)

        staged_sources = self.env['ai.agent.source'].search([
            ('attachment_id.checksum', 'in', missing_embeddings_batch.mapped('checksum'))
        ])
        _logger.info("Starting embedding update - missing %s embeddings.", len(missing_embeddings_batch))
        self.env['ir.cron']._commit_progress(remaining=len(missing_embeddings_batch))
        failed_embeddings = self.env[self._name]

        # Process each model group separately
        for model, embeddings in embeddings_by_model.items():
            provider = get_provider_for_embedding_model(self.env, model)
            # Create batches respecting provider limits
            batches = self._create_batches(embeddings, provider)
            _logger.info(
                "Processing %s embeddings for model %s in %s batches",
                len(embeddings), model, len(batches)
            )

            for batch_idx, batch in enumerate(batches):
                try:
                    _logger.info(
                        "Processing batch %s/%s with %s embeddings for model %s",
                        batch_idx + 1, len(batches), len(batch), model
                    )

                    # Prepare batch input
                    batch_content = [emb.content for emb in batch]
                    # Get embeddings for the entire batch
                    if provider == "mistral":
                        llm_service = MistralApi(env=self.env, provider=provider)
                    else:
                        llm_service = LLMApiService(env=self.env, provider=provider)
                    response = llm_service.get_embedding(
                        input=batch_content,
                        dimensions=self._get_dimensions(),
                        model=model,
                    )
                    # Update each embedding record with its corresponding vector
                    for idx, embedding in enumerate(batch):
                        try:
                            embedding.embedding_vector = response['data'][idx]['embedding']
                        except KeyError as e:
                            _logger.error(
                                "Failed to extract embedding for record %s: %s",
                                embedding.id, str(e)
                            )
                            failed_embeddings |= embedding

                    # Commit progress for this batch
                    if not self.env['ir.cron']._commit_progress(len(batch)):
                        break

                except (RequestException, UserError, AttributeError) as e:
                    _logger.error(
                        "Failed to process batch %s/%s for model %s: %s",
                        batch_idx + 1, len(batches), model, str(e)
                    )
                    # Mark all embeddings in failed batch as failed
                    failed_embeddings |= self.env[self._name].concat(*batch)
                    continue

        if failed_embeddings:
            failed_embeddings.write({'has_embedding_generation_failed': True})
            self.env['ir.cron']._commit_progress(len(failed_embeddings))

        # Handle the status of sources
        for source in staged_sources:
            embedding_model = source.agent_id._get_embedding_model()
            source._update_source_status(embedding_model)

        if len(missing_embeddings) > batch_size:
            # we still have unfinished embeddings to generate: run the CRON again
            self.env.ref('ai.ir_cron_generate_embedding')._trigger()
            return True

        return False