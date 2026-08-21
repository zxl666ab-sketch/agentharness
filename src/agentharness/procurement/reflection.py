"""LLM extraction with schema validation and self-correction reflection loop."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, TypeVar
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ExtractionMaxRetriesExceeded(ValueError):
    """Raised when LLM extraction fails to converge after maximum reflection retries."""


ParsingReflectionExhaustedError = ExtractionMaxRetriesExceeded


def extract_json_payload(text: str) -> str:
    """Extract markdown JSON code block or bare JSON object."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Remove triple backticks
        lines = stripped.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


async def extract_with_reflection(
    prompt: str,
    target_schema: type[T],
    llm_caller: Callable[[str], Any],
    max_retries: int = 2,
) -> T:
    """
    Execute structured LLM extraction with an error reflection loop.
    
    If the response fails JSON parsing or Pydantic validation:
    1. Extracts detailed field validation errors.
    2. Constructs a reflection prompt with the specific validation trace.
    3. Prompts the LLM to correct only the invalid fields.
    """
    current_prompt = prompt
    last_error_detail: str | None = None
    last_raw_response: str = ""

    for attempt in range(max_retries + 1):
        if attempt > 0:
            logger.info(
                "Self-correction reflection attempt %d/%d for schema %s",
                attempt,
                max_retries,
                target_schema.__name__,
            )
            reflection_prompt = (
                f"{current_prompt}\n\n"
                f"### Previous Attempt Output:\n"
                f"{last_raw_response}\n\n"
                f"### Validation Errors to Fix:\n"
                f"{last_error_detail}\n\n"
                f"Please fix the errors above and return ONLY the corrected valid JSON object."
            )
            raw_output = await llm_caller(reflection_prompt)
        else:
            raw_output = await llm_caller(current_prompt)

        raw_str = raw_output if isinstance(raw_output, str) else getattr(raw_output, "content", str(raw_output))
        last_raw_response = raw_str

        try:
            cleaned = extract_json_payload(raw_str)
            parsed_dict = json.loads(cleaned)
            validated = target_schema.model_validate(parsed_dict)
            return validated
        except json.JSONDecodeError as jde:
            last_error_detail = f"Invalid JSON syntax at line {jde.lineno}, col {jde.colno}: {jde.msg}"
        except ValidationError as ve:
            # Format Pydantic errors cleanly for LLM
            errors = [
                f"- Field '{'.'.join(str(loc) for loc in err['loc'])}': {err['msg']} (input was: {err.get('input', 'None')})"
                for err in ve.errors()
            ]
            last_error_detail = "\n".join(errors)
        except Exception as ex:  # Catch other potential normalization issues
            last_error_detail = f"Extraction error: {ex}"

    raise ExtractionMaxRetriesExceeded(
        f"Failed to extract valid {target_schema.__name__} after {max_retries} reflection attempts. "
        f"Last error: {last_error_detail}"
    )
