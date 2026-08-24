"""One LLM call, two providers, and the disk cache that makes them optional.

Every call is cached to disk by content hash. Once the cache is populated the
whole pipeline runs with no API key and no network, which is why the cache
directories are committed.

One provider: Gemini, which is the one that produced the committed cache. An
Anthropic path existed earlier and was removed rather than left in place
unexercised — see DECISIONS.md D-015.

`app.py` never imports this module.
"""

import hashlib
import json
import os
import time

import httpx

import config

# Transport failures worth re-sending: a stalled read, a refused connection, a
# rate limit. Distinct from a model that answered badly, which extract.py owns.
TRANSPORT_ERRORS = (httpx.TransportError, httpx.TimeoutException)


def load_env_file():
    """Read KEY=value pairs from a local .env into the environment.

    Five lines rather than a dependency. `.env` is gitignored; a key must never
    reach the repository. An existing environment variable always wins, so
    exporting a key for one run overrides the file without editing it.
    """
    path = config.REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def cache_key(payload):
    """Stable 16-hex-character key for a call.

    Provider and model are part of the payload so that an Anthropic cache and a
    Gemini cache cannot overwrite each other's answers to the same question.
    """
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:config.CACHE_KEY_LENGTH]


def read_cache(cache_dir, key):
    path = cache_dir / f"{key}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def write_cache(cache_dir, key, record):
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{key}.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def call_llm(system_prompt, user_prompt, model, max_tokens):
    """Send one prompt and return (text, input_tokens, output_tokens)."""
    load_env_file()

    last_error = None
    for attempt in range(config.LLM_TRANSPORT_RETRIES + 1):
        try:
            return _call_gemini(system_prompt, user_prompt, model, max_tokens)
        except TRANSPORT_ERRORS as error:
            last_error = error
            if attempt < config.LLM_TRANSPORT_RETRIES:
                time.sleep(config.LLM_TRANSPORT_BACKOFF_S * (2 ** attempt))

    raise RuntimeError(
        f"transport failed {config.LLM_TRANSPORT_RETRIES + 1} times in a row: "
        f"{type(last_error).__name__}: {last_error}"
    ) from last_error


def _call_gemini(system_prompt, user_prompt, model, max_tokens):
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(
            timeout=config.LLM_REQUEST_TIMEOUT_S * 1000,
            retry_options=types.HttpRetryOptions(
                attempts=config.LLM_TRANSPORT_RETRIES + 1),
        ),
    )
    response = client.models.generate_content(
        model=model,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=config.LLM_TEMPERATURE,
            max_output_tokens=max_tokens,
        ),
    )
    usage = response.usage_metadata
    return response.text, usage.prompt_token_count, usage.candidates_token_count


def strip_code_fences(text):
    """Remove a markdown fence the model added despite being told not to.

    Recorded rather than silently tolerated: `extract.py` counts how often this
    fires, because a model that keeps fencing its output is a prompt problem.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped, False
    lines = stripped.splitlines()
    if lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines[1:]).strip(), True
