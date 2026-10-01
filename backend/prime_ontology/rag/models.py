from django.db import models

from ..models import Ontology

APP = "prime_ontology"
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")


class RagDocument(models.Model):
    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="rag_documents")
    title = models.CharField(max_length=300)
    source_uri = models.CharField(max_length=500, blank=True, default="")
    doc_type = models.CharField(max_length=40, blank=True, default="document")  # contract | policy | email | manual | document ...
    classification = models.CharField(max_length=14, default="internal")  # public | internal | confidential | restricted
    metadata = models.JSONField(default=dict)  # free filters: {"department": "legal", "year": 2026}
    content_hash = models.CharField(max_length=40, blank=True, default="")
    chunk_count = models.IntegerField(default=0)
    created_by = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = APP
        ordering = ["-id"]


class RagChunk(models.Model):
    document = models.ForeignKey(RagDocument, on_delete=models.CASCADE, related_name="chunks")
    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="rag_chunks")
    ordinal = models.IntegerField(default=0)
    heading = models.CharField(max_length=300, blank=True, default="")
    page = models.IntegerField(null=True, blank=True)
    text = models.TextField()
    concepts = models.JSONField(default=list)  # ontology classes the chunk talks about
    entities = models.JSONField(default=list)  # fabric entity ids the chunk mentions
    embedding = models.BinaryField(null=True, blank=True)  # float32 vector
    embedding_provider = models.CharField(max_length=40, blank=True, default="")
    classification = models.CharField(max_length=14, default="internal")  # copied from the document: filtering without a join

    class Meta:
        app_label = APP
        indexes = [models.Index(fields=["ontology", "classification"])]
