"""Deterministic typed decision adapter used only by offline core tests."""

from __future__ import annotations

import hashlib
import json


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class FakeDecisionAdapter:
    def __init__(self, rankings=None, *, confidence=0.9, elapsed_ms=3):
        self.rankings = rankings or {}
        self.confidence = confidence
        self.elapsed_ms = elapsed_ms
        self.calls = 0

    def decide(self, request):
        self.calls += 1
        recommendations = {}
        for role, candidates in request["candidates"].items():
            ranking = list(self.rankings.get(role, candidates))
            denominator = sum(range(1, len(ranking) + 1))
            descending = list(range(len(ranking), 0, -1))
            scores = {
                profile_id: score / denominator
                for profile_id, score in zip(ranking, descending)
            }
            recommendations[role] = {
                "ranking": ranking,
                "probabilities": scores,
                "confidence": self.confidence,
                "abstain_reason": None,
            }
        return {
            "schema_version": 1,
            "request_sha256": hashlib.sha256(
                _canonical(request).encode(),
            ).hexdigest(),
            "adapter": request["adapter"],
            "language": request["language"],
            "truncation": {"occurred": False, "detail": None},
            "recommendations": recommendations,
            "usage": {
                "source": "fake",
                "billable_requests": 1,
                "input_tokens": 10,
                "output_tokens": 2,
                "cost_usd": 0.0,
            },
            "elapsed_ms": self.elapsed_ms,
        }
