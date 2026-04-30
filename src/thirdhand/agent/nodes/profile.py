"""Profile update node - extract and update user interests."""

from src.thirdhand.agent.state import AgentState


def update_profile_node(state: AgentState) -> dict:
    """Update user profile with new interests/context.

    Args:
        state: Current agent state.

    Returns:
        Dictionary with confirmation message.
    """
    updates = state.profile_updates

    if not updates:
        return {
            "response_text": "💡 Спасибо за информацию! Я запомнил.",
            "response_type": "text",
        }

    topic = updates.get("topic", "")
    keywords = updates.get("keywords", [])

    response_parts = ["💡 Спасибо! Я запомнил:"]
    if topic:
        response_parts.append(f"  • Тема: {topic}")
    if keywords:
        response_parts.append(f"  • Ключевые слова: {', '.join(keywords)}")

    return {
        "response_text": "\n".join(response_parts),
        "response_type": "text",
        "profile_updates": updates,
    }
