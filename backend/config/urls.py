from django.urls import include, path

urlpatterns = [
    path("api/v1/ontology/", include("prime_ontology.urls")),
]
