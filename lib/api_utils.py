"""Shared API call utilities with retry logic and exponential backoff."""

import time


# Retryable OpenAI error types (by class name, to avoid import issues)
_RETRYABLE_ERRORS = (
    "RateLimitError",
    "APITimeoutError",
    "APIConnectionError",
    "InternalServerError",
    "APIStatusError",
)


def call_llm_with_retry(client, model, messages, temperature=0,
                         max_retries=3, base_delay=2, on_retry=None):
    """Call an OpenAI-compatible chat completion with exponential backoff retry.

    Args:
        client: OpenAI client instance.
        model: Model name string.
        messages: List of message dicts (role + content).
        temperature: Sampling temperature.
        max_retries: Maximum number of retry attempts after the first failure.
        base_delay: Base delay in seconds for exponential backoff.
        on_retry: Optional callback(attempt, error, delay) called before each retry.

    Returns:
        The response message content string.

    Raises:
        The last exception if all retries are exhausted.
    """
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            last_error = e
            error_type = type(e).__name__

            # Only retry on transient errors
            is_retryable = any(error_type == t for t in _RETRYABLE_ERRORS)

            # Also retry on status code 429, 500, 502, 503, 504
            status_code = getattr(e, "status_code", None)
            if status_code in (429, 500, 502, 503, 504):
                is_retryable = True

            if not is_retryable or attempt >= max_retries:
                raise

            delay = base_delay * (2 ** attempt)
            # Cap delay at 30 seconds
            delay = min(delay, 30)

            if on_retry:
                on_retry(attempt + 1, e, delay)
            else:
                print(f"[API] {error_type}，{delay}s 后重试 ({attempt + 1}/{max_retries})...")

            time.sleep(delay)

    raise last_error
