import pytest

from backend.app.conversation import classify_conversation


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("Hi!", "greeting"),
        ("Hello there.", "greeting"),
        ("Hello everyone!", "greeting"),
        ("Hey, what's up?", "small_talk"),
        ("Good evening!", "greeting"),
        ("Good morning!!!", "greeting"),
        ("How are you?", "small_talk"),
        ("How's it going?", "small_talk"),
        ("How is your day?", "small_talk"),
        ("How are you doing today?", "small_talk"),
        ("How has your day been?", "small_talk"),
        ("Bye!", "goodbye"),
        ("See you later.", "goodbye"),
        ("Talk to you later", "goodbye"),
        ("See ya!", "goodbye"),
        ("Thanks!", "gratitude"),
        ("Thank you very much.", "gratitude"),
        ("Thanks for your help!", "gratitude"),
        ("I appreciate it", "gratitude"),
        ("Please", "polite"),
        ("You're welcome.", "polite"),
        ("Sorry!", "polite"),
        ("Excuse me", "polite"),
        ("Who are you?", "identity"),
        ("What is FinPilot?", "identity"),
        ("What can you do?", "identity"),
        ("What does FinPilot do?", "identity"),
        ("How can you help?", "identity"),
        ("Okay.", "follow_up"),
        ("Alright!", "follow_up"),
        ("Got it.", "follow_up"),
        ("Cool", "follow_up"),
        ("Help", "help"),
        ("Show me examples", "help"),
        ("What can I ask?", "help"),
        ("I need help", "help"),
        ("Hi, can you help me?", "help"),
        ("What is the weather in Hyderabad?", "unsupported_general_knowledge"),
        ("What is the capital of France?", "unsupported_general_knowledge"),
        ("Tell me more", "clarification"),
        ("Could you explain this?", "clarification"),
        ("Apple", "clarification"),
    ],
)
def test_deterministic_conversation_intents(question: str, intent: str) -> None:
    result = classify_conversation(question)

    assert result is not None
    assert result.intent == intent


@pytest.mark.parametrize(
    "question",
    [
        "Hi, what is a SIP?",
        "Thanks, now explain tax slabs",
        "Hello, explain health insurance",
    ],
)
def test_finance_questions_are_not_swallowed_by_conversation_intents(question: str) -> None:
    from backend.app.router import has_deterministic_finance_terms

    assert has_deterministic_finance_terms(question)
