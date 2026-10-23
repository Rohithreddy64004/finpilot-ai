"""Deterministic replies for basic social and ambiguous messages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


ConversationState = Literal["conversational", "clarification", "out_of_scope"]


@dataclass(frozen=True)
class ConversationReply:
    intent: str
    state: ConversationState
    answer: str


_GREETING_PREFIX = re.compile(
    r"^(?:hi|hello|hey|hiya|howdy|good morning|good afternoon|good evening)\b[\s,!.?-]*",
)
_POLITE_PREFIX = re.compile(r"^(?:please|excuse me)\b[\s,!.?-]*")
_GENERAL_KNOWLEDGE = re.compile(
    r"\b(?:weather|forecast|temperature|rain|rainy|snow|humidity|cricket|football|"
    r"basketball|world cup|recipe|cook(?:ing)?|capital of|solve this math|programming|"
    r"debug(?:ging)?|translate this)\b",
)
_AMBIGUOUS = re.compile(
    r"^(?:tell me more(?: about it)?|explain more|explain that|explain this|"
    r"what do you mean|what about(?: that| it)?|can you explain(?: this| that)?|"
    r"could you explain(?: this| that)?|which one|help me with that|"
    r"i do not understand|i don't understand|not sure|what now|what do you think)$",
)


def _normalize(text: str) -> str:
    value = text.casefold().replace("’", "'").strip()
    for contraction, expansion in (
        ("what's", "what is"),
        ("how's", "how is"),
        ("you're", "you are"),
        ("you've", "you have"),
        ("i'm", "i am"),
        ("can't", "cannot"),
        ("don't", "do not"),
    ):
        value = value.replace(contraction, expansion)
    value = re.sub(r"[^\w\s']", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _strip_social_prefixes(text: str) -> tuple[str, bool, bool]:
    remaining = text
    had_greeting = False
    had_polite = False
    while remaining:
        greeting = _GREETING_PREFIX.match(remaining)
        if greeting:
            remaining = remaining[greeting.end():].strip()
            had_greeting = True
            continue
        polite = _POLITE_PREFIX.match(remaining)
        if polite:
            remaining = remaining[polite.end():].strip()
            had_polite = True
            continue
        break
    return remaining, had_greeting, had_polite


def classify_conversation(question: str) -> ConversationReply | None:
    """Return a safe reply for a recognized social, out-of-scope, or ambiguous message."""

    text = _normalize(question)
    message, had_greeting, had_polite = _strip_social_prefixes(text)

    if re.fullmatch(r"(?:who are you|what is finpilot|what is finpilot ai|what does finpilot do|how does finpilot work|what can you do|how can you help|what do you do)", message):
        return ConversationReply(
            "identity",
            "conversational",
            "I'm FinPilot, a finance education assistant for Indian tax, investing, and insurance. I can help explain those topics using relevant sources.",
        )

    if re.fullmatch(r"(?:help|help me|i need help|can you help(?: me)?(?: with (?:this|something))?|show me examples?|give me examples?|what can i ask(?: you)?|what should i ask)", message):
        return ConversationReply(
            "help",
            "conversational",
            "I can help with Indian tax, investing, and insurance. You can ask about income tax slabs, what a SIP is, saving versus investing, or health insurance waiting periods.",
        )

    if re.fullmatch(r"(?:how are you(?: doing)?(?: today)?|how is it going|how is your day(?: going)?|how has your day been|how have you been|how is everything|how are things|what is up)", message):
        return ConversationReply(
            "small_talk",
            "conversational",
            "I'm doing well, thanks for asking! What would you like to know about Indian tax, investing, or insurance?",
        )

    if re.fullmatch(r"(?:thanks|thank you|thank you very much|thank you for (?:your )?(?:help|that)|thanks a lot|thanks so much|thanks for (?:your )?(?:help|explaining|that)|thx|much appreciated|appreciate it|i appreciate it|i really appreciate it|i appreciate your help)", message):
        return ConversationReply("gratitude", "conversational", "You're welcome! I'm happy to help.")

    if re.fullmatch(r"(?:bye|goodbye|see you|see ya|see you later|talk to you later|talk soon|catch you later|take care)", message):
        return ConversationReply(
            "goodbye",
            "conversational",
            "Goodbye! Come back whenever you have a question about Indian tax, investing, or insurance.",
        )

    if re.fullmatch(r"(?:you are welcome|no problem|no worries)", message):
        return ConversationReply("polite", "conversational", "Thanks! Let me know if you have a finance education question.")

    if re.fullmatch(r"(?:sorry|i am sorry|excuse me|please)", message):
        return ConversationReply("polite", "conversational", "No worries. What can I help you with?")

    if re.fullmatch(r"(?:okay|ok|alright(?: then)?|all right|got it|gotcha|cool|nice|understood|sounds good|sure)", message):
        return ConversationReply("follow_up", "conversational", "Great. What would you like to ask about tax, investing, or insurance?")

    if had_greeting and message in {"", "there", "everyone"}:
        return ConversationReply(
            "greeting",
            "conversational",
            "Hi! I can help with Indian tax, investing, and insurance. What would you like to know?",
        )

    if had_polite and not message:
        return ConversationReply("polite", "conversational", "Of course. What would you like to know?")

    if _GENERAL_KNOWLEDGE.search(text):
        return ConversationReply(
            "unsupported_general_knowledge",
            "out_of_scope",
            "I can help with Indian tax, investing, and insurance questions. Try asking about income tax slabs, the difference between saving and investing, or how health insurance waiting periods work.",
        )

    if _AMBIGUOUS.fullmatch(message) or len(message.split()) <= 2:
        return ConversationReply(
            "clarification",
            "clarification",
            "Could you clarify what you'd like to know? I can help with Indian tax, investing, and insurance questions.",
        )

    return None
