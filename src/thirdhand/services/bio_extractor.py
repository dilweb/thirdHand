"""Bio extractor service - extracts user facts from conversations."""

from typing import Any

import structlog
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.thirdhand.services.llm import create_llm, safe_invoke

logger = structlog.get_logger(__name__)


class BioFacts(BaseModel):
    """Schema for extracted user facts."""

    identity: dict[str, str] = Field(
        default_factory=dict,
        description="Identity facts: name, occupation, company, location",
    )
    tech_stack: list[str] = Field(
        default_factory=list,
        description="Technologies, tools, languages mentioned",
    )
    interests: list[str] = Field(
        default_factory=list,
        description="Topics the user is interested in",
    )
    preferences: dict[str, str] = Field(
        default_factory=dict,
        description="Communication preferences, style, timezone",
    )
    patterns: dict[str, Any] = Field(
        default_factory=dict,
        description="Behavioral patterns: active hours, common tasks",
    )
    current_project: str = Field(
        default="",
        description="What the user is currently working on",
    )


BIO_EXTRACTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You are extracting facts about the user from their conversation.
Extract only IMPORTANT, LONG-TERM facts:
- Identity: name, occupation, company, location
- Tech stack: technologies, tools, languages
- Interests: topics they care about
- Preferences: communication style, language, timezone
- Patterns: work hours, recurring tasks, habits
- Current project: what they're working on

IGNORE:
- Temporary states ("I'm hungry", "I'm tired")
- One-off requests ("buy milk", "remind me tomorrow")
- Greetings, thanks, acknowledgments

Return empty fields if no new facts found. Be concise.""",
        ),
        (
            "human",
            """User: {user_message}
Assistant: {assistant_message}

Existing profile: {existing_profile}

Extract any NEW facts not already in the profile.""",
        ),
    ]
)


async def extract_bio_facts(
    user_message: str,
    assistant_message: str,
    existing_profile: dict[str, Any] | None = None,
) -> BioFacts | None:
    """Extract bio facts from a conversation exchange.

    Args:
        user_message: The user's message.
        assistant_message: The assistant's response.
        existing_profile: Current profile to avoid duplicating facts.

    Returns:
        Extracted facts, or None if extraction failed.
    """
    llm = create_llm(temperature=0.0)
    structured_llm = llm.with_structured_output(BioFacts)
    chain = BIO_EXTRACTION_PROMPT | structured_llm

    profile_str = str(existing_profile) if existing_profile else "empty"

    result = safe_invoke(
        chain,
        {
            "user_message": user_message[:500],
            "assistant_message": assistant_message[:500],
            "existing_profile": profile_str[:1000],
        },
        fallback=None,
    )

    if result is None:
        logger.debug("bio_extraction_failed", preview=user_message[:50])
        return None

    # Check if all fields are empty
    if (
        not result.identity
        and not result.tech_stack
        and not result.interests
        and not result.preferences
        and not result.patterns
        and not result.current_project
    ):
        return None

    logger.info(
        "bio_facts_extracted",
        identity=result.identity,
        stack=result.tech_stack[:3],
        interests=result.interests[:3],
    )

    return result


def merge_facts(
    existing: dict[str, Any],
    new_facts: BioFacts,
) -> dict[str, Any]:
    """Merge new facts into existing profile without overwriting.

    Args:
        existing: Current context_summary.
        new_facts: Newly extracted facts.

    Returns:
        Merged profile.
    """
    profile = dict(existing) if existing else {}

    # Merge identity (prefer newer non-empty values)
    if new_facts.identity:
        profile.setdefault("identity", {})
        for k, v in new_facts.identity.items():
            if v:
                profile["identity"][k] = v

    # Merge tech_stack (union, keep unique)
    if new_facts.tech_stack:
        existing_stack = set(profile.get("tech_stack", []))
        profile["tech_stack"] = sorted(existing_stack | set(new_facts.tech_stack))

    # Merge interests (union with priority tracking)
    if new_facts.interests:
        existing_interests = {
            i["topic"] if isinstance(i, dict) else i
            for i in profile.get("interests", [])
        }
        for interest in new_facts.interests:
            if interest not in existing_interests:
                profile.setdefault("interests", [])
                profile["interests"].append(interest)
                existing_interests.add(interest)

    # Merge preferences (prefer newer non-empty values)
    if new_facts.preferences:
        profile.setdefault("preferences", {})
        for k, v in new_facts.preferences.items():
            if v:
                profile["preferences"][k] = v

    # Merge patterns
    if new_facts.patterns:
        profile.setdefault("patterns", {})
        for k, v in new_facts.patterns.items():
            if v:
                profile["patterns"][k] = v

    # Update current project if changed
    if new_facts.current_project:
        profile["current_project"] = new_facts.current_project

    return profile
