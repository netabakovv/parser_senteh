from __future__ import annotations

import json
import os
import urllib.request

ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def request_payload(text: str) -> dict:
    return {
        "state": text,
        "model": "jev-latest",
        "questions": {
            "task": {
                "type": "choice",
                "instructions": "Какую задачу описывает покупатель? Выбери ближайший вариант; если из текста это нельзя определить, выбери unclear.",
                "criteria": {
                    "connect_pipe_fitting": "Соединить трубу с фитингом",
                    "other": "Другая явно описанная задача",
                    "unclear": "Недостаточно контекста или задача неясна",
                },
            }
        },
    }


def classify(text: str, *, use_stub: bool = False) -> dict:
    if use_stub:
        # Deterministic test stub; confidence below the acceptance threshold is unclear.
        normalized = text.lower()
        if "соедин" in normalized and "труб" in normalized and not any(word in normalized for word in ("неясно", "непонят", "подобрать", "нужно")):
            response = {"answers": {"task": {"type": "choice", "choice": "connect_pipe_fitting", "probabilities": {"connect_pipe_fitting": 0.91, "other": 0.04, "unclear": 0.05}, "confidence": 0.88}}}
        else:
            response = {"answers": {"task": {"type": "choice", "choice": "unclear", "probabilities": {"connect_pipe_fitting": 0.25, "other": 0.25, "unclear": 0.50}, "confidence": 0.30}}}
    else:
        key = os.environ.get("JEV_API_KEY")
        if not key:
            raise RuntimeError("Задайте JEV_API_KEY или включите режим заглушки.")
        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(request_payload(text)).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            response = json.load(response)

    answer = response.get("answers", {}).get("task", {})
    probs = answer.get("probabilities", {})
    choice = answer.get("choice", "unclear")
    confidence = float(answer.get("confidence") or 0)
    accepted = choice if confidence >= 0.75 and probs.get(choice, 0) >= 0.75 else "unclear"
    return {"choice": accepted, "raw_choice": choice, "confidence": confidence, "probabilities": probs}
