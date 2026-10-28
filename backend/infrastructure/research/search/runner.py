"""Public search entrypoints compose the use case with its local adapter."""

from backend.application.search import Search
from backend.infrastructure.recommenders import create_recommender
from backend.infrastructure.research.search.backend import LocalSearch


def smoke(config, *, factory=create_recommender):
    return Search(LocalSearch(factory)).smoke(config)


def run_search(config, *, factory=create_recommender):
    return Search(LocalSearch(factory)).run(config)
